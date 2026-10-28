SC_ENSEMBLE_PROMPT = """
Given the question described as follows: {problem}
Several solutions have been generated to address the given question. They are as follows:
{solutions}

Carefully evaluate these solutions and identify the answer that appears most frequently across them. This consistency in answers is crucial for determining the most reliable solution.

In the "thought" field, provide a detailed explanation of your thought process. In the "solution_letter" field, output only the single letter ID (A, B, C, etc.) corresponding to the most consistent solution. Do not include any additional text or explanation in the "solution_letter" field.
"""

PYTHON_CODE_VERIFIER_PROMPT = """
You are a professional Python programmer. Your task is to write complete, self-contained code based on a given mathematical problem and output the answer. The code should include all necessary imports and dependencies, and be ready to run without additional setup or environment configuration.

Problem description: {problem}
Other analysis: {analysis}
{feedback}

Your code should:
1. Implement the calculation steps described in the problem.
2. Define a function named `solve` that performs the calculation and returns the result. The `solve` function should not require any input parameters; instead, it should obtain all necessary inputs from within the function or from globally defined variables.
3. `solve` function return the final calculation result.

Please ensure your code is efficient, well-commented, and follows Python best practices. The output should be limited to basic data types such as strings, integers, and floats. It is prohibited to transmit images or other file formats. The code output is intended for a text-based language model.
"""

VALID_CATEGORIES = {"geometry", "algebra", "number_theory", "combinatorics", "probability", "trigonometry"}

PROBLEM_CLASSIFY_PROMPT = """
Classify the following math problem into exactly ONE of these categories based on its dominant mathematical topic:

- geometry: problems about shapes, angles, triangles, circles, areas, volumes, coordinate geometry, vectors, transformations, reflections, projections
- algebra: problems about equations, inequalities, functions, polynomials, systems of equations, sequences, series (non-trig), logarithms
- number_theory: problems about divisibility, primes, modular arithmetic, GCD/LCM, digit problems, Diophantine equations
- combinatorics: problems about counting, permutations, combinations, binomial coefficients, inclusion-exclusion
- probability: problems about probability, expected value, random variables
- trigonometry: problems specifically about trigonometric identities, trigonometric equations, sine/cosine sums or products

Problem: {problem}

Analyze the key mathematical concepts and keywords, then classify.
"""

ANSWER_FORMAT_PROMPT = """
You are a mathematical answer formatting assistant. Your task is to extract the final answer from a solution and format it correctly in LaTeX notation.

Problem: {problem}
Solution: {solution}

Formatting rules:
1. The answer MUST be wrapped in \\boxed{{}} notation.
2. For percentages: use format like \\boxed{{28\\%}} (with \\% not %).
3. For units (area, length, etc.): include the unit in \\text{{}}, e.g., \\boxed{{100\\text{{ square units}}}}.
4. For angles in degrees: use \\boxed{{120^\\circ}}.
5. For fractions: use LaTeX format \\boxed{{\\frac{{a}}{{b}}}} unless the fraction simplifies to an integer.
6. For square roots: use \\boxed{{\\sqrt{{n}}}} or \\boxed{{a\\sqrt{{b}}}}.
7. For mixed numbers: convert to improper fractions, e.g., 2⅓ → \\boxed{{\\frac{{7}}{{3}}}}.
8. For simple integers/decimals: just \\boxed{{42}} or \\boxed{{3.14}}.
9. If the answer is a list or set, use comma separation inside \\boxed{{}}.
10. Do NOT include any explanation or extra text inside \\boxed{{}}.

Analyze what format the answer should be in, then provide the formatted answer.
"""

