import unittest

from agent_service.specialists import route_specialist, run_specialist


class RoutingTests(unittest.TestCase):
    def test_routes_arithmetic_to_math(self):
        self.assertEqual(route_specialist("Calculate 24 * 7"), "math-specialist")

    def test_routes_general_question_to_knowledge(self):
        self.assertEqual(route_specialist("What is our leave policy?"), "knowledge-specialist")

    def test_math_specialist_calculates(self):
        result = run_specialist("math-specialist", "Calculate 24 * 7")
        self.assertIn("168", result.answer)

    def test_knowledge_specialist_returns_mock_grounding(self):
        result = run_specialist("knowledge-specialist", "What is our leave policy?")
        self.assertIn("mock knowledge-base lookup", result.answer)


if __name__ == "__main__":
    unittest.main()

