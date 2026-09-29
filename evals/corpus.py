"""The scenarios, loaded and checked for consistency.

Which rubric properties a scored turn is judged on is not written in the
scenario: it follows from the turn's ``mode`` -- the communicative goal of
the message -- and each property's ``applies_to`` in evals/rubric.json. That
is what keeps a property from being asked where it cannot apply ("why it
helps" of a question about why something happens; "answers the ask" of
"Yeah.").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SCENARIOS = HERE / "scenarios"
RUBRIC = HERE / "rubric.json"
CONTAMINATION = PROJECT_ROOT / "tests" / "contamination_matrix.json"

# Every check a scenario may name, with the type it takes. Kept here so a
# typo in a scenario file is a test failure rather than a check that
# silently never ran.
CHECKS = {
    # Mechanical facts about the turn.
    "language": str,               # "en" or "ko": the reply is in it
    "not_searched": bool,          # no web search ran this turn
    "no_cards": bool,              # no result cards were shown
    "speakable": bool,             # nothing unpronounceable reached the voice
    "display_math_intact": bool,   # notation in the draft reached the screen whole
    # Correctness against a known answer.
    "must_match": list,            # every regex matches the displayed reply
    "must_match_any": list,        # at least one regex matches
    "clock_times": list,           # zones whose time at the turn must be stated
    "hour_difference": list,       # two zones; the stated gap must be right
    "utc_offset": dict,            # {"zone", "required"}: any stated offset is right
    "calendar": dict,              # {"date", "year"}: stated date parts are today's
    "quantity": list,              # [{"unit", "value", "tolerance", "required"}]
    "quantity_required_any": bool, # at least one listed quantity must be stated
    # The subject of an earlier turn, not dragged into this one.
    "must_contain_any": list,      # a plain phrase, case-insensitive
    "must_not_contain": list,
    # Kept for older scenarios; not used by version 2.
    "must_not_match": list,
    "max_words": int,
    "min_words": int,
    "not_repeat_previous": bool,
    # The reply answers rather than only asking back (Phase 3 R9).
    "answers_not_asks": bool,
}

SUITES = ("explanation", "relevance", "modality", "trajectories", "contamination")
# Suites added after Phase 2. Loaded only when named, so a run over "every
# suite" is still the Phase 2 corpus and the rerun compares like with like.
EXTRA_SUITES = ("followups",)
ALL_SUITES = SUITES + EXTRA_SUITES

# The turn types of tests/contamination_matrix.json. The matrix predates
# modes and is read as it is; this is the one place its turns are
# classified, reviewed with the version 2 rubric (docs/EVAL_RUBRIC.md).
CONTAMINATION_MODES = {
    "steep_time_after_coffee": "fact",
    "fresh_question_after_time": "how",
    "arithmetic_in_a_social_thread": "fact",
    "refusal_is_not_a_request": "receipt",
    "acknowledgement_is_not_a_constraint": "receipt",
    "a_number_that_is_not_a_budget": "remark",
    "which_one_means_the_nearest_subject": "fact",
    "a_new_subject_closes_the_old_one": "fact",
    "a_remark_is_not_a_task": "remark",
    "thanks_closes_rather_than_continues": "receipt",
    "a_correction_outranks_what_was_held": "fact",
    "a_finished_task_does_not_reopen": "fact",
}


def _rubric_file() -> dict:
    return json.loads(RUBRIC.read_text(encoding="utf-8"))


def rubric_version() -> int:
    return int(_rubric_file().get("version", 1))


def rubric_fingerprint() -> str:
    """A hash of the exact property definitions and their applicability.

    A verdict counts only against the rubric it was given under. The version
    number says which design; the fingerprint says which words -- so an
    edited definition cannot be silently averaged with verdicts given under
    the old one.
    """
    import hashlib

    data = json.dumps(
        {"version": rubric_version(), "properties": rubric(), "modes": modes()},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha1(data.encode("utf-8")).hexdigest()[:12]


def rubric() -> dict[str, dict]:
    """Every property: its definition, the modes it applies to, and when
    (if ever) it may be answered n/a."""
    return _rubric_file()["properties"]


def modes() -> dict[str, str]:
    return _rubric_file()["modes"]


def definitions() -> dict[str, str]:
    return {name: spec["definition"] for name, spec in rubric().items()}


def properties_for(mode: str) -> tuple[str, ...]:
    """The rubric properties that apply to a turn with this goal, in order."""
    return tuple(
        name for name, spec in rubric().items() if mode in spec.get("applies_to", ())
    )


@dataclass(frozen=True)
class Turn:
    say: str
    score: bool = False
    checks: dict = field(default_factory=dict)
    mode: str = ""

    @property
    def rubric(self) -> tuple[str, ...]:
        return properties_for(self.mode) if self.score else ()


@dataclass(frozen=True)
class Scenario:
    id: str
    suite: str
    kind: str
    language: str
    turns: tuple[Turn, ...]
    mode: str = ""
    domain: str = ""
    source: str = ""
    failure: tuple[str, ...] = ()
    desired: tuple[str, ...] = ()

    @property
    def scored(self) -> list[tuple[int, Turn]]:
        return [(index, turn) for index, turn in enumerate(self.turns) if turn.score]


def _load_suite(path: Path) -> list[Scenario]:
    data = json.loads(path.read_text(encoding="utf-8"))
    defaults = data.get("defaults", {})
    scenarios = []
    for raw in data["scenarios"]:
        turns = []
        for turn in raw["turns"]:
            scored = bool(turn.get("score"))
            checks = {**defaults.get("checks", {}), **turn.get("checks", {})} if scored else {}
            mode = turn.get("mode") or (raw.get("mode", "") if scored else "")
            turns.append(Turn(turn["say"], scored, checks, mode))
        scenarios.append(Scenario(
            id=raw["id"], suite=data["suite"], kind=data.get("kind", "behavioral"),
            language=raw.get("language", "en"), turns=tuple(turns),
            mode=raw.get("mode", ""), domain=raw.get("domain", ""),
            source=raw.get("source", ""),
            failure=tuple(raw.get("failure", ())), desired=tuple(raw.get("desired", ())),
        ))
    return scenarios


def _contamination() -> list[Scenario]:
    """tests/contamination_matrix.json, read as it is.

    Its cases are earlier live failures of exactly this kind -- an answer
    that carried the previous subject into the next question -- so they
    belong in the same measurement. The file stays the one place they are
    written; this only reads it, with the matrix's own rule: any of
    ``must_mention``, none of ``must_not_mention``.
    """
    data = json.loads(CONTAMINATION.read_text(encoding="utf-8"))
    scenarios = []
    for case in data["cases"]:
        checks = {}
        if case.get("must_mention"):
            checks["must_contain_any"] = list(case["must_mention"])
        if case.get("must_not_mention"):
            checks["must_not_contain"] = list(case["must_not_mention"])
        mode = CONTAMINATION_MODES.get(case["id"], "")
        turns = [Turn(line) for line in case.get("setup", ())]
        turns.append(Turn(case["turn"], True, checks, mode))
        scenarios.append(Scenario(
            id=case["id"], suite="contamination",
            kind="trajectory" if case.get("setup") else "behavioral",
            language=case.get("language", "en"), turns=tuple(turns),
            mode=mode, source=case.get("why", ""),
        ))
    return scenarios


def load(suites=None) -> list[Scenario]:
    """Every scenario in the named suites (all of them when None)."""
    wanted = set(suites or SUITES)
    scenarios: list[Scenario] = []
    for path in sorted(SCENARIOS.glob("*.json")):
        suite = json.loads(path.read_text(encoding="utf-8"))["suite"]
        if suite in wanted:
            scenarios.extend(_load_suite(path))
    if "contamination" in wanted:
        scenarios.extend(_contamination())
    return scenarios


def problems(scenarios=None) -> list[str]:
    """Everything inconsistent in the corpus; empty when it is sound."""
    scenarios = load(ALL_SUITES) if scenarios is None else scenarios
    known_modes = modes()
    properties = rubric()
    found: list[str] = []
    for name, spec in properties.items():
        unknown = set(spec.get("applies_to", ())) - set(known_modes)
        if unknown:
            found.append(f"rubric/{name}: applies to unknown modes {sorted(unknown)}")
        if not str(spec.get("definition", "")).strip():
            found.append(f"rubric/{name}: no definition")
    seen: set[str] = set()
    for scenario in scenarios:
        where = f"{scenario.suite}/{scenario.id}"
        if scenario.id in seen:
            found.append(f"{where}: duplicate id")
        seen.add(scenario.id)
        if scenario.suite not in ALL_SUITES:
            found.append(f"{where}: unknown suite")
        if scenario.language not in ("en", "ko"):
            found.append(f"{where}: language must be en or ko")
        if not scenario.scored:
            found.append(f"{where}: no scored turn")
        for index, turn in scenario.scored:
            if turn.mode not in known_modes:
                found.append(f"{where}[{index}]: mode {turn.mode!r} is not one of {sorted(known_modes)}")
            elif not turn.rubric:
                found.append(f"{where}[{index}]: no rubric property applies to mode {turn.mode!r}")
            for name, value in turn.checks.items():
                expected = CHECKS.get(name)
                if expected is None:
                    found.append(f"{where}[{index}]: unknown check {name!r}")
                elif not isinstance(value, expected):
                    found.append(f"{where}[{index}]: {name} should be {expected.__name__}")
    return found
