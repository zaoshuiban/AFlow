# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field


class GenerateOp(BaseModel):
    response: str = Field(default="", description="Your solution for this problem")


class CodeGenerateOp(BaseModel):
    code: str = Field(default="", description="Your complete code solution for this problem")


class ScEnsembleOp(BaseModel):
    solution_letter: str = Field(default="", description="The letter of most consistent solution.")


class ProblemClassifyOp(BaseModel):
    thought: str = Field(default="", description="Brief analysis of keywords and math concepts in the problem that indicate its category.")
    category: str = Field(default="algebra", description="One of: geometry, algebra, number_theory, combinatorics, probability, trigonometry")


class AnswerFormatOp(BaseModel):
    thought: str = Field(default="", description="Analysis of what the correct answer format should be based on the problem type.")
    formatted_answer: str = Field(default="", description="The answer reformatted with proper LaTeX notation, wrapped in \\boxed{{}}.")


class StrategySelectOp(BaseModel):
    thought: str = Field(default="", description="Comparison of the different strategy solutions and analysis of which is most reliable.")
    best_solution: str = Field(default="", description="The complete solution text from the most reliable strategy, with final answer in \\boxed{{}}.")


class SymbolicVerifyOp(BaseModel):
    is_consistent: bool = Field(default=True, description="Whether the proposed answer is consistent with the problem constraints.")
    verification_detail: str = Field(default="", description="Step-by-step verification of whether the answer satisfies the problem constraints.")
    corrected_answer: str = Field(default="", description="If the answer is inconsistent, provide the corrected answer in \\boxed{{}} notation.")
