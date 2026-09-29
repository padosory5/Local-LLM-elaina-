"""Turn a recorded turn into a draft scenario: failures create data first.

    .venv/Scripts/python.exe -m evals.from_failure 3f2a            # a turn id prefix
    .venv/Scripts/python.exe -m evals.from_failure 3f2a --dir <runtime>

When something she said was wrong, the record of that turn
(core/turn_trace.py) already holds the words, the turns before it in the
same session, what the model drafted, and every stage that changed it. This
prints a scenario for evals/scenarios/ with all of that filled in: the
conversation up to the turn, the draft and the reply as ``source``, and a
blank rubric and set of checks. What it cannot fill in is the judgment --
which property the reply failed, and what a good one would have done --
and that is the part a person writes before any code is.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import RUNTIME_ROOT  # noqa: E402
from evals import corpus  # noqa: E402


def records(runtime: Path) -> list[dict]:
    found = []
    for path in sorted((runtime / "turn_trace").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                found.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    found.sort(key=lambda record: record.get("started_at", ""))
    return found


def draft_scenario(turn_id: str, runtime: Path) -> dict:
    everything = records(runtime)
    target = next((r for r in everything if str(r.get("turn_id", "")).startswith(turn_id)), None)
    if target is None:
        raise SystemExit(f"No turn starting with {turn_id!r} in {runtime / 'turn_trace'}.")
    session = [r for r in everything if r.get("session_id") == target.get("session_id")]
    before = [r for r in session if r.get("started_at", "") < target.get("started_at", "")]
    context = target.get("context") or {}
    language = context.get("language") or "en"
    draft = (target.get("draft") or {}).get("text", "")
    changed = [step["name"] for step in target.get("steps") or () if step.get("changed")]
    source = (
        f"live {str(target.get('started_at', ''))[:10]}, turn {target.get('turn_id')}: "
        f"drafted {draft!r}; shown {target.get('display')!r}"
        + (f"; changed by {', '.join(changed)}" if changed else "; no stage changed it")
    )
    turns = [{"say": record.get("user_input", "")} for record in before[-4:]]
    turns.append({
        "say": target.get("user_input", ""),
        "score": True,
        "checks": {},
        "rubric": [],
    })
    return {
        "id": "TODO_name_the_behaviour_not_the_topic",
        "language": language,
        "mode": "",
        "domain": "",
        "source": source,
        "turns": turns,
        "failure": ["TODO: what was wrong with the reply"],
        "desired": ["TODO: what a good reply would have done"],
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("turn", help="a turn id, or its first few characters")
    parser.add_argument("--dir", default="", help="a runtime other than this one")
    args = parser.parse_args()
    runtime = Path(args.dir) if args.dir else Path(RUNTIME_ROOT)
    scenario = draft_scenario(args.turn, runtime)
    print(json.dumps(scenario, indent=2, ensure_ascii=False))
    print(
        "\nFill in the TODOs, choose rubric properties from evals/rubric.json "
        f"({', '.join(corpus.rubric())}) and any checks, and add it to a file "
        "in evals/scenarios/.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
