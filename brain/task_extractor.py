"""Structured extraction from a task step's freeform result text: Phase 4D-3.

A tier-2 planner (DesktopActionPlanner/BrowserActionPlanner) always reports
its result as one prose sentence -- that contract isn't changing here. What
4D-1/4D-2 lacked is a way to turn "Ocean View Resort ($180/night, 4.5
stars), Guam Beach Hotel ($120/night, 4.0 stars)..." into something a later
step (or the final answer) can actually compare or filter, instead of the
task planner's own model having to re-read a paragraph of prose every time
and hope it parses prices and ratings correctly.

Deliberately narrow: this extracts named items and their stated attributes
verbatim, nothing more. It never computes, infers, sorts, or fills in a
value the text didn't state -- that keeps a hallucinated price out of a
downstream comparison. Actual filtering/sorting on the extracted structure
is left to the task planner's own reasoning or a future pass; this module's
only job is turning prose into a shape that reasoning can trust.

Provenance (source, source_type, observed_at, confidence) is set the same
deterministic way as name/attributes are kept verbatim: the caller tells
this module which capability's step produced the text (never asked of the
model), and observed_at/confidence are computed in Python from a fixed
table, not guessed. This is what lets collected information carry "how
sure should I be" alongside "what is it" -- a web_search_snippet and a
browser_observed fact are not the same strength of evidence.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from brain.task_planner import ExtractedItem

# Deterministic, never model-guessed -- a fixed prior per source_type,
# not a per-fact judgment. browser_observed ranks highest (a live page
# was actually rendered and read); web_search_snippet ranks lowest of
# the "real evidence" tiers (a search engine's summary of a page, not
# the page itself); user_provided is treated as ground truth.
_CONFIDENCE_BY_SOURCE_TYPE = {
    "web_search_snippet": 0.55,
    "browser_observed": 0.85,
    "model_knowledge": 0.5,
    "user_provided": 1.0,
}

# Cheap pre-filter mirroring TaskIntentGate's own regex-before-LLM pattern:
# a step whose text is a single confirmation sentence ("Opened Notepad.")
# has nothing to extract, and this is called after every step, so skipping
# the LLM call for the common case matters for latency. Two or more
# comma-separated segments each carrying a number is a reasonable signal
# that multiple comparable, attribute-bearing items are actually present;
# a false negative here just means a genuinely list-shaped result stays as
# plain prose, never a wrong extraction.
_NUMBER_PATTERN = re.compile(r"\$?\d[\d,.]*")
_MIN_COMMA_SEGMENTS = 2
_MIN_NUMBER_HITS = 2


class TaskExtractor:
    """Turn one step's prose result into structured, comparable items."""

    def __init__(self, *, client: Any, model: str, keep_alive: Any = -1) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive

    def extract(
        self,
        text: str,
        *,
        source_type: str = "model_knowledge",
        source: str = "",
    ) -> tuple[ExtractedItem, ...]:
        text = str(text).strip()
        if not self._looks_extractable(text):
            return ()
        try:
            response = self.client.chat(
                model=self.model,
                messages=[{"role": "system", "content": self._prompt(text)}],
                stream=False,
                format="json",
                options={"temperature": 0, "num_predict": 300},
                keep_alive=self.keep_alive,
                think=False,
            )
            message = self._value(response, "message", {})
            payload = json.loads(str(self._value(message, "content", "")))
        except Exception as error:
            print(
                "[Task Extractor] Extraction failed safely: "
                f"{type(error).__name__}: {error}"
            )
            return ()
        return self._parse_items(payload, source_type=source_type, source=source)

    @staticmethod
    def _looks_extractable(text: str) -> bool:
        if text.count(",") < _MIN_COMMA_SEGMENTS:
            return False
        return len(_NUMBER_PATTERN.findall(text)) >= _MIN_NUMBER_HITS

    @staticmethod
    def _prompt(text: str) -> str:
        return (
            "Extract each distinct named item mentioned below as JSON. For "
            "each item, capture its name and any attributes explicitly "
            "stated (price, rating, date, size, distance, location, the "
            "site or source it came from, ...) as a flat string-to-string "
            "map. Never invent a value the text does not "
            "state, never compute or infer one (e.g. do not decide which "
            "is cheapest), and never include an item that is not clearly "
            "named. If nothing qualifies, return an empty list.\n"
            'Return JSON only: {"items": [{"name": "<name>", '
            '"attributes": {"<attribute>": "<value>"}}]}\n'
            f"Text: {text}"
        )

    @staticmethod
    def _parse_items(
        payload: Any, *, source_type: str, source: str,
    ) -> tuple[ExtractedItem, ...]:
        if not isinstance(payload, dict):
            return ()
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            return ()
        # Deterministic and Python-computed, exactly like the confidence
        # table -- never asked of the model, so it can't drift per item
        # within the same extraction call.
        observed_at = datetime.now().isoformat()
        confidence = _CONFIDENCE_BY_SOURCE_TYPE.get(source_type, 0.5)
        items: list[ExtractedItem] = []
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if not name:
                continue
            raw_attributes = entry.get("attributes")
            attributes = (
                {
                    str(key): str(value)
                    for key, value in raw_attributes.items()
                }
                if isinstance(raw_attributes, dict)
                else {}
            )
            items.append(ExtractedItem(
                name=name, attributes=attributes,
                source=source, source_type=source_type,
                observed_at=observed_at, confidence=confidence,
            ))
        return tuple(items)

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
