"""FAST/DEEP routing acted on -- for an evaluation arm, never for her own use.

With ELAINA_FASTDEEP_ROUTE unset, which is how she always runs, the policy
is record-only: ``words_model`` is "" for every turn, the shadow router
(brain/fastdeep_router.py) stays a record, and every turn is answered FAST.
Nothing in config.yaml sets it.

Corpus v2 (docs/FAST_DEEP_V2_PROTOCOL.md) needs what the SPLIT arm could not
give: FAST writing the earlier turns, the 27B writing a later one with
FAST's answers as its history, the turn after that back on the 8B, and the
real cost of each switch. Two arms set it in the backend's environment:

* ROUTED (``router``): the 27B writes the turns the shadow router decides
  DEEP -- the routed system itself;
* LATE (``after_first``): the 27B writes every turn after the
  conversation's first, whatever the router decided -- for each later turn,
  what sending it to DEEP would have produced.

Both switch at the same point of the turn, right after the router's
decision is recorded, so the only difference between them is which turns
switch. The 8B still reads a switched turn; the 27B writes it, and its soft
stages are off, as they were in the DEEP and SPLIT arms.
"""

from __future__ import annotations

import os

ROUTE_ENV = "ELAINA_FASTDEEP_ROUTE"
MODEL_ENV = "ELAINA_FASTDEEP_DEEP_MODEL"

RECORD_ONLY = ""
ROUTER = "router"
AFTER_FIRST = "after_first"
POLICIES = (RECORD_ONLY, ROUTER, AFTER_FIRST)


def configured() -> tuple[str, str]:
    """(policy, DEEP model) from the environment, read once when the engine
    starts. A misconfigured arm raises, so a run cannot quietly measure FAST
    under another arm's name."""
    policy = os.environ.get(ROUTE_ENV, "").strip().lower()
    if policy not in POLICIES:
        raise ValueError(f"{ROUTE_ENV}={policy!r}: expected one of {POLICIES[1:]} or unset")
    model = os.environ.get(MODEL_ENV, "").strip()
    if policy and not model:
        raise ValueError(f"{ROUTE_ENV}={policy} needs {MODEL_ENV}")
    return policy, (model if policy else "")


def words_model(decision: str, *, turn_number: int, policy: str, model: str) -> str:
    """The model that writes this turn when the arm switches it; "" when the
    turn is answered as always.

    ``turn_number`` counts the conversation's turns from 1.
    """
    if not policy or not model:
        return ""
    if policy == ROUTER:
        return model if decision == "DEEP" else ""
    if policy == AFTER_FIRST:
        return model if turn_number > 1 else ""
    return ""