STRATEGY_SELECT_PROMPT = """
You are evaluating multiple solutions to the same math problem, each generated using a different strategy.

Problem: {problem}

Strategy solutions:
{solutions}

Your task:
1. Compare the final answers from each strategy.
2. Check which solutions have correct reasoning and consistent final answers.
3. If multiple strategies agree on the same answer, that answer is more likely correct.
4. If strategies disagree, carefully verify which reasoning is correct.
5. Select the most reliable solution.

In the "thought" field, provide your comparison and analysis.
In the "best_solution" field, provide the COMPLETE solution text from the most reliable strategy (including all reasoning steps), with the final answer in \\boxed{{}}.
"""

SYMBOLIC_VERIFY_PROMPT = """
You are a mathematical verification assistant. Your task is to verify whether a proposed answer to a math problem is consistent with the problem's constraints and conditions.

Problem: {problem}
Proposed answer: {answer}

Verification steps:
1. Identify the key constraints and conditions in the problem.
2. Substitute the proposed answer back into the original problem conditions.
3. Check if ALL conditions are satisfied.
4. For algebraic problems: verify by substitution.
5. For geometric problems: verify the answer satisfies the given geometric properties.
6. For counting problems: verify by enumeration or logical reasoning.
7. For word problems: verify the answer makes sense in the real-world context.

If the answer is consistent with all constraints, set is_consistent to True.
If the answer is inconsistent, set is_consistent to False, explain the inconsistency, and provide the corrected answer in \\boxed{{}} notation.
"""

# ========== Auto-evolved operator: GeometricCombinatorialDecomposer ==========

DECOMPOSITION_ANALYSIS_PROMPT = """
You are a math problem decomposition specialist. Analyze the given problem step by step:

1. **Identify the problem type**: Is it geometric (angles, shapes, coordinates), combinatorial (counting, arrangements, probability), or algebraic/reasoning?

2. **For geometric problems**:
   - List all given constraints (congruent angles, parallel lines, etc.)
   - Identify key invariants (sum of interior angles, triangle properties, symmetry)
   - Express relationships as equations

3. **For combinatorial problems**:
   - Identify the objects being arranged/selected
   - Determine if order matters (permutations vs combinations)
   - Account for symmetries: rotations, reflections, identical objects
   - Use complementary counting or casework if needed

4. **For reasoning problems**:
   - Identify the key transformation or operation
   - Look for patterns, periodicities, or invariants
   - Consider working backwards from the desired result

5. **Write down a clear plan**: List the steps needed to solve, including any formulas or theorems to apply.

Do NOT solve the problem yet. Only provide the analysis and decomposition plan. Put your final analysis summary in \boxed{{}}.
"""


STRUCTURED_SOLVE_PROMPT = """
You are a precise math solver. Using the analysis provided above, solve the problem step by step.

Follow the decomposition plan carefully:
- For each step, show your work clearly
- Double-check arithmetic and algebraic manipulations
- For geometric problems: verify angle sums, use correct formulas
- For combinatorial problems: explicitly list cases, verify symmetry division
- For reasoning problems: check each transformation, verify periodicities

After solving, verify your answer by:
1. Substituting back into the original problem conditions
2. Checking edge cases or boundary conditions
3. Ensuring the answer is in simplest form

Put your final answer in \boxed{{}}.
"""

# ========== Auto-evolved operator: CombinatorialSymmetryDecomposer ==========

# ========== Restored revision-bound library prompts ==========

SYMMETRY_ANALYSIS_PROMPT = r"""
You are a combinatorial symmetry analyst. Analyze the following problem and identify:
(1) the finite set of objects and positions, (2) the symmetry group acting on
the positions, (3) the group elements, and (4) the arrangements fixed by each
operation. Do not apply circular-seating quotient formulas in this operator.
Put your final analysis summary in \boxed{{}}.
"""

SYMMETRY_SOLVE_PROMPT = r"""
Using the supplied finite symmetry analysis, solve the orbit-counting problem.
Apply Burnside's Lemma once: average the number of fixed arrangements over the
group elements. Check that rotations/reflections have not already been
quotiented by a circular-arrangement formula. Show all steps clearly and put
the final answer in \boxed{{}}.
"""
