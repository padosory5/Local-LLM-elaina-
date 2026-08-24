import json
import unittest

from brain.task_intent_gate import TaskIntentGate


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def chat(self, **_kwargs):
        self.calls += 1
        return {"message": {"content": json.dumps(self.payload)}}


class MustNotRunClient:
    def chat(self, **_kwargs):
        raise AssertionError(
            "The escalation LLM call should not run when the regex "
            "heuristic doesn't suspect a compound goal."
        )


class TaskIntentGateTests(unittest.TestCase):
    def test_two_different_capabilities_escalates_to_the_llm_check(self):
        client = FakeClient({
            "is_multistep_task": True,
            "confidence": 0.9,
            "reason": "Opens a native app, then separately searches the web.",
        })
        gate = TaskIntentGate(client=client, model="qwen3:8b")

        decision = gate.check("open Whale and search for UW tuition")

        self.assertEqual(client.calls, 1)
        self.assertTrue(decision.is_multistep)

    def test_same_capability_on_both_sides_never_escalates(self):
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("open Spotify and play Dynamite")

        self.assertFalse(decision.is_multistep)

    def test_no_conjunction_never_escalates(self):
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("search for hotels in Guam")

        self.assertFalse(decision.is_multistep)

    def test_heuristic_hit_but_llm_disagrees_stays_single_step(self):
        client = FakeClient({
            "is_multistep_task": False,
            "confidence": 0.85,
            "reason": "This is really one browser action.",
        })
        gate = TaskIntentGate(client=client, model="qwen3:8b")

        decision = gate.check("open the browser and search for hotels in Guam")

        self.assertEqual(client.calls, 1)
        self.assertFalse(decision.is_multistep)

    def test_llm_failure_fails_closed_to_single_step(self):
        class BrokenClient:
            def chat(self, **_kwargs):
                raise RuntimeError("offline")

        gate = TaskIntentGate(client=BrokenClient(), model="qwen3:8b")

        decision = gate.check("open Whale and search for UW tuition")

        self.assertFalse(decision.is_multistep)


if __name__ == "__main__":
    unittest.main()
