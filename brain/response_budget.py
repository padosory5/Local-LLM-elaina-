"""How much room a reply gets, from what the reply is for.

Phase 3D (docs/PHASE3_PLAN.md §1.5, 3D). Every non-social answer used to get
the same ceiling -- 45 words, two sentences -- unless the router flagged an
explicit request for detail, which it did on 1 turn in 86. Explanations
came out at a median of 32 words, written to fit, and in that room the 8B
said what an idea is *for* on 0 of 11 turns. A value question and "why
does ice float?" are not the same size of answer.

Three budgets, chosen from signals the turn already carries -- no new
classifier and no per-question-type table:

* ``value`` -- the turn asks for a value: the clock, arithmetic, a
  conversion, a figure (``AnswerCompletionGuard.asks_for_a_value``). The
  value first, one sentence of context at most.
* ``explain`` -- an information request that is not a value. Room for what
  it is for and one concrete case.
* ``elaborate`` -- the router's ``detailed_response`` (an explicit request
  for depth), or the person asking about her last answer because it did not
  land (the ``clarification`` label, R9). Room to take a different route.

Social acts (a greeting, a receipt, a reaction, a goodbye, a question she
has to ask, a confirmation) keep their conversation-style contracts
(brain/conversation_style.py), which were measured for those acts. The
ceilings live in config.yaml ``responses`` so they stay the person's to
change.
"""

from __future__ import annotations

from dataclasses import dataclass

VALUE = "value"
EXPLAIN = "explain"
ELABORATE = "elaborate"
KINDS = (VALUE, EXPLAIN, ELABORATE)

# Shipped defaults; config.yaml responses.<kind>_max_words/_max_sentences
# override them. Value is the old universal ceiling, unchanged.
DEFAULTS = {
    VALUE: (45, 2),
    EXPLAIN: (130, 6),
    ELABORATE: (240, 10),
}

_VALUE_INTENTS = frozenset({"time_question", "calculation"})
# Intents that are themselves a request for information, whatever speech act
# the router attached to them.
_QUESTION_INTENTS = frozenset({"knowledge_question", "web_search", "fact_check",
                               "clarification", "project_question"})
# Speech acts that ask for nothing to be explained: a thanks, a remark, a
# yes, a correction, an instruction, a request for advice (which has its own
# recommendation rules).
_NOT_A_REQUEST = frozenset({"social", "statement", "approval_response",
                            "correction", "action_request", "advice"})

# What fills the room, separately from how much room there is. A definition
# and a "why" both get room; only the "why" is helped by a worked example.
VALUE_ANSWER = "value"
STATE = "state"
EXPLAIN_ANSWER = "explain"
EXAMPLE = "example"
ELABORATE_ANSWER = "elaborate"
OPEN = "open"


def asks_for_information(intent: str, speech_act: str) -> bool:
    """Whether this turn asks for something to be told or explained.

    The router's own signal decides: ``speech_act`` distinguishes an
    information request from a thanks, a remark or an approval. Phase 3
    gave the explanation budget to every ``conversation`` turn, so "Thanks,
    that makes sense" got the room -- and the instruction -- of an
    explanation, and the 8B explained hash tables again.
    """
    act = str(speech_act or "").strip().lower()
    if act == "information_request":
        return True
    return intent in _QUESTION_INTENTS and act not in _NOT_A_REQUEST


