"""Live SOURCE_FOR check against a rendered source surface.

This uses a temporary profile, saves ``restaurant -> Naver Maps`` as a
standing stated preference, and runs the production recommendation
acquisition path.  The check passes only when the selected source is reached
and the evidence contains at least one concrete entity; a Naver Maps list or
search page by itself is never enough.

The run may open and operate the configured browser, but it never submits,
books, buys, saves, or signs in.

    python scripts/live_source_preference_check.py
"""

from __future__ import annotations

import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from brain import preferences, recommendation_state
from brain.chat_engine import ChatEngine
from brain.deliberation.goal import SOURCE_UTTERANCE, Slot
from brain.deliberation.profile import SOURCE_FOR, STATED, UserProfile, context_key
from brain.recommendation_state import RecommendationProblem


_QUERY = "Korean BBQ restaurants in Gangnam"


def main() -> int:
    print("=" * 70)
    print("Live Naver Maps source-preference check (read-only browser run)")
    print("=" * 70)
    engine = ChatEngine()
    try:
        with tempfile.TemporaryDirectory(prefix="elaina-live-source-") as directory:
            profile = UserProfile(path=Path(directory) / "profile.json")
            profile.observe(
                SOURCE_FOR,
                "Naver Maps",
                key=context_key("restaurant"),
                source=STATED,
            )
            engine.user_profile = profile
            engine.computer_control_mode.set_enabled(True)
            problem = RecommendationProblem(
                subject="Korean BBQ restaurants",
                domain="restaurant",
                category="restaurant",
                constraints=(
                    Slot(recommendation_state.AREA, "Gangnam", SOURCE_UTTERANCE),
                ),
                expires_at=time.monotonic() + 600,
            )
            engine.task_sessions._problem = problem
            resolution = engine._source_resolution(problem, _QUERY)
            print(resolution.log_block())
            if resolution.choice != "Naver Maps" or not resolution.applied:
                print("FAIL: the saved stated source preference did not resolve.")
                return 1

            result = engine._research_for_recommendation(
                _QUERY, resolution=resolution,
            )
            if result is None:
                print("FAIL: acquisition returned no grounded result.")
                return 1
            print(result.evidence)
            if "Observed on Naver Maps." not in result.evidence:
                print(
                    "FAIL: candidates did not come from a live Naver Maps "
                    "page; ordinary-search fallback is not proof."
                )
                return 1
            concrete = re.findall(
                r"^\s*\d+\. \[(?:FITS|UNCHECKED|MISMATCH)\] (.+?) --",
                result.evidence,
                flags=re.MULTILINE,
            )
            concrete = [
                name for name in concrete
                if name.casefold() != "naver maps"
                and not name.casefold().startswith("http")
            ]
            if not concrete:
                print("FAIL: the selected surface yielded no concrete named entities.")
                return 1
            print("PASS: Naver Maps was selected and yielded concrete candidates:")
            for name in concrete[:5]:
                print(f"  - {name}")
            return 0
    finally:
        engine.computer_control_mode.set_enabled(False)
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
