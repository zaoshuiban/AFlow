WORKFLOW_OPTIMIZE_PROMPT = """You are building a Graph and corresponding Prompt to jointly solve {type} problems. 
Referring to the given graph and prompt, which forms a basic example of a {type} solution approach, 
please reconstruct and optimize them. You can add, modify, or delete nodes, parameters, or prompts. Include your 
single modification in XML tags in your reply. Ensure they are complete and correct to avoid runtime failures. When 
optimizing, you can incorporate critical thinking methods like review, revise, ensemble (generating multiple answers through different/similar prompts, then voting/integrating/checking the majority to obtain a final answer), selfAsk, etc. Consider 
Python's loops (for, while, list comprehensions), conditional statements (if-elif-else, ternary operators), 
or machine learning techniques (e.g., linear regression, decision trees, neural networks, clustering). The graph 
complexity should not exceed 10. Use logical and control flow (IF-ELSE, loops) for a more enhanced graphical 
representation.Ensure that all the prompts required by the current graph from prompt_custom are included.Exclude any other prompts.
Hard structure constraints: any ScEnsemble vote must receive at most 5 candidate solutions total, and the graph must not contain 2 or more sequential verification/refinement steps after a candidate answer is produced.
Output the modified graph and all the necessary Prompts in prompt_custom (if needed).
The prompt you need to generate is only the one used in `prompt_custom.XXX` within Custom. Other methods already have built-in prompts and are prohibited from being generated. Only generate those needed for use in `prompt_custom`; please remove any unused prompts in prompt_custom.
The `<prompt>` content must be valid Python code. Every prompt must be assigned to a variable, e.g. `SOLVE_PROMPT = \"\"\"Solve...\"\"\"`. Do not output bare text lines such as `# SOLVE_PROMPT` followed by unquoted text, and do not use Markdown fences.
the generated prompt must not contain any placeholders.
Considering information loss, complex graphs may yield better results, but insufficient information transmission can omit the solution. It's crucial to include necessary context during the process."""


WORKFLOW_INPUT = """
Here is a graph and the corresponding prompt (prompt only related to the custom method) that performed excellently in a previous iteration (maximum score is 1). You must make further optimizations and improvements based on this graph. The modified graph must differ from the provided example, and the specific differences should be noted within the <modification>xxx</modification> section.\n
<sample>
    <experience>{experience}</experience>
    <modification>(such as:add /delete /modify/ ...)</modification>
    <score>{score}</score>
    <graph>{graph}</graph>
    <prompt>{prompt}</prompt>(only prompt_custom)
    <operator_description>{operator_description}</operator_description>
</sample>
Below are the logs of some results with the aforementioned Graph that performed well but encountered errors, which can be used as references for optimization:
{log}

First, provide optimization ideas. **Only one detail point can be modified at a time**, and no more than 5 lines of code may be changed per modification—extensive modifications are strictly prohibited to maintain project focus!
When introducing new functionalities in the graph, please make sure to import the necessary libraries or modules yourself, except for operator, prompt_custom, create_llm_instance, and CostManage, which have already been automatically imported.
**Under no circumstances should Graph output None for any field.**
Use custom methods to restrict your output format, rather than using code (outside of the code, the system will extract answers based on certain rules and score them).
It is very important to format the Graph output answers, you can refer to the standard answer format in the log.
You do not need to manually import prompt_custom or operator to use them; they are already included in the execution environment.
"""