@dataclass(frozen=True)
class ResponseBudget:
    kind: str
    max_words: int
    max_sentences: int
    # What the answer is for; "" when no instruction belongs in the prompt.
    shape: str = ""

    def goal(self, language: str = "en") -> str:
        """What the room is for, said to the model in the reply's language."""
        korean = str(language or "").lower().startswith("ko")
        goals = {
            VALUE_ANSWER: (
                "Give the value first; add at most one short sentence of context.",
                "요청하신 값을 먼저 말하고, 덧붙이는 설명은 한 문장 이내로 합니다.",
            ),
            STATE: (
                "Answer directly: the fact, the formula or what it is, and "
                "explain any technical term you use. No example or application "
                "unless they asked for one.",
                "바로 답합니다: 사실, 공식, 또는 그것이 무엇인지. 전문 용어를 "
                "쓰면 풀어서 설명합니다. 요청하지 않은 예시나 활용 사례는 "
                "덧붙이지 않습니다.",
            ),
            EXPLAIN_ANSWER: (
                "Explain it so it lands: say why it happens or what it is for, "
                "and show one concrete case.",
                "설명이 전달되도록 합니다: 왜 그런지 또는 무엇을 위한 것인지 "
                "말하고, 구체적인 사례 하나를 보여 줍니다.",
            ),
            EXAMPLE: (
                "They asked for an example: show one concrete situation where "
                "it is used, and what it solves there.",
                "예시를 원하셨습니다: 그것이 실제로 쓰이는 구체적인 상황 하나와, "
                "그 상황에서 무엇을 해결하는지 보여 줍니다.",
            ),
            ELABORATE_ANSWER: (
                "They did not follow the last explanation, or asked for depth: "
                "take a different route from before -- a new example, an "
                "analogy, or a step-by-step walk-through -- rather than saying "
                "the same thing again.",
                "이전 설명이 전달되지 않았거나 자세한 설명을 원하셨습니다. 같은 "
                "말을 반복하지 말고 다른 길로 설명합니다: 새로운 예시, 비유, "
                "또는 단계별 설명.",
            ),
            OPEN: (
                "Answer what was asked. Add a concrete case only when it makes "
                "the answer clearer.",
                "물으신 것에 답합니다. 구체적인 사례는 답을 더 분명하게 할 때만 "
                "덧붙입니다.",
            ),
        }
        english, korean_goal = goals.get(self.shape, ("", ""))
        return korean_goal if korean else english


def ceilings(config) -> dict[str, tuple[int, int]]:
    """The configured ceiling per budget, falling back to the defaults."""
    found = dict(DEFAULTS)
    if config is None:
        return found

    def read(key: str, default: int) -> int:
        try:
            value = config.get("responses", key, default=default, required=False)
            return int(value)
        except Exception:
            return default

    # The two keys that existed before Phase 3 keep meaning what they did:
    # max_words/max_sentences is the short ceiling, detailed_* the long one.
    found[VALUE] = (read("max_words", DEFAULTS[VALUE][0]),
                    read("max_sentences", DEFAULTS[VALUE][1]))
    found[EXPLAIN] = (read("explain_max_words", DEFAULTS[EXPLAIN][0]),
                      read("explain_max_sentences", DEFAULTS[EXPLAIN][1]))
    found[ELABORATE] = (read("detailed_max_words", DEFAULTS[ELABORATE][0]),
                        read("detailed_max_sentences", DEFAULTS[ELABORATE][1]))
    return found


def kind_for(*, intent: str, detailed: bool, value_response: bool,
             recommendation: bool = False, speech_act: str = "") -> str:
    """How much room this turn gets."""
    # A recommendation keeps the short budget: its own rules (the pick
    # first, one reason) and its finalizer were measured at that length,
    # and Phase 3 does not change them.
    if value_response or intent in _VALUE_INTENTS or recommendation:
        return VALUE
    if intent == "clarification":
        return ELABORATE
    if not asks_for_information(intent, speech_act):
        return VALUE
    if detailed:
        return ELABORATE
    return EXPLAIN


def shape_for(*, kind: str, intent: str, value_response: bool,
              recommendation: bool = False, speech_act: str = "",
              answer_shape: str = "") -> str:
    """What the answer is for, from the router's reading of the request."""
    if kind == VALUE:
        # A value gets "value first". A recommendation has its own rules,
        # and a thanks or a remark is told nothing about explaining.
        if (value_response or intent in _VALUE_INTENTS) and not recommendation:
            return VALUE_ANSWER
        return ""
    if kind == ELABORATE:
        return ELABORATE_ANSWER
    return {
        "state": STATE,
        "explain": EXPLAIN_ANSWER,
        "example": EXAMPLE,
    }.get(str(answer_shape or "").strip().lower(), OPEN)


def budget_for(*, intent: str, detailed: bool, value_response: bool,
               recommendation: bool = False, speech_act: str = "",
               answer_shape: str = "", config=None) -> ResponseBudget:
    kind = kind_for(intent=intent, detailed=detailed, value_response=value_response,
                    recommendation=recommendation, speech_act=speech_act)
    shape = shape_for(kind=kind, intent=intent, value_response=value_response,
                      recommendation=recommendation, speech_act=speech_act,
                      answer_shape=answer_shape)
    words, sentences = ceilings(config)[kind]
    return ResponseBudget(kind, words, sentences, shape)
