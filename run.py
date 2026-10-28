# -*- coding: utf-8 -*-
# @Date    : 8/23/2024 20:00 PM
# @Author  : didi
# @Desc    : Entrance of AFlow.

import argparse
import os
from typing import Dict, List

from data.download_data import download
from scripts.optimizer import Optimizer
from scripts.async_llm import LLMsConfig
import multiprocessing
multiprocessing.set_start_method("fork", force=True)

class ExperimentConfig:
    def __init__(self, dataset: str, question_type: str, operators: List[str]):
        self.dataset = dataset
        self.question_type = question_type
        self.operators = operators


EXPERIMENT_CONFIGS: Dict[str, ExperimentConfig] = {
    "DROP": ExperimentConfig(
        dataset="DROP",
        question_type="qa",
        operators=["Custom", "AnswerGenerate", "ScEnsemble"],
    ),
    "HotpotQA": ExperimentConfig(
        dataset="HotpotQA",
        question_type="qa",
        operators=["Custom", "AnswerGenerate", "ScEnsemble"],
    ),
    "MATH": ExperimentConfig(
        dataset="MATH",
        question_type="math",
        operators=[
            "Custom",
            "ScEnsemble",
            "Programmer",
            "ProblemRouter",
        ],
    ),
    "GSM8K": ExperimentConfig(
        dataset="GSM8K",
        question_type="math",
        operators=["Custom", "ScEnsemble", "Programmer", "AnswerExtractor", "ReviewRevise", "MultiPathReasoner", "CodeVerifyRescue"],
    ),
    "MBPP": ExperimentConfig(
        dataset="MBPP",
        question_type="code",
        operators=["Custom", "CustomCodeGenerate", "ScEnsemble", "Test"],
    ),
    "HumanEval": ExperimentConfig(
        dataset="HumanEval",
        question_type="code",
        operators=["Custom", "CustomCodeGenerate", "ScEnsemble", "Test"],
    ),
    "LiveCodeBench": ExperimentConfig(
        dataset="LiveCodeBench",
        question_type="code",
        operators=["Custom", "CustomCodeGenerate", "ScEnsemble", "Test"],
    ),
}


def parse_args():
    parser = argparse.ArgumentParser(description="AFlow Optimizer")
    parser.add_argument(
        "--dataset",
        type=str,
        choices=list(EXPERIMENT_CONFIGS.keys()),
        required=True,
        help="Dataset type",
    )
    parser.add_argument("--sample", type=int, default=4, help="Sample count")
    parser.add_argument(
        "--optimized_path",
        type=str,
        default="workspace",
        help="Optimized result save path",
    )
    parser.add_argument("--initial_round", type=int, default=1, help="Initial round")
    parser.add_argument("--max_rounds", type=int, default=20, help="Max iteration rounds")
    parser.add_argument("--check_convergence", type=bool, default=True, help="Whether to enable early stop")
    parser.add_argument("--validation_rounds", type=int, default=1, help="Validation rounds")
    parser.add_argument(
        "--if_force_download",
        type=lambda x: x.lower() == "true",
        default=False,
        help="Whether enforce dataset download.",
    )
    parser.add_argument(
        "--opt_model_name",
        type=str,
        default="deepseek-v4-pro",
        help="Specifies the name of the model used for optimization tasks.",
    )
    parser.add_argument(
        "--exec_model_name",
        type=str,
        default="deepseek-v4-flash",
        help="Specifies the name of the model used for execution tasks.",
    )
    parser.add_argument(
        "--enable_operator_library",
        type=lambda x: x.lower() == "true",
        default=False,
        help="Expose the evidence-aware operator router (off by default to preserve the AFlow baseline).",
    )
    parser.add_argument(
        "--enable_operator_evolution",
        type=lambda x: x.lower() == "true",
        default=False,
        help="Enable operator evolution when stagnation is detected.",
    )
    parser.add_argument(
        "--enable_bottleneck_detection",
        type=lambda x: x.lower() == "true",
        default=False,
        help="Enable bottleneck detection for triggering operator evolution.",
    )
    parser.add_argument(
        "--enable_code_validation",
        type=lambda x: x.lower() == "true",
        default=False,
        help="Enable AST validation for LLM-generated graphs.",
    )
    parser.add_argument(
        "--enable_smoke_test",
        type=lambda x: x.lower() == "true",
        default=False,
        help="Enable smoke test for LLM-generated workflows.",
    )
    parser.add_argument(
        "--enable_prompt_guidance",
        type=lambda x: x.lower() == "true",
        default=True,
        help="Enable anti-patterns and positive examples in evolution prompt (set false for ablation).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    config = EXPERIMENT_CONFIGS[args.dataset]

    # Restore persistent, evidence-backed operators before graph optimization.
    # Candidate/rejected artifacts remain archived but are never installed.
    if config.dataset == "MATH" and args.enable_operator_library:
        from scripts.operator_library import OperatorLibrary

        library_path = os.path.join(
            args.optimized_path, config.dataset, "operator_library", "operators.json"
        )
        template_dir = os.path.join(
            args.optimized_path, config.dataset, "workflows", "template"
        )
        if os.path.exists(library_path):
            OperatorLibrary(library_path, auto_create=False).install_routable_artifacts(
                template_dir
            )
        if "ContextAwareOperatorRouter" not in config.operators:
            config.operators.append("ContextAwareOperatorRouter")

    # 每次启动新实验时清空历史 results，避免跨实验分数污染导致收敛误判
    import json, os
    results_file = os.path.join(args.optimized_path, config.dataset, "workflows", "results.json")
    if os.path.exists(results_file):
        with open(results_file, "w") as f:
            json.dump([], f)
        print(f"🔄 已清空历史 results.json: {results_file}")

    models_config = LLMsConfig.default()
    opt_llm_config = models_config.get(args.opt_model_name)
    if opt_llm_config is None:
        raise ValueError(
            f"The optimization model '{args.opt_model_name}' was not found in the 'models' section of the configuration file. "
            "Please add it to the configuration file or specify a valid model using the --opt_model_name flag. "
        )

    exec_llm_config = models_config.get(args.exec_model_name)
    if exec_llm_config is None:
        raise ValueError(
            f"The execution model '{args.exec_model_name}' was not found in the 'models' section of the configuration file. "
            "Please add it to the configuration file or specify a valid model using the --exec_model_name flag. "
        )

    download(["datasets"], force_download=args.if_force_download) # remove download initial_rounds in new version.

    optimizer = Optimizer(
        dataset=config.dataset,
        question_type=config.question_type,
        opt_llm_config=opt_llm_config,
        exec_llm_config=exec_llm_config,
        check_convergence=args.check_convergence,
        operators=config.operators,
        optimized_path=args.optimized_path,
        sample=args.sample,
        initial_round=args.initial_round,
        max_rounds=args.max_rounds,
        validation_rounds=args.validation_rounds,
        enable_code_validation=(
            args.enable_code_validation
            or (
                config.dataset == "MATH"
                and (args.enable_operator_library or args.enable_operator_evolution)
            )
        ),
        enable_bottleneck_detection=args.enable_bottleneck_detection,
        enable_smoke_test=args.enable_smoke_test,
        enable_operator_evolution=args.enable_operator_evolution,
        enable_prompt_guidance=args.enable_prompt_guidance,
    )

    # Optimize workflow via setting the optimizer's mode to 'Graph'
    optimizer.optimize("Graph")

    # Test workflow via setting the optimizer's mode to 'Test'
    # optimizer.optimize("Test")
