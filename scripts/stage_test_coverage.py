"""Which tests make each stage of the reply actually do something.

"Which tests require this guard?" cannot be answered by searching for its
name: a test that imports a module may never reach the stage, and a
whole-turn test reaches every stage without naming any of them. The turn
records can answer it. This runs the deterministic suite once and, for every
test, notes which stages ran during its turns and which of them changed the
reply. A stage that changes the reply inside a test is a stage that test's
assertions were written against.

    .venv/Scripts/python.exe scripts/stage_test_coverage.py

Writes runtime/debug/stage_coverage.json, which
scripts/response_pipeline_report.py reads. Takes as long as the unit suite
(about two and a half minutes).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# Measuring her decisions never needs her voice, and the voice is billed.
os.environ.setdefault("ELAINA_TTS", "off")

from core import turn_trace  # noqa: E402
from core.paths import DEBUG_DIRECTORY  # noqa: E402
from tests.run_tests import discovered_modules  # noqa: E402


DEFAULT_OUT = Path(DEBUG_DIRECTORY) / "stage_coverage.json"


class _Collecting(unittest.TextTestResult):
    """A test result that also notes the turns each test ran."""

    stages: dict[str, dict] = {}
    tests_with_turns = 0
    turns = 0

    def startTest(self, test) -> None:
        self._mark = turn_trace.summary()["turns"]
        super().startTest(test)

    def stopTest(self, test) -> None:
        traces = turn_trace.finished_since(self._mark)
        if traces:
            type(self).tests_with_turns += 1
            type(self).turns += len(traces)
        name = test.id()
        for trace in traces:
            for step in trace.steps:
                if step.get("gap"):
                    continue
                entry = self.stages.setdefault(step["name"], {
                    "runs": 0, "changed": 0,
                    "tests_running": set(), "tests_changing": set(),
                })
                entry["runs"] += 1
                entry["tests_running"].add(name)
                if step.get("changed"):
                    entry["changed"] += 1
                    entry["tests_changing"].add(name)
        super().stopTest(test)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        loader.loadTestsFromName(f"tests.{module}")
        for module in discovered_modules()
    )
    runner = unittest.TextTestRunner(
        stream=io.StringIO(), verbosity=0, buffer=True,
        resultclass=_Collecting,
    )
    result = runner.run(suite)

    stages = {
        name: {
            "runs": entry["runs"],
            "changed": entry["changed"],
            "tests_running": sorted(entry["tests_running"]),
            "tests_changing": sorted(entry["tests_changing"]),
        }
        for name, entry in sorted(_Collecting.stages.items())
    }
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tests": result.testsRun,
        "failures": len(result.failures) + len(result.errors),
        "tests_with_turns": _Collecting.tests_with_turns,
        "turns": _Collecting.turns,
        "stages": stages,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(
        f"{result.testsRun} tests, {report['failures']} failed; "
        f"{report['tests_with_turns']} ran whole turns ({report['turns']} turns). "
        f"Wrote {out}"
    )
    for name, entry in stages.items():
        print(f"  {name:<26} ran in {len(entry['tests_running']):>4} tests, "
              f"changed the reply in {len(entry['tests_changing']):>3}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
