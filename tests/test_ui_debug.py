import unittest

from ui_debug import agent_tree_markdown, final_response_markdown, grouped_timeline, metrics_markdown


class DebugFormattingTests(unittest.TestCase):
    def test_groups_consecutive_text_chunks(self):
        timeline = grouped_timeline(
            [
                {"elapsed": 0.0, "event": {"type": "RUN_STARTED"}},
                {"elapsed": 0.2, "event": {"type": "TEXT_MESSAGE_CONTENT"}},
                {"elapsed": 0.3, "event": {"type": "TEXT_MESSAGE_CONTENT"}},
                {"elapsed": 0.4, "event": {"type": "RUN_FINISHED"}},
            ]
        )
        self.assertIn("Answer text chunk × 2", timeline)
        self.assertIn("Run finished", timeline)

    def test_formats_metrics_as_table(self):
        output = metrics_markdown(
            {
                "standardTokenUsage": [
                    {"provider": "mock", "model": "poc", "inputTokens": 4, "outputTokens": 8, "totalTokens": 12}
                ],
                "cost": {"estimatedCostUsd": 0.00002},
            }
        )
        self.assertIn("| Model | poc |", output)
        self.assertIn("$0.000020", output)

    def test_formats_final_response_without_metrics(self):
        output = final_response_markdown({"finalResponse": "Complete answer"}, 5)
        self.assertIn("5 text chunks combined", output)
        self.assertIn("Complete answer", output)

    def test_agent_tree_supports_multiple_named_agents(self):
        output = agent_tree_markdown(
            "running",
            {
                "run-1": {
                    "name": "knowledge-specialist",
                    "status": "complete",
                    "duration": 1.2,
                    "subagentRunId": "run-1",
                    "metadata": {"agentId": "kb-agent"},
                    "tools": ["search_managed_kb"],
                },
                "run-2": {
                    "name": "compliance-specialist",
                    "status": "running",
                    "subagentRunId": "run-2",
                    "metadata": {"agentId": "compliance-agent"},
                    "tools": [],
                },
            },
        )
        self.assertIn("Knowledge Specialist", output)
        self.assertIn("Compliance Specialist", output)
        self.assertIn("search_managed_kb", output)


if __name__ == "__main__":
    unittest.main()
