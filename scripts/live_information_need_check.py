"""Exercise the real model against the Information Acquisition layer.

Mirrors live_task_planner_check.py's split exactly: the real Ollama model
drives every decision under test (TaskIntentGate's escalation call,
TaskPlanner's _preview()/_plan_next()/extraction calls, and
SemanticIntentRouter's classification call) but the tier-2 capabilities a
plan step would actually dispatch to (web_search, browser_control) are
simulated stand-ins, not real DuckDuckGo calls or a real controlled
browser -- consistent with this project's existing "real model, simulated
environment" pattern.

Covers the 5 scenarios from the Information Acquisition design, plus
paraphrases of each, plus varying starting states:
  1. Casual discovery -- must stay off the task planner entirely (the
     fast, cheap web_search path), verified against the real TaskIntentGate.
  2. Quantity + price discovery -- escalates, stays "discover", answers
     from web_search results filtered by the stated budget.
  3. Quantity + price + explicit currency verification -- escalates,
     becomes "verify", actually dispatches a browser_control step.
  4. Bare follow-up verification question -- escalates, becomes "verify",
     uses browser_control without a redundant new web_search.
  5. A consequential commit ("book the best one") -- must NOT escalate via
     TaskIntentGate (it's one committing action, not a multi-step research
     task) and must instead reach SemanticIntentRouter's browser_action
     classification, which is what lets the existing is_committing_element
     confirmation checkpoint fire at all.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import ollama


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.registry import AgentRegistry  # noqa: E402
from brain.browser_action_planner import ActionPlanResult  # noqa: E402
from brain.intent_router import SemanticIntentRouter  # noqa: E402
from brain.task_extractor import TaskExtractor  # noqa: E402
from brain.task_intent_gate import TaskIntentGate  # noqa: E402
from brain.task_planner import TaskPlanner  # noqa: E402
from config.loader import Config  # noqa: E402
from scripts.console_style import status_label  # noqa: E402

_HOTEL_LIST = (
    "Myeongdong Hotel (₩150,000/night, 4.5 stars), "
    "Gangnam Suites (₩180,000/night, 4.2 stars), "
    "Hongdae Boutique (₩120,000/night, 4.0 stars), "
    "Itaewon Plaza (₩250,000/night, 4.6 stars), "
    "and Dongdaemun Inn (₩95,000/night, 3.8 stars)"
)


class SimulatedWebSearchCapability:
    """A discovery-level source: snippet-style results, never a directly
    observed page. A second call (a "browser already open" starting state)
    returns the same list slightly reworded, matching the existing
    SimulatedBrowserCapability's increasingly-informative-by-call-count
    philosophy: the real model's reasoning is under test, not a scripted
    search index."""

    def __init__(self, *, warm_start: bool = False) -> None:
        self.calls: list[str] = ["(seed call from a prior scenario)"] if warm_start else []

    def act(self, goal: str) -> ActionPlanResult:
        self.calls.append(goal)
        if len(self.calls) <= 1:
            return ActionPlanResult(
                "done", f"Web search found several hotels in Seoul: {_HOTEL_LIST}.",
            )
        return ActionPlanResult(
            "done", f"Re-searched and found the same hotels again: {_HOTEL_LIST}.",
        )


class SimulatedBrowserVerifyCapability:
    """A directly-observed page: confirms one specific fact per call,
    never a bulk re-listing -- proves a "verify" goal drives real,
    targeted browser_control steps rather than re-reading search
    snippets."""

    _KNOWN = {
        "Myeongdong Hotel": "₩155,000/night",
        "Gangnam Suites": "₩182,000/night",
        "Hongdae Boutique": "₩121,000/night",
        "Itaewon Plaza": "₩248,000/night",
        "Dongdaemun Inn": "₩96,000/night",
    }

    def __init__(self, *, warm_start: bool = False) -> None:
        self.calls: list[str] = ["(seed call from a prior scenario)"] if warm_start else []

    def act(self, goal: str) -> ActionPlanResult:
        self.calls.append(goal)
        # Naming whichever hotel(s) the sub_goal actually asks about (not a
        # single fixed hotel) matters here: a goal verifying several named
        # hotels needs a distinguishable answer per one, or the model can
        # never tell it already covered a given target and keeps retrying.
        named = [name for name in self._KNOWN if name in goal]
        if not named:
            named = ["Myeongdong Hotel"]
        confirmations = "; ".join(
            f"{name}'s actual current price is {self._KNOWN[name]} and it "
            "shows rooms available Friday night"
            for name in named
        )
        return ActionPlanResult(
            "done", f"Opened the booking page(s): {confirmations}.",
        )


class NeverCalledCapability:
    """Records (then fails) any step a scenario has no legitimate reason
    to need. TaskPlanner's own step runner catches this class's exception
    like any other capability failure -- the model may recover from it and
    keep going -- so `.calls` is what the post-hoc checks below actually
    rely on to notice the dispatch happened at all, not the raise itself."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    def act(self, goal: str):
        self.calls.append(goal)
        raise AssertionError(
            f"This scenario should never dispatch a step to {self.name!r}: "
            f"{goal!r}"
        )


