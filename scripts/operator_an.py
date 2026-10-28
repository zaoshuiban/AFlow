# -*- coding: utf-8 -*-
# @Date    : 6/27/2024 19:46 PM
# @Author  : didi
# @Desc    : action nodes for operator

from pydantic import BaseModel, Field


class GenerateOp(BaseModel):
    response: str = Field(default="", description="Your solution for this problem")


class CodeGenerateOp(BaseModel):
    code: str = Field(default="", description="Your complete code solution for this problem")


class AnswerGenerateOp(BaseModel):
    thought: str = Field(default="", description="The step by step thinking process")
    answer: str = Field(default="", description="The final answer to the question")


class FormatOp(BaseModel):
    solution: str = Field(default="", description="Your formatted answer for this problem")


class ScEnsembleOp(BaseModel):
    thought: str = Field(default="", description="The thought of the most consistent solution.")
    solution_letter: str = Field(default="", description="The letter of most consistent solution.")


class ReflectionTestOp(BaseModel):
    reflection_and_solution: str = Field(
        default="", description="Corrective solution for code execution errors or test case failures"
    )


class MdEnsembleOp(BaseModel):
    thought: str = Field(default="", description="Step-by-step analysis of the solutions to determine the best one.")
    solution_letter: str = Field(default="", description="The letter of the chosen best solution (only one letter).")


class ReviewOp(BaseModel):
    review_result: bool = Field(
        default=False,
        description="The Review Result (Bool). If you think this solution looks good for you, return 'true'; If not, return 'false'",
    )
    feedback: str = Field(
        default="",
        description="Your FeedBack for this problem based on the criteria. If the review result is true, you can put it 'nothing here'.",
    )


class ReviseOp(BaseModel):
    solution: str = Field(default="", description="Based on the feedback, revised solution for this problem")


# === Auto-generated operators for GSM8K experiment ===


class AnswerExtractOp(BaseModel):
    thought: str = Field(default="", description="Analysis of where the final answer is in the text")
    answer: str = Field(default="", description="The final numerical answer extracted from the solution")


class ReviewReviseOp(BaseModel):
    review_result: bool = Field(
        default=True,
        description="Whether the solution is correct. True if correct, False if incorrect.",
    )
    confidence: float = Field(
        default=1.0,
        description="Your confidence level (0.0 to 1.0) that the review_result is accurate. Only revise if confidence >= 0.8.",
    )
    feedback: str = Field(
        default="",
        description="If incorrect, explain the error. If correct, say 'The solution is correct.'",
    )
    revised_solution: str = Field(
        default="",
        description="If the original solution was incorrect AND your confidence >= 0.8, provide the corrected solution here. If correct or low confidence, leave empty.",
    )


# === MATH-specific operators ===


class AnswerFormatOp(BaseModel):
    thought: str = Field(
        default="",
        description="Analysis of what the correct answer format should be based on the problem type.",
    )
    formatted_answer: str = Field(
        default="",
        description="The answer reformatted with proper LaTeX notation, wrapped in \\boxed{}.",
    )


class StrategySelectOp(BaseModel):
    thought: str = Field(
        default="",
        description="Comparison of the different strategy solutions and analysis of which is most reliable.",
    )
    best_solution: str = Field(
        default="",
        description="The complete solution text from the most reliable strategy, with final answer in \\boxed{}.",
    )


class SymbolicVerifyOp(BaseModel):
    is_consistent: bool = Field(
        default=True,
        description="Whether the proposed answer is consistent with the problem constraints. True if consistent, False if inconsistent.",
    )
    verification_detail: str = Field(
        default="",
        description="Step-by-step verification of whether the answer satisfies the problem's constraints and conditions.",
    )
    corrected_answer: str = Field(
        default="",
        description="If the answer is inconsistent, provide the corrected answer in \\boxed{} notation. If consistent, leave empty.",
    )
