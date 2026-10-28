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
