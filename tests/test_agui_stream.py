import json
import unittest

from fastapi.testclient import TestClient

from agent_service.main import app


class AguiStreamTests(unittest.TestCase):
    def test_stream_contains_tool_lifecycle_and_metrics(self):
        payload = {
            "threadId": "thread-test",
            "runId": "run-test",
            "messages": [{"id": "m1", "role": "user", "content": "Calculate 24 * 7"}],
            "state": {},
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
        with TestClient(app) as client:
            with client.stream(
                "POST", "/agent", json=payload, headers={"Accept": "text/event-stream"}
            ) as response:
                response.raise_for_status()
                events = [
                    json.loads(line.removeprefix("data:").strip())
                    for line in response.iter_lines()
                    if line.startswith("data:")
                ]

        event_types = [event["type"] for event in events]
        expected_tool_order = [
            "TOOL_CALL_START",
            "TOOL_CALL_ARGS",
            "TOOL_CALL_END",
            "TOOL_CALL_RESULT",
        ]
        positions = [event_types.index(event_type) for event_type in expected_tool_order]
        self.assertEqual(positions, sorted(positions))

        args = "".join(event["delta"] for event in events if event["type"] == "TOOL_CALL_ARGS")
        self.assertEqual(json.loads(args), {"expressionFromQuestion": "Calculate 24 * 7"})

        finished = events[-1]
        self.assertEqual(finished["type"], "RUN_FINISHED")
        streamed_text = "".join(
            event["delta"] for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"
        )
        self.assertEqual(finished["result"]["finalResponse"], streamed_text)
        self.assertNotIn("usage", finished["result"])
        self.assertEqual(finished["usage"][0]["model"], "poc-model-v1")
        self.assertGreater(finished["usage"][0]["totalTokens"], 0)
        self.assertTrue(
            any(
                event["type"] == "CUSTOM" and event.get("name") == "cost-metrics"
                for event in events
            )
        )


if __name__ == "__main__":
    unittest.main()
