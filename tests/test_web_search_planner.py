import unittest

from brain.web_search_planner import WebSearchActionPlanner


class FakeClient:
    """Returns one queued response per .chat() call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": self._responses.pop(0)}}


class FakeResearchAgent:
    def __init__(self, *, results=None, error=None):
        self._results = results
        self._error = error
        self.calls = []

    def research_structured(self, *, search_query, max_results):
        self.calls.append((search_query, max_results))
        if self._error is not None:
            raise self._error
        return self._results


class WebSearchActionPlannerTests(unittest.TestCase):
    def test_synthesizes_a_short_answer_from_real_results(self):
        research_agent = FakeResearchAgent(results=(
            {"title": "Ocean View Resort", "url": "https://a.example", "summary": "$180/night"},
            {"title": "Guam Beach Hotel", "url": "https://b.example", "summary": "$120/night"},
        ))
        client = FakeClient(["Ocean View Resort is $180/night; Guam Beach Hotel is $120/night."])
        planner = WebSearchActionPlanner(
            research_agent=research_agent, client=client, model="qwen3:8b", keep_alive=-1,
        )

        result = planner.act("Find hotels in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(
            result.summary,
            "Ocean View Resort is $180/night; Guam Beach Hotel is $120/night.",
        )
        self.assertEqual(research_agent.calls, [("Find hotels in Guam.", 5)])
        self.assertEqual(len(result.steps_taken), 2)

    def test_empty_goal_fails_without_calling_research(self):
        research_agent = FakeResearchAgent(results=())
        planner = WebSearchActionPlanner(
            research_agent=research_agent, client=FakeClient([]), model="qwen3:8b", keep_alive=-1,
        )

        result = planner.act("   ")

        self.assertEqual(result.status, "failed")
        self.assertEqual(research_agent.calls, [])

    def test_research_failure_is_reported_not_raised(self):
        research_agent = FakeResearchAgent(error=RuntimeError("No useful evidence was returned."))
        planner = WebSearchActionPlanner(
            research_agent=research_agent, client=FakeClient([]), model="qwen3:8b", keep_alive=-1,
        )

        result = planner.act("Find hotels on the moon.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "web_search_failed")
        self.assertIn("No useful evidence", result.summary)

    def test_synthesis_call_failure_falls_back_to_top_result(self):
        research_agent = FakeResearchAgent(results=(
            {"title": "Ocean View Resort", "url": "https://a.example", "summary": "$180/night"},
        ))

        class BrokenClient:
            def chat(self, **kwargs):
                raise RuntimeError("model offline")

        planner = WebSearchActionPlanner(
            research_agent=research_agent, client=BrokenClient(), model="qwen3:8b", keep_alive=-1,
        )

        result = planner.act("Find hotels in Guam.")

        # The search itself succeeded -- a synthesis hiccup must not turn
        # that into a failed step, only a plainer answer.
        self.assertEqual(result.status, "done")
        self.assertIn("Ocean View Resort", result.summary)

    def test_synthesis_returning_empty_content_falls_back_to_top_result(self):
        research_agent = FakeResearchAgent(results=(
            {"title": "Ocean View Resort", "url": "https://a.example", "summary": "$180/night"},
        ))
        client = FakeClient([""])
        planner = WebSearchActionPlanner(
            research_agent=research_agent, client=client, model="qwen3:8b", keep_alive=-1,
        )

        result = planner.act("Find hotels in Guam.")

        self.assertEqual(result.status, "done")
        self.assertIn("Ocean View Resort", result.summary)


if __name__ == "__main__":
    unittest.main()