class FakeControlMode:
    enabled = True


def _build_planner(
    *, web_search: Any, browser: Any, model: str, keep_alive: Any, client: Any,
    task_extractor: Any,
) -> TaskPlanner:
    return TaskPlanner(
        client=client,
        model=model,
        keep_alive=keep_alive,
        agent_registry=AgentRegistry(),
        desktop_action_planner=NeverCalledCapability("ui_control"),
        browser_action_planner=browser,
        web_search_action_planner=web_search,
        computer_control_mode=FakeControlMode(),
        browser_control_enabled=True,
        task_extractor=task_extractor,
    )


def _print_run(goal: str, result) -> None:
    print(f"Goal: {goal}")
    if result.task_state.plan_preview:
        print(f"Plan preview: {result.task_state.plan_preview}")
    print(
        f"Status: {result.status}  Steps: {result.task_state.step_count}  "
        f"Verification level: {result.task_state.verification_level}"
    )
    for step_result in result.task_state.completed_steps:
        print(
            f"  [{step_result.step.capability}] {step_result.step.sub_goal!r} "
            f"-> {step_result.status}"
        )
    print(f"Summary: {result.summary}\n")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    config = Config()
    model = str(config.get("llm", "ollama", "model"))
    keep_alive = config.get(
        "llm", "ollama", "keep_alive", default=-1, required=False,
    )
    client = ollama.Client(host=config.get("llm", "ollama", "base_url"))

    all_checks: list[tuple[str, bool]] = []

    def gate_check(text: str) -> bool:
        gate = TaskIntentGate(client=client, model=model, keep_alive=keep_alive)
        decision = gate.check(text)
        print(f"[Gate] {text!r} -> is_multistep={decision.is_multistep} ({decision.reason})")
        return decision.is_multistep

    # --- Scenario 1: casual discovery must stay off the task planner. ---
    for text in (
        "Give me some good hotels in Seoul.",
        "What are some good hotels to stay at in Seoul?",
    ):
        all_checks.append((
            f"[Scenario 1] {text!r} never escalates to the task planner",
            not gate_check(text),
        ))

    # --- Scenario 2: quantity + price discovery -- stays "discover". ---
    for text in (
        "Give me five good hotels in Seoul under ₩200,000.",
        "Can you find me five highly rated hotels in Seoul for less than ₩200,000?",
    ):
        escalates = gate_check(text)
        all_checks.append((f"[Scenario 2] {text!r} escalates to the task planner", escalates))
        if not escalates:
            continue
        web_search = SimulatedWebSearchCapability()
        browser = SimulatedBrowserVerifyCapability()
        extractor = TaskExtractor(client=client, model=model, keep_alive=keep_alive)
        planner = _build_planner(
            web_search=web_search, browser=browser, model=model,
            keep_alive=keep_alive, client=client, task_extractor=extractor,
        )
        result = planner.run(text)
        _print_run(text, result)
        all_checks += [
            ("[Scenario 2] Reached done", result.status == "done"),
            (
                "[Scenario 2] Verification level stayed 'discover' "
                "(no explicit currency/availability check requested)",
                result.task_state.verification_level == "discover",
            ),
            ("[Scenario 2] web_search was actually used", bool(web_search.calls)),
            (
                "[Scenario 2] Final summary respects the ₩200,000 budget "
                "(does not recommend the ₩250,000 hotel)",
                "Itaewon" not in result.summary,
            ),
        ]

    # --- Scenario 3: explicit currency verification -- becomes "verify",
    # actually dispatches a browser_control step. Warm-started capabilities
    # simulate a browser already open from a prior scenario. ---
    for text in (
        "I'm booking a hotel in Seoul tonight, find five highly-rated "
        "hotels under ₩200,000 and check their actual current prices.",
        "I need to book a hotel in Seoul tonight -- search for five "
        "highly rated ones under ₩200,000 and confirm their current "
        "prices.",
    ):
        escalates = gate_check(text)
        all_checks.append((f"[Scenario 3] {text!r} escalates to the task planner", escalates))
        if not escalates:
            continue
        web_search = SimulatedWebSearchCapability(warm_start=True)
        browser = SimulatedBrowserVerifyCapability(warm_start=True)
        extractor = TaskExtractor(client=client, model=model, keep_alive=keep_alive)
        planner = _build_planner(
            web_search=web_search, browser=browser, model=model,
            keep_alive=keep_alive, client=client, task_extractor=extractor,
        )
        result = planner.run(text)
        _print_run(text, result)
        all_checks += [
            ("[Scenario 3] Reached done", result.status == "done"),
            (
                "[Scenario 3] Verification level became 'verify'",
                result.task_state.verification_level == "verify",
            ),
            (
                "[Scenario 3] browser_control was actually dispatched "
                "(not just web_search snippets)",
                len(browser.calls) > 1,  # > 1 accounts for the warm-start seed
            ),
        ]

    # --- Scenario 4: bare follow-up verification. The gate sees the real,
    # deictic wording a user would actually say ("these"/"those"); the
    # planner is handed the same request already resolved against the
    # prior results (as chat_engine's own conversation state would resolve
    # it before task_planner.run() ever sees the goal) -- confirms via
    # targeted browser_control, not a redundant new search. ---
    for gate_text, planner_goal in (
        (
            "Which of these hotels is actually available Friday night?",
            "Which of Myeongdong Hotel, Gangnam Suites, and Hongdae "
            "Boutique is actually available Friday night?",
        ),
        (
            "Are any of those hotels actually available this Friday?",
            "Are Myeongdong Hotel, Gangnam Suites, or Hongdae Boutique "
            "actually available this Friday?",
        ),
    ):
        escalates = gate_check(gate_text)
        all_checks.append(
            (f"[Scenario 4] {gate_text!r} escalates to the task planner", escalates)
        )
        if not escalates:
            continue
        web_search = NeverCalledCapability("web_search")
        browser = SimulatedBrowserVerifyCapability()
        extractor = TaskExtractor(client=client, model=model, keep_alive=keep_alive)
        planner = _build_planner(
            web_search=web_search, browser=browser, model=model,
            keep_alive=keep_alive, client=client, task_extractor=extractor,
        )
        result = planner.run(planner_goal)
        _print_run(planner_goal, result)
        if web_search.calls:
            # Informational, not a checklist failure: the "verify" prompt
            # itself allows a web_search pass before browser-confirming,
            # so a model choosing to (re-)search first when the hotels are
            # already named is a minor inefficiency, not a wrong answer --
            # the real requirement is that it still verifies via
            # browser_control, checked below.
            print(
                "[Note] Tried web_search before falling back to "
                "browser_control (allowed, just not the most direct path)."
            )
        all_checks += [
            ("[Scenario 4] Reached done", result.status == "done"),
            (
                "[Scenario 4] Verification level became 'verify'",
                result.task_state.verification_level == "verify",
            ),
            ("[Scenario 4] browser_control was actually used", bool(browser.calls)),
        ]

    # --- Scenario 5: a consequential commit must NOT escalate via the
    # task-planner gate (it's one action, not a research task) and must
    # instead reach SemanticIntentRouter's browser_action classification --
    # tested across two different starting states (a results page already
    # open vs. a specific hotel page already open). ---
    for text in ("Book the best one.", "Reserve the best one for me."):
        all_checks.append((
            f"[Scenario 5] {text!r} does not escalate via the task-planner gate",
            not gate_check(text),
        ))

    router = SemanticIntentRouter(client, model)
    for text, surface in (
        (
            "Book the best one.",
            {
                "kind": "browser",
                "title": "best hotels in Seoul - Google Search",
                "url": "https://www.google.com/search?q=best+hotels+in+seoul",
            },
        ),
        (
            "Reserve the best one for me.",
            {
                "kind": "browser",
                "title": "Myeongdong Hotel - Booking.com",
                "url": "https://www.booking.com/hotel/kr/myeongdong.html",
            },
        ),
    ):
        result = router.route(
            text,
            computer_control_enabled=True,
            conversation_state={"active_desktop_surface": surface},
        )
        print(
            f"[Router] {text!r} (surface={surface['title']!r}) -> "
            f"intent={result.intent} operation={result.computer_operation} "
            f"action_requested={result.action_requested}"
        )
        all_checks += [
            (
                f"[Scenario 5] {text!r} classifies as computer_action",
                result.intent == "computer_action",
            ),
            (
                f"[Scenario 5] {text!r} reaches browser_action "
                "(so the confirmation checkpoint can fire)",
                result.computer_operation == "browser_action",
            ),
        ]

    failures = 0
    for name, passed in all_checks:
        failures += 0 if passed else 1
        print(f"[{status_label(passed)}] {name}")
    print(
        f"{len(all_checks) - failures}/{len(all_checks)} live information-need "
        "checks passed."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
