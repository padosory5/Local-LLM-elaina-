"""One explanation contract, and an optional plan -- Stage C.

In her own use, only the contract (arm B) can run, and only as the
controlled experiment config.yaml names (``responses.explanation_contract``,
ELAINA_EXPLANATION_CONTRACT overriding it): off, on every explanation turn,
or on a random half of them, each explanation turn's arm logged in its
trace. docs/STAGE_C_RESULTS.md found it a partial improvement -- under the
strict concrete_first definition it still fails on 22 of 30 held-out
turns -- so it runs as an experiment, to be measured on real turns
(evals/explanation_experiment.py), not as a solved problem. No plan and no
model call in any of her own modes.

The evaluation arms are set only by ELAINA_EXPLAIN_ARM, which nothing in
config.yaml sets; they take precedence over the experiment.

docs/STAGE_C_DIAGNOSIS.md found why both models open an explanation with a
general statement and leave the example, if any, for last: on an
explanation turn the prompt carries five sources of order and length, each
written for something else, and they disagree.

* the persona: "Lead with the answer. State the fact ... first", "Short by
  default, a sentence or two", "No jargon, no over-explaining";
* the voice requirements: "Give the requested result before reactions,
  background ...", and 130 words / 6 sentences;
* the explain goal: one sentence asking for a concrete case, with no place;
* the style contract for an answer: "Lead with the answer in plain spoken
  words.", "At most 4 sentences.";
* after generation, her_voice cuts an answer to 4 sentences.

The arms (docs/STAGE_C_PROTOCOL.md):

* ``contract`` (B) -- on an explanation turn those order and length rules
  are taken out and one contract says the order and the length. The length
  is the one every remaining enforcer already applies, 4 sentences, so the
  contract does not fight the response stages, which are unchanged.
* ``plan`` (C) -- B, plus a small plan from one 8B call before generation:
  what the person needs to understand, the situation to open with, the
  answer in a clause, at most two terms with their everyday meaning, and
  whether a sentence on how it works is needed. A plan, not a draft.
* ``plan_file`` (D) -- B, plus the same plan written beforehand by the 27B
  (the ceiling for the planner), read from a file.

"An explanation turn" is the response budget's ``explain`` kind with the
``explain`` shape, on the answer act: the 20 of the 21 diagnosed turns.
Every other turn is built exactly as in production.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass

from brain.response_budget import CHECKING_UNDERSTANDING

ARM_ENV = "ELAINA_EXPLAIN_ARM"
PLANS_ENV = "ELAINA_EXPLAIN_PLANS"

OFF = ""
CONTRACT = "contract"
PLAN = "plan"
PLAN_FILE = "plan_file"
# Stage C's own contract, for comparison runs only.
CONTRACT_STAGE_C = "contract_stage_c"
ARMS = (OFF, CONTRACT, CONTRACT_STAGE_C, PLAN, PLAN_FILE)

# The production experiment: arm B only, never a plan, never another call.
EXPERIMENT_ENV = "ELAINA_EXPLANATION_CONTRACT"
EXPERIMENT_OFF, EXPERIMENT_ON, EXPERIMENT_SPLIT = "off", "on", "split"
EXPERIMENT_MODES = (EXPERIMENT_OFF, EXPERIMENT_ON, EXPERIMENT_SPLIT)
CURRENT = "current"

MAX_SENTENCES = 4

# The persona rules an explanation turn drops, by section and position.
# personality_en.txt and personality_ko.txt are parallel rule for rule
# (brain/personality_loader.py), so one position names both.
SUPERSEDED_PERSONA_RULES = (
    ("VOICE", 4),                        # Short by default, a sentence or two
    ("KNOWLEDGE AND CALCULATIONS", 0),   # Lead with the answer; the fact first
    ("KNOWLEDGE AND CALCULATIONS", 3),   # keep it simple; no over-explaining
)

# The lines of the answer act's style block an explanation turn drops: its
# order rule and its sentence ceiling. The contract says both.
SUPERSEDED_STYLE_LINES = (
    "Lead with the answer in plain spoken words.",
    f"At most {MAX_SENTENCES} sentences.",
)

# The voice requirements on an explanation turn: the production rules that
# do not concern order or length, verbatim (a test holds them to
# brain/response_policy.py), then the contract.
_KEPT_REQUIREMENTS = {
    "en": (
        "Answer the current request in this response.",
        "Do not say that you will calculate, explain, check, or break something down later "
        "when you can do it now.",
        "Do not ask whether the user wants the answer after they already requested it.",
        "Compose a naturally shorter complete answer; never stop mid-sentence or omit the "
        "requested result to satisfy a length target.",
    ),
    "ko": (
        "지금 들어온 요청에 이 응답에서 답합니다.",
        "지금 할 수 있는 계산이나 설명을 나중에 하겠다고 미루지 않습니다.",
        "이미 요청하신 것을 다시 원하시는지 묻지 않습니다.",
        "길이를 맞추려고 문장을 중간에 끊거나 요청하신 결과를 빼지 않습니다. 자연스럽게 짧게 씁니다.",
        "반드시 습니다체로 씁니다. '~요'로 끝나는 해요체는 쓰지 않습니다.",
        "확실하지 않은 것은 지어내지 않습니다. 영화 제목, 상품명, 가격은 확인된 것만 말합니다.",
    ),
}

# Stage C's arm B, word for word: tested in docs/STAGE_C_RESULTS.md, kept
# only as a comparison arm. It made a concrete opening the rule, which the
# held-out run paid for in answers that started with a scene.
STAGE_C_CONTRACT_TEXT = {
    "en": (
        "HOW TO EXPLAIN THIS\n"
        "Build the explanation around one concrete situation, in this order:\n"
        "1. Open with a particular, everyday situation in which the answer can be seen "
        "happening (a specific person, object or number), and let that opening already carry "
        "the answer to their question. The situation is where the answer happens, not a "
        "warm-up before it; a comparison of the form \"it is like X\" is not a situation.\n"
        "2. If it helps, say the general point in plain words as what that situation shows.\n"
        "3. Use a technical word only when the answer needs it, and say what it means in "
        "everyday words in the same sentence.\n"
        "4. Add more about how it works only when the answer is unclear without it.\n"
        "At most {sentences} sentences and at most {words} words."
    ),
    "ko": (
        "설명하는 방법\n"
        "하나의 구체적인 상황을 중심으로, 이 순서로 설명합니다.\n"
        "1. 답이 실제로 일어나는 모습이 보이는 특정한 일상 상황(구체적인 사람, 물건, 숫자)으로 "
        "시작하고, 그 시작 부분에서 이미 그 상황을 통해 질문에 대한 답을 말합니다. 상황은 답이 "
        "일어나는 곳이지 답 앞의 도입부가 아닙니다. \"X와 같다\"는 식의 비유는 상황이 아닙니다.\n"
        "2. 도움이 되면, 그 상황이 보여 주는 일반적인 원리를 쉬운 말로 말합니다.\n"
        "3. 전문 용어는 답에 꼭 필요할 때만 쓰고, 같은 문장에서 일상적인 말로 뜻을 풀어 줍니다.\n"
        "4. 작동 원리는 그것 없이는 답이 분명하지 않을 때만 더 설명합니다.\n"
        "{sentences}문장, {words}단어 이내로 말합니다."
    ),
}

# The contract her own use runs. The goal is an explanation that is
# understandable and sounds natural; a direct answer comes first, and a
# concrete example is a tool for understanding, used when it helps and left
# out when the direct answer is already clear -- never an opening that
# delays the answer.
CONTRACT_TEXT = {
    "en": (
        "HOW TO EXPLAIN THIS\n"
        "Explain it the way a knowledgeable person would say it out loud:\n"
        "1. Answer their question directly in the first sentence, in plain words.\n"
        "2. Add one concrete example (a specific situation, object or number) only when it makes "
        "the answer easier to understand, such as when the idea is abstract or how it works is "
        "hard to picture. When the direct answer is already clear, leave the example out. Never "
        "open with an example or a story that delays the answer.\n"
        "3. Use a technical word only when the answer needs it, and say what it means in "
        "everyday words in the same sentence.\n"
        "4. Say more about how it works only when the answer is unclear without it.\n"
        "5. " + CHECKING_UNDERSTANDING[0] + "\n"
        "At most {sentences} sentences and at most {words} words; shorter is better when the "
        "answer is simple."
    ),
    "ko": (
        "설명하는 방법\n"
        "아는 사람이 말로 설명하듯이 자연스럽게 설명합니다.\n"
        "1. 첫 문장에서 질문에 바로, 쉬운 말로 답합니다.\n"
        "2. 구체적인 예시(특정한 상황, 물건, 숫자)는 개념이 추상적이거나 원리를 떠올리기 어려울 "
        "때처럼 답을 이해하기 쉽게 만들 때만 하나 덧붙입니다. 바로 한 답으로 이미 분명하면 예시는 "
        "넣지 않습니다. 답을 미루는 예시나 이야기로 시작하지 않습니다.\n"
        "3. 전문 용어는 답에 꼭 필요할 때만 쓰고, 같은 문장에서 일상적인 말로 뜻을 풀어 줍니다.\n"
        "4. 작동 원리는 그것 없이는 답이 분명하지 않을 때만 더 설명합니다.\n"
        "5. " + CHECKING_UNDERSTANDING[1] + "\n"
        "{sentences}문장, {words}단어 이내로 말하고, 답이 간단하면 더 짧게 말합니다."
    ),
}

PLAN_TEXT = {
    "en": (
        "PLAN FOR THIS ANSWER (follow it; do not read it out)\n"
        "What they need to understand: {understand}\n"
        "Situation to open with: {scene}\n"
        "The answer, carried by that situation: {answer}\n"
        "Words to explain in everyday terms if you use them: {terms}\n"
        "How it works: {depth}"
    ),
    "ko": (
        "이 답의 계획 (따르되, 그대로 읽지 않습니다)\n"
        "이해해야 할 것: {understand}\n"
        "시작할 상황: {scene}\n"
        "그 상황이 전하는 답: {answer}\n"
        "쓰게 되면 일상적인 말로 풀어 줄 용어: {terms}\n"
        "작동 원리: {depth}"
    ),
}
_DEPTH_TEXT = {
    "en": {"brief": "not needed beyond the answer",
           "mechanism": "one more sentence, only if it makes the answer clearer"},
    "ko": {"brief": "답 이상은 필요 없습니다",
           "mechanism": "답을 더 분명하게 할 때만 한 문장 더"},
}
_NO_TERMS = {"en": "none", "ko": "없음"}

PLANNER_PROMPT = (
    "You plan how an answer will be explained. You do not write the answer.\n"
    "Read the person's question (their earlier questions, if any, come first) and return "
    "one JSON object with exactly these keys:\n"
    "\"understand\": what this person needs to understand for the question to be answered, "
    "in at most 15 words.\n"
    "\"scene\": one particular, everyday situation in which the answer can be seen happening, "
    "with a specific person, object or number, in at most 20 words. Not a comparison of the "
    "form \"it is like X\".\n"
    "\"answer\": the answer to the question itself, in plain words, in at most 20 words.\n"
    "\"terms\": a list of at most 2 objects {{\"term\": ..., \"plain\": ...}} for words the "
    "answer cannot do without that a non-specialist may not know, each meaning in everyday "
    "words in at most 12 words; an empty list if there are none.\n"
    "\"depth\": \"brief\" if the scene and the answer are enough, \"mechanism\" if one more "
    "sentence on how it works is needed.\n"
    "Write every value in {language}."
)
_LANGUAGE_NAME = {"en": "English", "ko": "Korean"}

# A plan is unusable -- and the turn falls back to the contract alone --
# when it cannot be read, lacks a scene or an answer, or runs past twice the
# length it was asked for: past that it is a draft, not a plan.
_LIMIT_WORDS = {"scene": 20, "answer": 20}


def _lang(language: str) -> str:
    return "ko" if str(language or "").strip().lower().startswith("ko") else "en"


def configured() -> tuple[str, dict]:
    """(arm, plans by key), read once when the engine starts. A misconfigured
    arm raises, so a run cannot quietly measure production under another
    arm's name."""
    arm = os.environ.get(ARM_ENV, "").strip().lower()
    if arm not in ARMS:
        raise ValueError(f"{ARM_ENV}={arm!r}: expected one of {ARMS[1:]} or unset")
    plans: dict = {}
    if arm == PLAN_FILE:
        path = os.environ.get(PLANS_ENV, "").strip()
        if not path:
            raise ValueError(f"{ARM_ENV}={arm} needs {PLANS_ENV}")
        with open(path, encoding="utf-8") as handle:
            plans = json.load(handle)["plans"]
    return arm, plans


