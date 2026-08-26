import json
import unittest

from brain.task_extractor import TaskExtractor


class FakeClient:
    """Returns one queued JSON decision per .chat() call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps(self._responses.pop(0))}}


class TaskExtractorTests(unittest.TestCase):
    def test_extracts_named_items_with_stated_attributes(self):
        extractor = TaskExtractor(
            client=FakeClient([
                {
                    "items": [
                        {
                            "name": "Ocean View Resort",
                            "attributes": {"price": "$180/night", "rating": "4.5 stars"},
                        },
                        {
                            "name": "Guam Beach Hotel",
                            "attributes": {"price": "$120/night", "rating": "4.0 stars"},
                        },
                    ],
                },
            ]),
            model="qwen3:8b",
            keep_alive=-1,
        )

        items = extractor.extract(
            "Ocean View Resort ($180/night, 4.5 stars), Guam Beach Hotel "
            "($120/night, 4.0 stars)."
        )

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].name, "Ocean View Resort")
        self.assertEqual(items[0].attributes["price"], "$180/night")
        self.assertEqual(items[1].attributes["rating"], "4.0 stars")

    def test_skips_the_model_call_for_a_plain_single_action_sentence(self):
        client = FakeClient([])  # would raise IndexError if ever called

        extractor = TaskExtractor(client=client, model="qwen3:8b", keep_alive=-1)
        items = extractor.extract("Opened Notepad.")

        self.assertEqual(items, ())
        self.assertEqual(client.calls, [])

    def test_returns_empty_when_the_model_finds_nothing_to_extract(self):
        extractor = TaskExtractor(
            client=FakeClient([{"items": []}]),
            model="qwen3:8b",
            keep_alive=-1,
        )

        # Still "looks extractable" by the cheap regex (2+ commas, 2+
        # numbers) but genuinely has no named items -- e.g. a page that
        # just lists numbers.
        items = extractor.extract("Page 1 of 12, showing 1, 2, 3, 4, 5.")

        self.assertEqual(items, ())

    def test_extraction_failure_fails_safely(self):
        extractor = TaskExtractor(client=FakeClient([]), model="qwen3:8b", keep_alive=-1)
        # No queued responses at all -- FakeClient.chat() raises IndexError,
        # exercising the extractor's own exception handling.

        items = extractor.extract("A ($1), B ($2), C ($3).")

        self.assertEqual(items, ())

    def test_ignores_an_item_with_no_name(self):
        extractor = TaskExtractor(
            client=FakeClient([
                {"items": [{"attributes": {"price": "$1"}}, {"name": "B", "attributes": {}}]},
            ]),
            model="qwen3:8b",
            keep_alive=-1,
        )

        items = extractor.extract("A ($1), B ($2), C ($3).")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "B")

    def test_malformed_response_shape_returns_empty(self):
        extractor = TaskExtractor(
            client=FakeClient(["not a dict"]),
            model="qwen3:8b",
            keep_alive=-1,
        )

        items = extractor.extract("A ($1), B ($2), C ($3).")

        self.assertEqual(items, ())


class TaskExtractorProvenanceTests(unittest.TestCase):
    """Provenance is deterministic (Python-computed from source_type),
    never asked of the model -- same 'never invent a value' contract as
    name/attributes."""

    def test_defaults_to_model_knowledge_when_unspecified(self):
        extractor = TaskExtractor(
            client=FakeClient([{"items": [{"name": "A", "attributes": {"x": "$1"}}]}]),
            model="qwen3:8b", keep_alive=-1,
        )

        items = extractor.extract("A ($1), B ($2), C ($3).")

        self.assertEqual(items[0].source_type, "model_knowledge")
        self.assertEqual(items[0].confidence, 0.5)
        self.assertEqual(items[0].source, "")
        self.assertTrue(items[0].observed_at)

    def test_web_search_snippet_gets_lower_confidence_than_browser_observed(self):
        extractor = TaskExtractor(
            client=FakeClient([
                {"items": [{"name": "A", "attributes": {"x": "$1"}}]},
                {"items": [{"name": "B", "attributes": {"x": "$2"}}]},
            ]),
            model="qwen3:8b", keep_alive=-1,
        )

        web_items = extractor.extract(
            "A ($1), B ($2), C ($3).", source_type="web_search_snippet",
            source="hotels in Guam",
        )
        browser_items = extractor.extract(
            "A ($1), B ($2), C ($3).", source_type="browser_observed",
            source="Search for hotels in Guam.",
        )

        self.assertEqual(web_items[0].source_type, "web_search_snippet")
        self.assertEqual(web_items[0].source, "hotels in Guam")
        self.assertEqual(browser_items[0].source_type, "browser_observed")
        self.assertLess(web_items[0].confidence, browser_items[0].confidence)

    def test_user_provided_gets_full_confidence(self):
        extractor = TaskExtractor(
            client=FakeClient([{"items": [{"name": "A", "attributes": {"x": "$1"}}]}]),
            model="qwen3:8b", keep_alive=-1,
        )

        items = extractor.extract(
            "A ($1), B ($2), C ($3).", source_type="user_provided",
        )

        self.assertEqual(items[0].confidence, 1.0)

    def test_unknown_source_type_falls_back_to_a_neutral_confidence(self):
        extractor = TaskExtractor(
            client=FakeClient([{"items": [{"name": "A", "attributes": {"x": "$1"}}]}]),
            model="qwen3:8b", keep_alive=-1,
        )

        items = extractor.extract(
            "A ($1), B ($2), C ($3).", source_type="something_unrecognized",
        )

        self.assertEqual(items[0].confidence, 0.5)

    def test_all_items_in_one_call_share_the_same_observed_at(self):
        extractor = TaskExtractor(
            client=FakeClient([{
                "items": [
                    {"name": "A", "attributes": {"x": "$1"}},
                    {"name": "B", "attributes": {"x": "$2"}},
                ],
            }]),
            model="qwen3:8b", keep_alive=-1,
        )

        items = extractor.extract("A ($1), B ($2), C ($3).")

        self.assertEqual(items[0].observed_at, items[1].observed_at)


if __name__ == "__main__":
    unittest.main()
