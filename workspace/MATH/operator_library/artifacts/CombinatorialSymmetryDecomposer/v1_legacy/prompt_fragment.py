# Historical source preserved exactly in behavior, including Python's original
# interpretation of ``\b`` in the non-raw ``\boxed`` string literals.
SYMMETRY_ANALYSIS_PROMPT = """
You are a combinatorial symmetry analyst. Analyze the following problem and identify: (1) The set of objects being arranged or counted, (2) The symmetry group acting on these objects (rotations, reflections, permutations, etc.), (3) The number of elements in the symmetry group, (4) For each type of symmetry operation, determine how many arrangements are fixed by that operation. If the problem involves probability with continuous variables (like arrival times), instead identify the sample space as a geometric region and determine the region where the event occurs. Output your analysis clearly. Put your final answer in \boxed{{}}.
"""


SYMMETRY_SOLVE_PROMPT = """
Using the symmetry analysis provided above, solve the combinatorial problem. If the problem involves a finite symmetry group, apply Burnside's Lemma: count the number of distinct arrangements by averaging the number of fixed arrangements over all group elements. If the problem involves continuous variables (arrival times, distances), use geometric probability: set up coordinates, define the favorable region, compute its area/volume, and divide by the total area/volume. Show all steps clearly. Put your final answer in \boxed{{}}.
"""
