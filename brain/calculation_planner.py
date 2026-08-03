from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tools.calculator import CalculationError, CalculationStep, evaluate_expression


def _format_number(value: float) -> str:
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}"


# Asking the model to also make a final "grand total" tool call was tried and
# measured to destabilize the arithmetic it had already gotten right elsewhere
# in the same prompt (this model reliably follows about 3-4 simultaneous
# rules; a 5th one reliably breaks a different one). Instead of asking the
# model to sum its own verified components, the total is summed here in
# plain Python from steps that look like final per-item dollar amounts --
# 100% reliable, and it never touches the tool-calling prompt at all.
_COMPONENT_LABEL_KEYWORDS = (
    "cost", "share", "amount", "bill", "charge", "payment", "profit",
)
_TOTAL_LABEL_KEYWORDS = ("total", "grand", "combined", "sum")


def _is_component_amount_label(label: str) -> bool:
    lowered = label.lower()
    if any(keyword in lowered for keyword in _TOTAL_LABEL_KEYWORDS):
        return False
    return any(keyword in lowered for keyword in _COMPONENT_LABEL_KEYWORDS)


def _has_explicit_total(steps: tuple[CalculationStep, ...]) -> bool:
    return any(
        any(keyword in step.label.lower() for keyword in _TOTAL_LABEL_KEYWORDS)
        for step in steps
    )


@dataclass(frozen=True)
class CalculationPlan:
    """A word problem solved with one verified tool call per arithmetic step."""

    steps: tuple[CalculationStep, ...]

    @property
    def total_value(self) -> float:
        return self.steps[-1].value if self.steps else 0.0

    def as_trusted_result_text(self) -> str:
        lines = [
            f"{step.label}: {_format_number(step.value)}"
            for step in self.steps
        ]
        text = (
            "Verified calculation. Every number below was computed exactly "
            "by a calculator tool, one step at a time -- never estimated or "
            "recomputed mentally. Use these exact numbers; do not "
            "recompute, round differently, or alter them.\n"
            + "\n".join(lines)
        )

        if not _has_explicit_total(self.steps):
            components = [
                step for step in self.steps
                if _is_component_amount_label(step.label)
            ]
            if len(components) >= 2:
                computed_total = sum(step.value for step in components)
                text += (
                    "\nCombined total of the amounts above (computed "
                    "exactly, not estimated): "
                    f"{_format_number(computed_total)}"
                )

        return text


_CALCULATOR_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": (
            "Evaluate one plain arithmetic expression using only numbers "
            "and + - * / ( ), and return the exact numeric result. Call "
            "this for every arithmetic operation, including simple day "
            "counts -- never state a number derived from arithmetic "
            "without calling this first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Short description of what this computes",
                },
                "expression": {
                    "type": "string",
                    "description": (
                        "Plain arithmetic using only numbers and + - * / ( )"
                    ),
                },
            },
            "required": ["label", "expression"],
        },
    },
}

_SYSTEM_PROMPT = (
    "Solve this word problem step by step. Call the calculate tool for "
    "every single arithmetic operation -- including simple things like "
    "counting days (e.g. 21 - 12 + 1) -- and wait for the real result "
    "before deciding your next step. Never write a number in your own "
    "text that came from arithmetic you did not call the tool for first.\n"
    "For a billing or proration problem with events on specific days:\n"
    "- A change ON day N means the day-N-1 range ends and a new range "
    "starts ON day N (day N belongs to the NEW range, not the old one). "
    "Compute each range boundary with its own calculate call, for example "
    'if the next event is on day 12, call calculate("end of first range", '
    '"12 - 1") to get day 11, then calculate("days in first range", '
    '"11 - 1 + 1").\n'
    "- Track the quantity that changes at each event (e.g. user count) as "
    "its own running-total calculate call, in day order: the count after "
    "the second event must be calculated FROM the verified count after "
    'the first event (e.g. calculate("count after event 2", "8 - 1") '
    "using the 8 already verified from event 1), never recomputed from "
    "the original starting count. The range BEFORE an event uses the "
    "count BEFORE that event, not after it.\n"
    "- For each range, compute that range's rate using the running count "
    "verified for that specific range (base_rate + per_unit_rate * "
    "(count - included_limit)), then compute that range's prorated cost "
    "as its own call: (rate / cycle_length * days_in_range). Never "
    "multiply a full-cycle rate directly by a day count without dividing "
    "by the cycle length first.\n"
    "If a total amount (profit, pot, winnings, bill) is described as being "
    "split, shared, or distributed among parties in proportion to their "
    "contributions, that total IS the amount to divide up -- never add it "
    "to the contribution amounts as a separate quantity.\n"
    "If the request asks for a separate value for each person, item, or "
    "period, call the tool once per person/item so every value is "
    "individually verified -- never fold several requested amounts into "
    "one calculation.\n"
    "Once every needed number has come from the tool, respond in plain "
    "text with no further tool calls, stating every requested amount "
    "using only the verified tool results."
)

