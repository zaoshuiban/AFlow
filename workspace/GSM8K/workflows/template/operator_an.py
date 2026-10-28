# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field

class GenerateOp(BaseModel):
    response: str = Field(default="", description="Your solution for this problem")

class CodeGenerateOp(BaseModel):
    code: str = Field(default="", description="Your complete code solution for this problem")

class ScEnsembleOp(BaseModel):
    solution_letter: str = Field(default="", description="The letter of most consistent solution.")

class AnswerExtractOp(BaseModel):
    thought: str = Field(default="", description="Analysis of where the final answer is in the text")
    answer: str = Field(default="", description="The final numerical answer extracted from the solution")

class ReviewReviseOp(BaseModel):
    review_result: bool = Field(default=True, description="Whether the solution is correct.")
    feedback: str = Field(default="", description="If incorrect, explain the error.")
    revised_solution: str = Field(default="", description="Corrected solution if original was wrong.")