def experiment_mode(config) -> str:
    """The production experiment's mode: ELAINA_EXPLANATION_CONTRACT, else
    config.yaml ``responses.explanation_contract``, else off. An unknown
    value is said out loud and read as off -- her own use must still start."""
    value = os.environ.get(EXPERIMENT_ENV)
    if value is None:
        try:
            value = config.get("responses", "explanation_contract",
                               default=EXPERIMENT_OFF, required=False)
        except Exception:
            value = EXPERIMENT_OFF
    # YAML 1.1 reads a bare off / on as a boolean.
    if value is False:
        value = EXPERIMENT_OFF
    elif value is True:
        value = EXPERIMENT_ON
    mode = str(value if value is not None else EXPERIMENT_OFF).strip().lower()
    if mode not in EXPERIMENT_MODES:
        print(f"[Explain] explanation_contract={value!r} is not one of {EXPERIMENT_MODES}; "
              "running with it off")
        return EXPERIMENT_OFF
    return mode


def production_variant(mode: str, turn_key: str) -> tuple[str, str]:
    """(CONTRACT or CURRENT, how it was chosen) for one explanation turn.

    ``split`` decides by the turn's own random id, so the arm is random per
    turn, independent of anything about the turn, and logged with it."""
    if mode == EXPERIMENT_ON:
        return CONTRACT, "flag on"
    if mode == EXPERIMENT_SPLIT:
        bit = hashlib.sha256(str(turn_key).encode("utf-8")).digest()[0] & 1
        return (CONTRACT if bit else CURRENT), "split by turn id"
    return CURRENT, "flag off"


