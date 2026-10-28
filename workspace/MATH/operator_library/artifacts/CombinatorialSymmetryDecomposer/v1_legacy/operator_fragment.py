# Historical executable fragment used by expB_evolution_v7 and the direct CSVs.
class CombinatorialSymmetryDecomposer(Operator):
    def __init__(self, llm: AsyncLLM, name: str = "CombinatorialSymmetryDecomposer"):
        super().__init__(llm, name)
        self.custom = Custom(llm, "Custom")

    async def __call__(self, problem: str) -> dict:
        analysis = await self.custom(input=problem, instruction=SYMMETRY_ANALYSIS_PROMPT)
        if analysis.get("status") == "error":
            return {"status": "error", "response": "", "observation": f"Stage 1 failed: {analysis.get('observation', '')}"}
        combined_input = f"Problem: {problem}\n\nAnalysis:\n{analysis['response']}"
        result = await self.custom(input=combined_input, instruction=SYMMETRY_SOLVE_PROMPT)
        if result.get("status") == "error":
            return {"status": "error", "response": "", "observation": f"Stage 2 failed: {result.get('observation', '')}"}
        return {"status": "success", "response": result["response"], "observation": ""}
