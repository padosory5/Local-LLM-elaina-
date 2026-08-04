"""Run representative answer-completion cases against the configured Ollama.

This is read-only. It does not start the microphone, Electron, agents, memory,
or external tools.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.personality_loader import PersonalityLoader
from brain.calculation_planner import CalculationPlanner
from brain.response_messages import build_personality_messages
from brain.response_policy import AnswerCompletionGuard, ResponseLimits
from brain.text_filter import TextFilter
from scripts.console_style import status_label


@dataclass(frozen=True)
class Case:
    name: str
    prompt: str
    expected_numbers: tuple[str, ...]


CASES = (
    Case(
        name="proportional gambling distribution",
        prompt=(
            "I put in 100 dollars, one friend put in 100 dollars, another "
            "friend put in 50 dollars, and the final total was 650 dollars. "
            "How much should each person receive if it is split "
            "proportionally, and how much profit did I make?"
        ),
        expected_numbers=("260", "130", "160"),
    ),
    Case(
        name="restaurant bill and tip",
        prompt=(
            "A restaurant bill is 80 dollars, the tip is 20 percent, and "
            "three people split the final total equally. How much does each "
            "person pay?"
        ),
        expected_numbers=("32",),
    ),
    Case(
        name="discounted price",
        prompt=(
            "A jacket costs 80 dollars and is discounted by 25 percent. "
            "What is the final price?"
        ),
        expected_numbers=("60",),
    ),
    Case(
        name="travel time",
        prompt=(
            "A car travels 150 kilometers at 50 kilometers per hour. "
            "How many hours does the trip take?"
        ),
        expected_numbers=("3",),
    ),
)


def value(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def main() -> int:
    try:
        import ollama
        from config.loader import Config
    except ModuleNotFoundError:
        print(
            "Elaina's Python packages are not installed in this environment. "
            "Activate Elaina's .venv and run pip install -r requirements.txt."
        )
        return 2

    config = Config()
    model = str(config.get("llm", "ollama", "model"))
    host = str(config.get("llm", "ollama", "base_url"))
    keep_alive = config.get(
        "llm", "ollama", "keep_alive", default=-1, required=False
    )
    language = str(config.get(
        "language", "response", default="en", required=False
    ))
    limits = ResponseLimits(
        max_words=int(config.get(
            "responses", "max_words", default=45, required=False
        )),
        max_sentences=int(config.get(
            "responses", "max_sentences", default=2, required=False
        )),
    )
    personality = PersonalityLoader().load(language)
    client = ollama.Client(host=host)
    planner = CalculationPlanner(
        client=client,
        model=model,
        keep_alive=keep_alive,
    )
    failures = 0

    print(f"Testing {model} with {len(CASES)} calculation cases.\n")
    for case in CASES:
        instruction = limits.instruction(calculation=True)
        plan = planner.plan(case.prompt)
        if plan is None:
            failures += 1
            print(f"[{status_label(False)}] {case.name}")
            print("The calculation planner returned no verified result.\n")
            continue
        messages = build_personality_messages(
            system_prompt=personality,
            history=[],
            user_input=case.prompt,
            context_sections=((
                "TRUSTED TOOL RESULT",
                plan.as_trusted_result_text(),
            ),),
        )
        messages[-1]["content"] += (
            "\n\nVOICE RESPONSE REQUIREMENTS\n"
            f"{instruction}"
        )
        try:
            response = client.chat(
                model=model,
                messages=messages,
                stream=False,
                options={
                    "temperature": 0.1,
                    "num_predict": limits.generation_budget(calculation=True),
                },
                keep_alive=keep_alive,
                think=False,
            )
        except Exception as error:
            print(
                "Could not reach the configured Ollama model: "
                f"{type(error).__name__}: {error}"
            )
            print("Start Ollama and verify the model in config/config.yaml.")
            return 2
        message = value(response, "message", {})
        reply = TextFilter.for_voice_response(
            value(message, "content", "")
        )
        if AnswerCompletionGuard.needs_retry(reply, calculation=True):
            retry = client.chat(
                model=model,
                messages=[
                    *messages,
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": (
                            "The draft did not provide the requested result. "
                            "Do the calculation now, give every final amount "
                            "first, and do not ask permission."
                        ),
                    },
                ],
                stream=False,
                options={
                    "temperature": 0.1,
                    "num_predict": limits.generation_budget(calculation=True),
                },
                keep_alive=keep_alive,
                think=False,
            )
            reply = TextFilter.for_voice_response(
                value(value(retry, "message", {}), "content", "")
            )

        if limits.exceeds(reply):
            rewrite = client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": personality},
                    {
                        "role": "user",
                        "content": (
                            f"CURRENT USER MESSAGE\n{case.prompt}\n\n"
                            f"DRAFT ANSWER\n{reply}\n\n"
                            "VOICE RESPONSE REQUIREMENTS\n"
                            f"{instruction} Rewrite the complete draft within "
                            "the limits without removing any requested result."
                        ),
                    },
                ],
                stream=False,
                options={
                    "temperature": 0.1,
                    "num_predict": limits.generation_budget(calculation=True),
                },
                keep_alive=keep_alive,
                think=False,
            )
            candidate = TextFilter.for_voice_response(
                value(value(rewrite, "message", {}), "content", "")
            )
            if (
                candidate
                and not AnswerCompletionGuard.needs_retry(
                    candidate,
                    calculation=True,
                )
                and not limits.exceeds(candidate)
            ):
                reply = candidate
        has_numbers = all(number in reply for number in case.expected_numbers)
        complete = not AnswerCompletionGuard.needs_retry(
            reply,
            calculation=True,
        )
        within_limits = not limits.exceeds(reply)
        passed = has_numbers and complete and within_limits
        failures += 0 if passed else 1

        print(f"[{status_label(passed)}] {case.name}")
        print(f"Prompt: {case.prompt}")
        print(f"Elaina: {reply}")
        print(
            "Checks: "
            f"numbers={has_numbers}, complete={complete}, "
            f"within_limits={within_limits}\n"
        )

    print(f"Result: {len(CASES) - failures}/{len(CASES)} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