def applies(*, kind: str, shape: str, act: str) -> bool:
    """An explanation turn: the explain budget, the explain shape, the answer act."""
    return kind == "explain" and shape == "explain" and act == "answer"


def persona_without_conflicts(text: str, rules: dict[str, list[str]]) -> str:
    """The persona with the superseded order and length rules taken out."""
    drop = {f"- {rules[section][index]}" for section, index in SUPERSEDED_PERSONA_RULES}
    return "\n".join(line for line in text.splitlines() if line.strip() not in drop)


def style_without_conflicts(text: str) -> str:
    """The answer act's style block without its order rule and sentence ceiling."""
    return "\n".join(line for line in text.splitlines() if line.strip() not in SUPERSEDED_STYLE_LINES)


@dataclass(frozen=True)
class Plan:
    understand: str
    scene: str
    answer: str
    terms: tuple            # ((term, plain), ...), at most 2
    depth: str              # "brief" or "mechanism"

    def as_record(self) -> dict:
        found = asdict(self)
        found["terms"] = [list(pair) for pair in self.terms]
        return found


def parse_plan(text: str) -> tuple[Plan | None, str]:
    """(plan, "") or (None, why it is unusable)."""
    try:
        data = json.loads(str(text or "").strip())
    except (TypeError, ValueError):
        return None, "not JSON"
    if not isinstance(data, dict):
        return None, "not an object"
    values = {}
    for key in ("scene", "answer"):
        value = str(data.get(key) or "").strip()
        if not value:
            return None, f"no {key}"
        if len(value.split()) > 2 * _LIMIT_WORDS[key]:
            return None, f"{key} longer than {2 * _LIMIT_WORDS[key]} words"
        values[key] = value
    terms = []
    raw_terms = data.get("terms") or []
    if not isinstance(raw_terms, list):
        return None, "terms is not a list"
    for item in raw_terms[:2]:
        if isinstance(item, dict) and str(item.get("term") or "").strip():
            terms.append((str(item["term"]).strip(), str(item.get("plain") or "").strip()))
    depth = str(data.get("depth") or "").strip().lower()
    return Plan(
        understand=str(data.get("understand") or "").strip(),
        scene=values["scene"],
        answer=values["answer"],
        terms=tuple(terms),
        depth=depth if depth in ("brief", "mechanism") else "brief",
    ), ""


