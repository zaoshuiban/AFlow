import copy
import unittest

from scripts.diagnostic_constraint_checks import check_analysis


class ConstraintChecksTests(unittest.TestCase):
    def circle(self, value='26'):
        return {'problem':r'The radius of the circle is $\sqrt{50}$ cm. Find the square of the distance from $B$ to the center.',
                'reference_solution':rf'The distance squared is \boxed{{{value}}}.'}

    def test_circle_inside_candidate_cannot_be_reference_outside(self):
        a={'violated_constraint':'The configuration selects the root with B outside the circle, giving OB²=26.'}
        result=check_analysis(self.circle(),a)
        self.assertEqual(result['status'],'contradiction')
        self.assertIn('inside',result['checks'][0]['basis'])

    def test_valid_outside_and_inside_claims(self):
        for value,word in [('26','inside'),('74','outside')]:
            a={'violated_constraint':f'The configuration selects the root with B {word} the circle.'}
            result=check_analysis(self.circle(value),a)
            self.assertEqual(result['status'],'limited_checks_consistent')
            self.assertFalse(result['semantic_verified'])

    def test_solver_error_description_is_not_diagnostic_assertion(self):
        a={'explanation':'The solver wrongly believes B is outside the circle and chooses 74.'}
        result=check_analysis(self.circle(),a)
        self.assertEqual(result['status'],'needs_review')
        self.assertFalse(any(c['status']=='contradiction' for c in result['checks']))

    def test_circle_counterexample_rejecting_inside_candidate(self):
        a={'counterexample':'If the diagram showed B inside the circle, then the root with OB²=26 would be the one to reject.'}
        self.assertEqual(check_analysis(self.circle(),a)['status'],'contradiction')

    def test_unknown_radius_is_not_guessed(self):
        case=self.circle();case['problem']=case['problem'].replace('50','k')
        self.assertEqual(check_analysis(case,{'explanation':'The reference has B outside.'})['status'],'not_covered')

    def test_target_rays_detected_in_both_orders(self):
        case={'problem':'Find x.', 'reference_solution':r'\angle AEB is a straight angle. Target x=\boxed{162}.'}
        for rays in ('EA and EB','EB and EA'):
            result=check_analysis(case,{'violated_constraint':f'x is the angle at E between {rays}.'})
            self.assertEqual(result['status'],'contradiction')

    def test_correct_rays_do_not_trigger_opposite_ray_rule(self):
        case={'problem':'Find x.', 'reference_solution':r'\angle AEB is a straight angle. Target x=\boxed{162}.'}
        result=check_analysis(case,{'violated_constraint':'x is the angle between ED and EB.'})
        self.assertEqual(result['status'],'not_covered')

    def segment(self):
        return {'problem':'Find the length of $PQ$.',
                'reference_solution':r'\triangle ABC \sim \triangle XYZ. \triangle ABD \sim \triangle UVW.'}

    def test_reciprocal_sum_is_not_harmonic_mean(self):
        a={'violated_constraint':'PQ equals harmonic mean 1/(1/AB+1/CD).'}
        self.assertEqual(check_analysis(self.segment(),a)['status'],'contradiction')

    def test_segment_scope_needs_endpoint(self):
        a={'applicability':'A segment through the intersection of diagonals parallel to the bases.'}
        self.assertEqual(check_analysis(self.segment(),a)['status'],'needs_review')
        a['applicability']='The segment from the intersection of diagonals to one leg, parallel to bases.'
        self.assertEqual(check_analysis(self.segment(),a)['status'],'not_covered')

    def test_out_of_scope_formulas_are_left_for_review(self):
        case={'problem':'Find the harmonic mean.', 'reference_solution':'Use a formula.'}
        self.assertEqual(check_analysis(case,{'explanation':'Use harmonic mean 1/(1/a+1/b).'})['status'],'not_covered')


if __name__=='__main__': unittest.main()