WORKFLOW_CUSTOM_USE = """\nHere's an example of using the `custom` method in graph:
```
# You can write your own prompt in <prompt>prompt_custom</prompt> and then use it in the Custom method in the graph
response = await self.custom(input=problem, instruction=prompt_custom.XXX_PROMPT)
# You can also concatenate previously generated string results in the input to provide more comprehensive contextual information.
# response = await self.custom(input=problem+f"xxx:{xxx}, xxx:{xxx}", instruction=prompt_custom.XXX_PROMPT)
# The output from the Custom method can be placed anywhere you need it, as shown in the example below
solution = await self.generate(problem=f"question:{problem}, xxx:{response['response']}")
```
Note: In custom, the input and instruction are directly concatenated(instruction+input), and placeholders are not supported. Please ensure to add comments and handle the concatenation externally.\n

**Introducing multiple operators at appropriate points can enhance performance. If you find that some provided operators are not yet used in the graph, try incorporating them.**

**Only instantiate operators that are explicitly listed in `<operator_description>`.** Do not invent operator class names from examples or previous experiments. If a specialized operator such as a geometry or trigonometry solver is not listed there, route to an available general solver such as Custom or MultiStrategySolver instead.

**IMPORTANT: Never instantiate a library-managed auto-evolved specialist directly. Its candidate/experimental lifecycle and failure boundaries would be bypassed. Use `ContextAwareOperatorRouter` when it is listed; that router is the only authorized entry point and falls back safely when no revision-bound evidence exists.**

**REFERENCE GRAPH EXAMPLE (MATH):**
```
class Workflow:
    def __init__(self, name, llm_config, dataset):
        self.llm = create_llm_instance(llm_config)
        self.custom = operator.Custom(self.llm)
        self.sc_ensemble = operator.ScEnsemble(self.llm)
        self.problem_router = operator.ProblemRouter(self.llm)
        self.multi_strategy_solver = operator.MultiStrategySolver(self.llm)

    async def __call__(self, problem: str):
        # Step 1: Route problem to appropriate solver
        route = await self.problem_router(problem=problem)
        solutions = []
        # Step 2: Add one routed candidate using an available listed operator
        if route['category'] in ["geometry", "trigonometry", "algebra"]:
            routed_sol = await self.multi_strategy_solver(problem=problem)
            solutions.append(routed_sol['response'])
        # Step 3: Generate 3 standard solutions for ensemble
        for _ in range(3):
            sol = await self.custom(input=problem, instruction=prompt_custom.SOLVE_PROMPT)
            solutions.append(sol['response'])
        # Step 4: Self-consistency voting
        result = await self.sc_ensemble(solutions=solutions, problem=problem)
        return result['response'], self.llm.get_usage_summary()["total_cost"]
```
This routing pattern achieves the best results by ensuring specialized operators are ONLY invoked for problems they excel at, while Custom+ScEnsemble handles all other cases reliably.

**CRITICAL CONSTRAINTS:**
1. **ALL operators return dict with key 'response'** (except Programmer which returns 'code' and 'output', and ProblemRouter which returns 'category'). When consuming an operator's output, ALWAYS use `result['response']`. NEVER use keys like 'final_answer', 'answer', 'solution', or 'result' — these do NOT exist in the return dict. Example: `sol = await self.my_op(problem=problem); solutions.append(sol['response'])`.
2. **Every solving path in the graph must have redundancy.** If any operator (e.g., Programmer) can fail or produce errors, the graph must still produce a valid answer through alternative paths. A safe pattern is: use multiple Custom calls for text reasoning, optionally add Programmer as one additional candidate, then ensemble all candidates with ScEnsemble for self-consistency voting.
3. **HARD LIMIT: ScEnsemble voting width <= 5.** The `solutions` list passed into any ScEnsemble call must contain at most 5 candidate answers total, including Custom, Programmer, and contextual-router outputs. Do not generate 6-way, 7-way, or larger votes; graph bloat reduces consensus quality.
4. **HARD LIMIT: no >=2-step chain verification.** After a candidate answer is produced, you may use at most one verification/refinement/formatting step before returning or ensembling. Forbidden patterns include `Verifier -> Formatter`, `Formatter -> Verifier`, `Verifier -> Custom review`, `Verifier -> Formatter -> Verifier`, or any two-or-more sequential check/revise/format/verify calls. If verification is useful, use exactly one such step or put candidates directly into ScEnsemble.
5. **Proven winning patterns for MATH**: (a) Custom with ScEnsemble (multi-solution self-consistency voting) — this alone achieves ~0.77, (b) optional single answer formatting as the final step only when it is not chained after another verifier/refiner, (c) adding Programmer or a specialized reasoning operator to diversify ensemble candidates while keeping total vote width <= 5.
6. **🧭 ROUTING PATTERN (highly recommended)**: Use ProblemRouter to classify the problem FIRST, then route to specialized solvers based on category. This avoids wasting specialized operators on mismatched problems. Pattern:
```python
route = await self.problem_router(problem=problem)
category = route['category']
if category in ["geometry", "trigonometry", "algebra"]:
    # Use an available listed solver; never instantiate a managed evolved class directly
    routed_sol = await self.multi_strategy_solver(problem=problem)
    solutions.append(routed_sol['response'])
else:
    # Default: pure Custom voting
    pass
# Generate 3 Custom solutions regardless
for _ in range(3):
    sol = await self.custom(input=problem, instruction=prompt_custom.SOLVE_PROMPT)
    solutions.append(sol['response'])
result = await self.sc_ensemble(solutions=solutions, problem=problem)
```
This ensures specialized operators are ONLY called when the problem matches their expertise, preventing noise in the ensemble vote.
"""

WORKFLOW_TEMPLATE = """from typing import Literal
import workspace.{dataset}.workflows.template.operator as operator
import workspace.{dataset}.workflows.round_{round}.prompt as prompt_custom
from scripts.async_llm import create_llm_instance


from scripts.evaluator import DatasetType

{graph}
"""