_STILL_WORKING_PATTERN = re.compile(
    r"\b(?:let'?s|we'?ll|i'?ll|now|next)\b.{0,15}\b"
    r"(?:calculate|compute|figure|work out|determine|find)\b"
    r"|\bwe (?:need|still need) to\b",
    flags=re.IGNORECASE,
)

_MAX_TOOL_ROUNDS = 20
_MAX_NUDGES = 2
_MAX_TOOL_ERRORS = 3


class CalculationPlanner:
    """
    Solves a word problem with one verified tool call per arithmetic step.

    Unlike asking the model to write a full plan of expressions before any
    arithmetic runs, the model here sees each real computed result before
    deciding its next step. A wrong running total or a misjudged date range
    shows up immediately instead of silently propagating through several
    steps the model only ever estimated in its head.
    """

    def __init__(self, client: Any, model: str, keep_alive: Any) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive

    def plan(self, request: str) -> CalculationPlan | None:
        messages: list[Any] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": request},
        ]
        steps: list[CalculationStep] = []
        nudges_used = 0
        errors_seen = 0

        for _ in range(_MAX_TOOL_ROUNDS):
            message = self._ask(messages)
            if message is None:
                return None

            tool_calls = self._value(message, "tool_calls", None)
            if tool_calls:
                messages.append(message)
                for call in tool_calls:
                    step, error_text = self._run_tool_call(call)
                    if step is not None:
                        steps.append(step)
                        messages.append({
                            "role": "tool",
                            "content": _format_number(step.value),
                        })
                        continue

                    errors_seen += 1
                    if errors_seen > _MAX_TOOL_ERRORS:
                        return None
                    messages.append({
                        "role": "tool",
                        "content": f"Error: {error_text}",
                    })
                continue

            content = str(self._value(message, "content", "") or "").strip()

            # Never accept a "final answer" backed by zero verified steps,
            # and never accept prose that is still narrating a future step
            # instead of actually calling the tool for it.
            if not steps or _STILL_WORKING_PATTERN.search(content):
                if nudges_used >= _MAX_NUDGES:
                    return None
                nudges_used += 1
                messages.append(message)
                messages.append({
                    "role": "user",
                    "content": (
                        "Call the calculate tool for your next arithmetic "
                        "step instead of describing it in text."
                    ),
                })
                continue

            if not content:
                return None
            return CalculationPlan(steps=tuple(steps))

        return None

    def _ask(self, messages: list[Any]) -> Any:
        try:
            response = self.client.chat(
                model=self.model,
                messages=messages,
                tools=[_CALCULATOR_TOOL],
                stream=False,
                options={
                    "temperature": 0,
                    "num_predict": 300,
                },
                keep_alive=self.keep_alive,
                think=False,
            )
        except Exception as error:
            print(
                "[Calculation Planner] Request failed: "
                f"{type(error).__name__}: {error}"
            )
            return None
        return self._value(response, "message", None)

    def _run_tool_call(
        self,
        call: Any,
    ) -> tuple[CalculationStep | None, str]:
        function = self._value(call, "function", {})
        arguments = self._value(function, "arguments", {})
        label = str(self._value(arguments, "label", "")).strip()
        expression = str(self._value(arguments, "expression", "")).strip()

        if not label or not expression:
            message = "Each call needs a non-empty label and expression."
            print(f"[Calculation Planner] {message}")
            return None, message

        try:
            value = evaluate_expression(expression)
        except CalculationError as error:
            print(f"[Calculation Planner] {error}")
            return None, str(error)

        return (
            CalculationStep(label=label, expression=expression, value=value),
            "",
        )

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
