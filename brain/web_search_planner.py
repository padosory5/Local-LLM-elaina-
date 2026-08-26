"""Web search as a TaskPlanner capability: discovery without a browser.

Wraps the existing ResearchAgent/WebSearchTool -- not a second search
implementation. The plain `web_search` intent's own fast, cheap,
zero-TaskPlanner-overhead path (brain/chat_engine.py calling
self.research_agent.research() directly) is untouched; this adapter
exists only so a multi-step task can dispatch a sub_goal to "search the
web and report back" using the same real search backend, alongside
browser_control/ui_control steps, when a research task needs more than
one capability in sequence.

TaskStepResult.summary is documented (brain/task_planner.py) as always
one prose sentence -- raw ResearchAgent evidence (concatenated
"[i] title\\nSource: url\\nSnippet: ..." blocks per result) would violate
that contract and blow past the task planner's own display-length caps.
So this planner runs its own small synthesis call turning the structured
results into one short sentence before returning, the same
single-purpose-call pattern already established by TaskExtractor and
TaskPlanner._preview() in this codebase.
"""

from __future__ import annotations

from typing import Any

from brain.browser_action_planner import ActionPlanResult

_SYNTHESIS_PROMPT = (
    "Summarize these real web search results in one short sentence "
    "(spoken aloud, under 30 words) that directly addresses the goal -- "
    "state only what the results actually say, never add or infer a "
    "detail they do not contain.\n"
    "Goal: {goal}\n"
    "Results:\n{results}"
)


class WebSearchActionPlanner:
    """One bounded web-search step: search, then synthesize one sentence."""

    def __init__(
        self,
        *,
        research_agent: Any,
        client: Any,
        model: str,
        keep_alive: Any = -1,
        max_results: int = 5,
    ) -> None:
        self.research_agent = research_agent
        self.client = client
        self.model = model
        self.keep_alive = keep_alive
        self.max_results = int(max_results)

    def act(self, goal: str) -> ActionPlanResult:
        goal = str(goal).strip()
        if not goal:
            return ActionPlanResult(
                "failed", "The search goal was empty.",
                failure_code="invalid_target",
            )
        try:
            results = self.research_agent.research_structured(
                search_query=goal, max_results=self.max_results,
            )
        except Exception as error:
            return ActionPlanResult(
                "failed", f"I couldn't search for that: {error}",
                failure_code="web_search_failed",
            )
        summary = self._synthesize(goal, results)
        steps_taken = tuple(
            f"{item.get('title', '')} ({item.get('url', '')})" for item in results
        )
        return ActionPlanResult("done", summary, steps_taken=steps_taken)

    def _synthesize(
        self, goal: str, results: tuple[dict[str, str], ...],
    ) -> str:
        formatted = "\n".join(
            f"- {item.get('title', '')}: {item.get('summary', '')} "
            f"({item.get('url', '')})"
            for item in results
        )
        try:
            response = self.client.chat(
                model=self.model,
                messages=[{
                    "role": "system",
                    "content": _SYNTHESIS_PROMPT.format(goal=goal, results=formatted),
                }],
                stream=False,
                options={"temperature": 0, "num_predict": 150},
                keep_alive=self.keep_alive,
                think=False,
            )
            message = self._value(response, "message", {})
            content = str(self._value(message, "content", "")).strip()
            if content:
                return content
        except Exception as error:
            print(
                "[Web Search Planner] Synthesis failed safely: "
                f"{type(error).__name__}: {error}"
            )
        # Fall back to the top result's own title/summary rather than
        # failing the whole step over a synthesis-call hiccup -- the
        # search itself already succeeded.
        if results:
            top = results[0]
            return f"{top.get('title', '')}: {top.get('summary', '')}".strip(": ")
        return "The search returned no usable results."

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
