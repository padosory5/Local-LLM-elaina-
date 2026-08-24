"""Goal-level task planner: Phase 4D-1 foundation + 4D-2 recovery.

Composes Elaina's existing single-ability planners (DesktopActionPlanner,
BrowserActionPlanner) into multi-step tasks. This is a second, higher tier
above them -- it never sees their tool lists, only capability names and
one-sentence sub-goals; each sub-goal is handed to the matching tier-2
planner's own proven, bounded, verified .act() loop unchanged.

Goal -> Capability Check -> Plan -> Execute -> Observe -> Update State ->
Replan if Necessary -> Complete -> Stop, one step at a time -- not a rigid
upfront plan, since real execution requires observing real results (you
don't know which hotels exist until you've searched) before deciding the
next step.

4D-2: a failed step does not end the task by itself -- it folds into
history like any other step, and the next planning call sees it and
decides whether to retry differently, switch capability, or give up.
_MAX_CONSECUTIVE_FAILURES bounds that against an unproductive loop; any
successful step resets the count, so one bad step never taints an
otherwise-progressing task. Structured extraction/comparison (4D-3) and
risk classification (4D-5) are deliberately still not built here -- this
pass only has to additionally pass 4D-2's own bar: a task that hits one
recoverable failure keeps going and still finishes, while one that hits
nothing but dead ends stops cleanly instead of spinning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agents.preconditions import check_precondition
from tools.computer_control.computer_control import PreparedComputerAction

_MAX_STEPS_DEFAULT = 8
# A failed step no longer ends the task outright -- it flows back into the
# next planning call so the model can see what happened and choose to retry
# differently, switch capability, or give up. This bound only guards against
# an unproductive loop of back-to-back failures; a success anywhere resets
# it, so one bad step never taints an otherwise-progressing task.
_MAX_CONSECUTIVE_FAILURES = 2

# Which precondition (see agents/preconditions.py) gates each capability.
# Checked lazily, right before a step is first dispatched to that
# capability -- so "here's why I can't do this" falls out of the same
# infrastructure already built for it, without a separate upfront LLM call
# to enumerate every capability a goal might eventually need.
_CAPABILITY_PRECONDITIONS = {
    "ui_control": "computer_control_mode_enabled",
    "browser_control": "browser_page_control_enabled",
}


@dataclass(frozen=True)
class TaskStep:
    capability: str
    sub_goal: str
    rationale: str = ""


@dataclass(frozen=True)
class TaskStepResult:
    step: TaskStep
    status: str  # "done" | "needs_confirmation" | "failed"
    summary: str = ""
    # Freeform text folded into collected_information. Structured
    # extraction into a common representation is 4D-3's job, not this pass's.
    info: str = ""
    failure_code: str = ""


@dataclass
class TaskState:
    goal: str
    status: str = "in_progress"
    completed_steps: list[TaskStepResult] = field(default_factory=list)
    current_capability: str = ""
    current_application: str = ""
    collected_information: list[str] = field(default_factory=list)
    preferences: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    step_count: int = 0
    consecutive_failures: int = 0


@dataclass(frozen=True)
class TaskRunResult:
    status: str  # "done"|"failed"|"needs_confirmation"|"capability_unavailable"|"stopped"
    summary: str = ""
    task_state: TaskState = field(default_factory=lambda: TaskState(goal=""))
    pending_step: TaskStep | None = None
    pending_capability: str = ""
    pending_prepared: PreparedComputerAction | None = None


class TaskPlanner:
    """Run one multi-step goal to completion, confirmation, or a safe stop."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        keep_alive: Any = -1,
        agent_registry: Any,
        desktop_action_planner: Any,
        browser_action_planner: Any,
        computer_control_mode: Any = None,
        browser_control_enabled: bool = True,
        max_steps: int = _MAX_STEPS_DEFAULT,
        response_language: str = "en",
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive
        self.agent_registry = agent_registry
        self.executors: dict[str, Any] = {
            "ui_control": desktop_action_planner,
            "browser_control": browser_action_planner,
        }
        self.max_steps = int(max_steps)
        self.response_language = response_language
        self._precondition_context = {
            "computer_control_mode": computer_control_mode,
            "browser_control_enabled": browser_control_enabled,
        }

    def run(self, goal: str) -> TaskRunResult:
        task_state = TaskState(goal=str(goal).strip())
        return self._advance(task_state)

    def resume(
        self,
        task_state: TaskState,
        *,
        approved_action: PreparedComputerAction,
        step: TaskStep | None = None,
    ) -> TaskRunResult:
        capability = task_state.current_capability
        executor = self.executors.get(capability)
        if executor is None:
            task_state.status = "failed"
            return TaskRunResult("failed", "I lost track of that task.", task_state)
        step_result = self._resume_step(capability, executor, approved_action, step)
        task_state = self._fold_result(task_state, step_result)
        if (
            step_result.status != "done"
            and task_state.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES
        ):
            task_state.status = "failed"
            return TaskRunResult("failed", step_result.summary, task_state)
        return self._advance(task_state)

    def _advance(self, task_state: TaskState) -> TaskRunResult:
        while task_state.step_count < self.max_steps:
            decision = self._plan_next(task_state)
            if decision is None:
                task_state.status = "failed"
                return TaskRunResult(
                    "failed", "I couldn't plan the next step.", task_state,
                )
            if decision.get("done"):
                summary = str(decision.get("summary", "")).strip() or "Done."
                task_state.status = "done"
                return TaskRunResult("done", summary, task_state)
            if decision.get("stop"):
                reason = str(decision.get("reason", "")).strip() or (
                    "I couldn't make further progress on that."
                )
                task_state.status = "stopped"
                return TaskRunResult("stopped", reason, task_state)

            capability = str(decision.get("capability", "")).strip()
            sub_goal = str(decision.get("sub_goal", "")).strip()
            if capability not in self.executors or not sub_goal:
                task_state.status = "failed"
                return TaskRunResult(
                    "failed", "I couldn't work out a valid next step.", task_state,
                )

            precondition_name = _CAPABILITY_PRECONDITIONS.get(capability)
            if precondition_name:
                ok, message = check_precondition(
                    precondition_name, **self._precondition_context,
                )
                if not ok:
                    task_state.status = "failed"
                    return TaskRunResult("capability_unavailable", message, task_state)

            step = TaskStep(
                capability=capability,
                sub_goal=sub_goal,
                rationale=str(decision.get("rationale", "")).strip(),
            )
            task_state.step_count += 1
            task_state.current_capability = capability
            step_result, prepared = self._run_step(step, self.executors[capability])
            task_state = self._fold_result(task_state, step_result)

            if step_result.status == "needs_confirmation":
                task_state.status = "needs_confirmation"
                return TaskRunResult(
                    "needs_confirmation", step_result.summary, task_state,
                    pending_step=step, pending_capability=capability,
                    pending_prepared=prepared,
                )
            if step_result.status == "failed":
                if task_state.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    task_state.status = "failed"
                    return TaskRunResult("failed", step_result.summary, task_state)
                # Otherwise loop back to _plan_next: the failure is now part
                # of the history it's given, so the model can retry with a
                # different sub_goal, switch capability, or stop on its own.
                continue

        task_state.status = "stopped"
        return TaskRunResult(
            "stopped", "I stopped after the safe planning limit.", task_state,
        )

    def _run_step(
        self, step: TaskStep, executor: Any,
    ) -> tuple[TaskStepResult, PreparedComputerAction | None]:
        try:
            plan_result = executor.act(step.sub_goal)
        except Exception as error:
            return TaskStepResult(
                step, "failed", summary=f"That step failed: {error}",
            ), None
        if step.capability == "ui_control":
            return self._from_desktop_result(step, plan_result)
        return self._from_browser_result(step, plan_result)

    @staticmethod
    def _from_desktop_result(
        step: TaskStep, plan_result: Any,
    ) -> tuple[TaskStepResult, PreparedComputerAction | None]:
        if plan_result.status == "needs_confirmation":
            pending = plan_result.pending
            prepared = PreparedComputerAction(
                operation="ui_action",
                target=pending.control_name,
                display_name=pending.control_name,
                window_title=pending.window_title,
                window_snapshot=pending.window_snapshot,
            )
            return TaskStepResult(
                step, "needs_confirmation", summary=plan_result.summary,
            ), prepared
        status = "done" if plan_result.status == "done" else "failed"
        return TaskStepResult(
            step, status, summary=plan_result.summary, info=plan_result.summary,
            failure_code=plan_result.failure_code,
        ), None

    @staticmethod
    def _from_browser_result(
        step: TaskStep, plan_result: Any,
    ) -> tuple[TaskStepResult, PreparedComputerAction | None]:
        if plan_result.status == "needs_confirmation":
            pending = plan_result.pending
            prepared = PreparedComputerAction(
                operation="browser_action",
                target=pending.element_id,
                display_name=pending.element_label or pending.element_id,
                tab_index=pending.tab_index,
                url=pending.url,
                browser_action=pending.action,
                browser_text=pending.text,
                browser_scan_id=pending.scan_id,
                browser_href=pending.href,
            )
            return TaskStepResult(
                step, "needs_confirmation", summary=plan_result.summary,
            ), prepared
        status = "done" if plan_result.status == "done" else "failed"
        return TaskStepResult(
            step, status, summary=plan_result.summary, info=plan_result.summary,
            failure_code=plan_result.failure_code,
        ), None

    @staticmethod
    def _resume_step(
        capability: str,
        executor: Any,
        approved_action: PreparedComputerAction,
        step: TaskStep | None,
    ) -> TaskStepResult:
        fallback_step = step or TaskStep(
            capability=capability, sub_goal=approved_action.target,
        )
        try:
            if capability == "ui_control":
                plan_result = executor.resume_confirmed_click(
                    window_title=approved_action.window_title,
                    control_name=approved_action.display_name,
                    window_snapshot=approved_action.window_snapshot,
                )
            elif capability == "browser_control" and hasattr(
                executor, "resume_confirmed_action",
            ):
                plan_result = executor.resume_confirmed_action(
                    tab_index=approved_action.tab_index,
                    element_id=approved_action.target,
                    element_label=approved_action.display_name,
                    action=approved_action.browser_action or "click",
                    text=approved_action.browser_text,
                    expected_url=approved_action.url,
                    expected_scan_id=approved_action.browser_scan_id,
                    expected_href=approved_action.browser_href,
                )
            elif capability == "browser_control":
                plan_result = executor.resume_confirmed_click(
                    tab_index=approved_action.tab_index or 0,
                    element_id=approved_action.target,
                    element_label=approved_action.display_name,
                )
            else:
                return TaskStepResult(
                    fallback_step, "failed", summary="Unknown capability.",
                )
        except Exception as error:
            return TaskStepResult(
                fallback_step, "failed", summary=f"That step failed: {error}",
            )
        status = "done" if plan_result.status == "done" else "failed"
        return TaskStepResult(
            fallback_step, status, summary=plan_result.summary,
            info=plan_result.summary,
            failure_code=getattr(plan_result, "failure_code", ""),
        )

    @staticmethod
    def _fold_result(task_state: TaskState, step_result: TaskStepResult) -> TaskState:
        task_state.completed_steps.append(step_result)
        if step_result.info:
            task_state.collected_information.append(step_result.info)
        if step_result.failure_code:
            task_state.errors.append(
                f"{step_result.step.capability}: {step_result.failure_code}",
            )
        if step_result.status == "failed":
            task_state.consecutive_failures += 1
        elif step_result.status == "done":
            task_state.consecutive_failures = 0
        return task_state

    def _plan_next(self, task_state: TaskState) -> dict[str, Any] | None:
        prompt = self._build_prompt(task_state)
        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": task_state.goal},
                ],
                stream=False,
                format="json",
                options={"temperature": 0, "num_predict": 300},
                keep_alive=self.keep_alive,
                think=False,
            )
            message = self._value(response, "message", {})
            payload = json.loads(str(self._value(message, "content", "")))
            return payload if isinstance(payload, dict) else None
        except Exception as error:
            print(
                "[Task Planner] Planning call failed: "
                f"{type(error).__name__}: {error}"
            )
            return None

    def _build_prompt(self, task_state: TaskState) -> str:
        capabilities = "\n".join(
            f"- {name}: {self._capability_description(name)}"
            for name in sorted(self.executors)
        )
        history = "\n".join(
            f"- [{result.step.capability}] {result.step.sub_goal} -> "
            f"{result.status}: {result.summary}"
            for result in task_state.completed_steps
        ) or "(none yet)"
        info = "\n".join(
            f"- {item}" for item in task_state.collected_information
        ) or "(none yet)"
        return (
            "You are Elaina's task planner. Decide the SINGLE next step "
            "toward the goal, one capability-scoped sub-goal at a time -- "
            "never a full upfront plan, since results must be observed "
            "before deciding what comes next.\n"
            f"Available capabilities:\n{capabilities}\n"
            "Describe sub_goal in plain language for that capability's own "
            "planner to carry "
            "out -- never name a specific tool, button, or control "
            "yourself; the capability's own planner resolves those against "
            "the real, live application or page.\n"
            "A step can fail. When the most recent entry below is a "
            "failure, read its summary and decide: retry with a "
            "meaningfully different sub_goal (not the same wording), try a "
            "different capability if one could also make progress, or "
            "return {\"stop\": true, \"reason\": ...} if nothing left is "
            "likely to work. "
            f"Consecutive failed steps right now: "
            f"{task_state.consecutive_failures} (the task stops itself "
            f"after {_MAX_CONSECUTIVE_FAILURES} in a row with no success "
            "in between), so do not repeat a failing approach unchanged.\n"
            "Return JSON only, exactly one shape:\n"
            '{"done": true, "summary": "<final answer for the user, '
            'grounded only in what was actually observed below>"}\n'
            '{"stop": true, "reason": "<why you cannot make further '
            'progress>"}\n'
            '{"capability": "<one of the available capabilities>", '
            '"sub_goal": "<one sentence>", "rationale": "<short, why this '
            'step now>"}\n'
            f"Goal: {task_state.goal}\n"
            f"Steps completed so far:\n{history}\n"
            f"Information collected so far:\n{info}\n"
            "User preferences: "
            f"{json.dumps(task_state.preferences, ensure_ascii=False)}\n"
            "Only report done once the goal is genuinely satisfied by what "
            "was actually observed above -- never invent a result you "
            "didn't see."
        )

    def _capability_description(self, name: str) -> str:
        definition = None
        if self.agent_registry is not None:
            get = getattr(self.agent_registry, "get", None)
            definition = get(name) if callable(get) else None
        if definition is None:
            return name
        use_when = "; ".join(getattr(definition, "use_when", ()) or ())
        avoid_when = "; ".join(getattr(definition, "avoid_when", ()) or ())
        parts = [definition.description]
        if use_when:
            parts.append(f"Use when: {use_when}.")
        if avoid_when:
            parts.append(f"Avoid when: {avoid_when}.")
        return " ".join(parts)

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
