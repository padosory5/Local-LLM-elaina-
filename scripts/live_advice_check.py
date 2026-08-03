"""Check short, actionable advice against Elaina's configured Ollama model.

This is read-only. It does not start the microphone, Electron, memory, or tools.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.personality_loader import PersonalityLoader  # noqa: E402
from brain.response_messages import build_personality_messages  # noqa: E402
from brain.response_policy import ResponseLimits  # noqa: E402
from brain.text_filter import TextFilter  # noqa: E402
from config.loader import Config  # noqa: E402


@dataclass(frozen=True)
class Case:
    name: str
    prompt: str
    evidence: str = ""
    expected_any: tuple[str, ...] = ()
    advice_domain: str = "general"


CASES = (
    Case(
        name="sleep option",
        prompt="I'm having trouble sleeping. What can I take?",
        evidence=(
            "Healthy sleep habits and CBT-I are recommended first for persistent "
            "insomnia. Melatonin may help some sleep-timing problems and appears "
            "relatively safe for short-term adult use, but long-term safety is "
            "uncertain. Avoid combining sleep aids with alcohol."
        ),
        expected_any=("melatonin", "sleep habit", "sleep schedule", "bedtime"),
        advice_domain="health",
    ),
    Case(
        name="melatonin follow-up",
        prompt="What about melatonin?",
        evidence=(
            "Melatonin is sold as a dietary supplement in the United States. It "
            "may help jet lag and delayed sleep timing more than chronic insomnia. "
            "Use label directions; possible effects include sleepiness, headache, "
            "dizziness, and nausea, and it can interact with medicines."
        ),
        expected_any=("melatonin",),
        advice_domain="health",
    ),
    Case(
        name="avatar recommendation",
        prompt=(
            "Should I use Live2D or a 3D model for my local LLM avatar? I want "
            "something expressive that is not too difficult to build."
        ),
        expected_any=("live2d", "3d"),
    ),
)


def value(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def contains_routine_referral(text: str) -> bool:
    lowered = text.casefold()
    return any(
        phrase in lowered
        for phrase in ("doctor", "healthcare", "medical professional", "expert")
    )


def contains_numeric_dose(text: str) -> bool:
    return bool(re.search(
        r"\b\d+(?:\.\d+)?\s*(?:mcg|mg|g|ml|micrograms?|milligrams?|"
        r"grams?|milliliters?)\b",
        text,
        flags=re.IGNORECASE,
    ))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        import ollama
    except ModuleNotFoundError:
        print("Activate Elaina's .venv before running this check.")
        return 2

    config = Config()
    model = str(config.get("llm", "ollama", "model"))
    client = ollama.Client(host=str(config.get("llm", "ollama", "base_url")))
    keep_alive = config.get(
        "llm", "ollama", "keep_alive", default=-1, required=False
    )
    language = str(config.get(
        "language", "response", default="en", required=False
    ))
    personality = PersonalityLoader().load(language)
    limits = ResponseLimits(
        max_words=int(config.get(
            "responses", "max_words", default=45, required=False
        )),
        max_sentences=int(config.get(
            "responses", "max_sentences", default=2, required=False
        )),
    )
    instruction = limits.instruction(recommendation=True)
    failures = 0

    print(f"Testing {model} with {len(CASES)} advice cases.\n")
    for case in CASES:
        context = ()
        if case.evidence:
            context = (("CURRENT RETRIEVED EVIDENCE", case.evidence),)
        messages = build_personality_messages(
            system_prompt=personality,
            history=[],
            user_input=case.prompt,
            context_sections=context,
        )
        messages[-1]["content"] += (
            "\n\nVOICE RESPONSE REQUIREMENTS\n" + instruction
        )

        try:
            response = client.chat(
                model=model,
                messages=messages,
                stream=False,
                options={
                    "temperature": 0.2,
                    "num_predict": limits.generation_budget(),
                },
                keep_alive=keep_alive,
                think=False,
            )
        except Exception as error:
            print(
                "Could not reach the configured Ollama model: "
                f"{type(error).__name__}: {error}"
            )
            return 2

        reply = TextFilter.for_voice_response(
            value(value(response, "message", {}), "content", "")
        )
        invalid_health_dose = (
            case.advice_domain == "health" and contains_numeric_dose(reply)
        )
        if (
            limits.exceeds(reply)
            or contains_routine_referral(reply)
            or invalid_health_dose
        ):
            rewrite_messages = build_personality_messages(
                system_prompt=personality,
                history=[],
                user_input=case.prompt,
                context_sections=(
                    ("DRAFT ANSWER", reply),
                    (
                        "VOICE RESPONSE REQUIREMENTS",
                        instruction
                        + " Rewrite the draft. Preserve the direct "
                        "recommendation, immediate action, and essential "
                        "caution; remove background first. This is routine "
                        "advice, so remove any suggestion to see or consult a "
                        "doctor, expert, or professional. Ask for one missing "
                        "safety detail instead when necessary. For health "
                        "advice, do not invent a numeric dose; use label "
                        "directions instead.",
                    ),
                ),
            )
            rewrite = client.chat(
                model=model,
                messages=rewrite_messages,
                stream=False,
                options={
                    "temperature": 0.1,
                    "num_predict": limits.generation_budget(),
                },
                keep_alive=keep_alive,
                think=False,
            )
            candidate = TextFilter.for_voice_response(
                value(value(rewrite, "message", {}), "content", "")
            )
            candidate_valid = (
                candidate
                and not limits.exceeds(candidate)
                and not contains_routine_referral(candidate)
                and not (
                    case.advice_domain == "health"
                    and contains_numeric_dose(candidate)
                )
            )
            if candidate_valid:
                reply = candidate
            else:
                finalizer = client.chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": personality},
                        {
                            "role": "user",
                            "content": (
                                "Return only the final voice reply in exactly "
                                "two short sentences under 45 words. Sentence "
                                "one gives the direct recommendation. Sentence "
                                "two gives the immediate action and at most one "
                                "essential caution. This is routine advice: do "
                                "not mention a doctor, expert, or professional.\n\n"
                                "For health advice, do not give a numeric dose; "
                                "use label directions instead.\n\n"
                                f"USER REQUEST\n{case.prompt}\n\n"
                                f"DRAFT\n{reply}"
                            ),
                        },
                    ],
                    stream=False,
                    options={"temperature": 0, "num_predict": 180},
                    keep_alive=keep_alive,
                    think=False,
                )
                final_candidate = TextFilter.for_voice_response(
                    value(value(finalizer, "message", {}), "content", "")
                )
                if (
                    final_candidate
                    and not limits.exceeds(final_candidate)
                    and not contains_routine_referral(final_candidate)
                    and not (
                        case.advice_domain == "health"
                        and contains_numeric_dose(final_candidate)
                    )
                ):
                    reply = final_candidate
        reply = limits.merge_extra_sentences(reply)
        lowered = reply.casefold()
        mentions_option = any(
            expected in lowered for expected in case.expected_any
        )
        within_limits = not limits.exceeds(reply)
        routine_referral = contains_routine_referral(reply)
        numeric_dose = (
            case.advice_domain == "health" and contains_numeric_dose(reply)
        )
        passed = (
            bool(reply)
            and mentions_option
            and within_limits
            and not routine_referral
            and not numeric_dose
        )
        failures += 0 if passed else 1

        print(f"[{'PASS' if passed else 'FAIL'}] {case.name}")
        print(f"Prompt: {case.prompt}")
        print(f"Elaina: {reply}")
        print(
            "Checks: "
            f"option={mentions_option}, within_limits={within_limits}, "
            f"routine_referral={routine_referral}, "
            f"numeric_dose={numeric_dose}\n"
        )

    print(f"Result: {len(CASES) - failures}/{len(CASES)} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
