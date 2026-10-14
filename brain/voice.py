"""Which way she talks: as an assistant, or as a person.

The user, rating the English simulated learners (2026-10-14): helpful in
six conversations of six, and "Elaina still feels too much of an AI" -- a
person explaining quantization says "think of it like..." and "ya, it's easy
to think that way, but...", not "Model quantization reduces the number of
bits... This makes the model smaller... For example...". That register was
asked for in so many words: personality_en.txt opens its voice with "Speak
like a professional assistant" and "Friendly, never familiar... not the
user's buddy", and every explanation turn added "say why it happens, what it
is for, and show one worked example".

``person`` is the other register, kept beside the first so the two can be
compared before one replaces the other:

* ``personality_en_person.txt`` -- the same character, the same honesty,
  advice, tool and safety rules word for word; the voice, the explanation
  rule, venting and two example exchanges rewritten (tests/test_voice.py
  holds the rest to the original).
* the explanation goal (brain/response_budget.py) -- start from something
  they know or a simple comparison, then the point in plain words, instead
  of the why / what-for / worked-example checklist.

English only. Korean keeps its own file and goal until the user brings
Korean back (it is left out of the work for now).

config.yaml ``responses.voice``; ELAINA_VOICE overrides it.
"""

from __future__ import annotations

import os

ENV = "ELAINA_VOICE"
ASSISTANT = "assistant"
PERSON = "person"
VOICES = (ASSISTANT, PERSON)


def configured(config) -> str:
    """The voice: ELAINA_VOICE, else config.yaml ``responses.voice``, else the
    assistant. An unknown value is said out loud and read as the assistant --
    her own use must still start."""
    value = os.environ.get(ENV)
    if value is None:
        try:
            value = config.get("responses", "voice", default=ASSISTANT, required=False)
        except Exception:
            value = ASSISTANT
    chosen = str(value or ASSISTANT).strip().lower()
    if chosen not in VOICES:
        print(f"[Voice] voice={value!r} is not one of {VOICES}; talking as the {ASSISTANT}")
        return ASSISTANT
    return chosen
