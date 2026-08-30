"""The last step of the cascade, and only ever the last.

Some constraints are settled exactly by string and number handling --
electric or acoustic, under 500,000 won, in Seoul, on the 18th. Those never
reach this module, because asking a model to compare two numbers is a
round-trip spent on something arithmetic already answered.

Others cannot be. "Soft", "easy to eat", "quiet enough to study in",
"beginner-friendly" are real constraints that a search result rarely states
in those words, and a restaurant listing that says nothing about texture is
not thereby evidence that its food is soft. Measured live, three UNCHECKED
restaurants for a sore throat produced a confident recommendation of Korean
BBQ -- because nothing distinguished "no evidence against" from "evidence
for".

So this answers exactly one question, about candidates that survived every
deterministic check, for constraints that could not be resolved any other
way:

    does this candidate satisfy this quality -- yes, no, or unknown?

"Unknown" is a first-class answer and the default on anything going wrong.
A failed or unparseable call leaves the candidates exactly as they were,
which keeps the honest "I could not establish this" outcome rather than
inventing a verdict.
"""

from __future__ import annotations

import json
import re

# Small on purpose. This is a judgement about a handful of short strings,
# not a research task, and it sits inside a turn the person is waiting on.
MAX_CANDIDATES = 5
_MAX_TOKENS = 200

_PROMPT = (
    "Decide whether each option plausibly satisfies one requirement.\n\n"
    "REQUIREMENT: {constraint}\n\n"
    "OPTIONS:\n{options}\n\n"
    "Answer only from what the option's own name and description support, "
    "plus ordinary knowledge of what such a place or product is like. "
    "Answer \"yes\" only when it clearly does satisfy the requirement, "
    "\"no\" when it clearly does not, and \"unknown\" when there is not "
    "enough to tell. Guessing is worse than \"unknown\".\n\n"
    "Return JSON only, no other text:\n"
    "{{\"verdicts\": [{{\"n\": <option number>, "
    "\"answer\": \"yes\"|\"no\"|\"unknown\"}}]}}"
)


def _parse(text: str, names: list[str]) -> dict[str, str]:
    raw = str(text or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except Exception:
        return {}
    verdicts: dict[str, str] = {}
    for entry in data.get("verdicts", []) or ():
        try:
            index = int(entry.get("n"))
        except Exception:
            continue
        answer = str(entry.get("answer", "")).strip().casefold()
        if answer not in {"yes", "no", "unknown"}:
            continue
        if 1 <= index <= len(names):
            verdicts[names[index - 1]] = answer
    return verdicts


def check(client, model: str, fits, constraint: str) -> dict[str, str]:
    """Ask once whether these candidates meet this one quality.

    Returns a name -> "yes" / "no" / "unknown" mapping, empty when the call
    could not be made or could not be read. Never raises: an unavailable
    model must leave the recommendation honest rather than break the turn.
    """
    constraint = " ".join(str(constraint or "").split())
    shortlist = list(fits)[:MAX_CANDIDATES]
    if not constraint or not shortlist or client is None:
        return {}
    names = [fit.name for fit in shortlist]
    options = "\n".join(
        f"{index}. {fit.name}"
        + (f" -- {fit.summary[:160]}" if fit.summary else "")
        for index, fit in enumerate(shortlist, start=1)
    )
    prompt = _PROMPT.format(constraint=constraint, options=options)
    try:
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            # Without this the model spends the whole budget on a think
            # block and returns empty content -- measured: every semantic
            # check came back unresolved for that reason alone, which read
            # exactly like "the evidence was not there".
            think=False,
            options={"temperature": 0, "num_predict": _MAX_TOKENS},
        )
    except Exception as error:
        print(
            f"[Recommendation Reasoning]\n  Decision: leave unresolved"
            f"\n  Why: the semantic check could not run ({type(error).__name__})"
        )
        return {}
    content = ""
    try:
        content = response["message"]["content"]
    except Exception:
        content = str(getattr(
            getattr(response, "message", None), "content", "",
        ) or "")
    return _parse(content, names)