def plan_key(said: list[str]) -> str:
    """The questions so far, the current one last: what a plan is for."""
    return "\n".join(str(item).strip() for item in said)


def planner_messages(said: list[str], language: str) -> list[dict]:
    lang = _lang(language)
    earlier, current = [str(s).strip() for s in said[:-1]], str(said[-1]).strip()
    body = ("Earlier questions:\n" + "\n".join(f"- {line}" for line in earlier) + "\n\n"
            if earlier else "")
    return [
        {"role": "system", "content": PLANNER_PROMPT.format(language=_LANGUAGE_NAME[lang])},
        {"role": "user", "content": f"{body}Question:\n{current}"},
    ]


def render_plan(plan: Plan, language: str) -> str:
    lang = _lang(language)
    terms = "; ".join(f"{term} ({plain})" if plain else term for term, plain in plan.terms)
    return PLAN_TEXT[lang].format(
        understand=plan.understand or "-", scene=plan.scene, answer=plan.answer,
        terms=terms or _NO_TERMS[lang], depth=_DEPTH_TEXT[lang][plan.depth],
    )


def text_for(arm: str) -> dict:
    """Which contract an arm runs: the plan arms and the Stage C comparison
    arm keep the text they were tested with; everything else the revision."""
    return STAGE_C_CONTRACT_TEXT if arm in (CONTRACT_STAGE_C, PLAN, PLAN_FILE) else CONTRACT_TEXT


def requirements(language: str, *, max_words: int, plan: Plan | None = None,
                 text: dict | None = None) -> str:
    """The voice requirements of an explanation turn under the contract."""
    lang = _lang(language)
    contract = text if text is not None else CONTRACT_TEXT
    parts = [" ".join(_KEPT_REQUIREMENTS[lang]),
             contract[lang].format(sentences=MAX_SENTENCES, words=max_words)]
    if plan is not None:
        parts.append(render_plan(plan, lang))
    return "\n\n".join(parts)


def _contract_version(text: dict) -> str:
    """A short hash of everything the contract changes: logged with every
    turn, so a real turn can be tied to the exact contract it ran under."""
    frozen = {"kept": _KEPT_REQUIREMENTS, "contract": text, "sentences": MAX_SENTENCES,
              "persona": SUPERSEDED_PERSONA_RULES, "style": SUPERSEDED_STYLE_LINES}
    return hashlib.sha256(json.dumps(frozen, ensure_ascii=False, sort_keys=True).encode("utf-8")
                          ).hexdigest()[:12]


CONTRACT_VERSION = _contract_version(CONTRACT_TEXT)
STAGE_C_CONTRACT_VERSION = _contract_version(STAGE_C_CONTRACT_TEXT)
