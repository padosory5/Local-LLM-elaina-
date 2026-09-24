"""Say a Korean sentence in 습니다체 when it came out in 해요체.

``personality_ko.txt`` specifies 습니다체 and qwen3:8b does not hold it.
Measured across three live runs, roughly half of her generated Korean
sentences came back 해요체, the prompt did not move it, and asking for the
line again mostly produced 해요체 a second time -- once it produced 반말,
which is further from the register than what it replaced.

That is the exact shape of problem this project has learned to fix in code
rather than in wording. The fix is narrow on purpose:

**Only exact endings, only form.** Every entry here is a change of
*politeness form*, never of meaning, tense, or content. "인상적이에요" and
"인상적입니다" say the same thing about the same subject; nothing here can
turn one claim into another, which is what makes a deterministic rewrite
safe on text that has already passed the grounding guards.

**Unsure means unchanged.** Endings that need real verb morphology --
"고마워요", "있나요?", "지내세요?", "그렇군요" -- are left alone rather than
guessed at. Half-converted Korean is worse than politely wrong Korean, and
the style layer's re-say still gets its attempt at those.
"""

from __future__ import annotations

import re


LANGUAGES = ("ko",)


# The ㄹ that a future form inserts, and how to take it back off. "도와드릴
# 게요" is 도와드리 + ㄹ게요, and 겠습니다 attaches to 도와드리 -- so the
# syllable has to be decomposed rather than sliced.
_HANGUL_BASE = 0xAC00
_JONGSEONG_COUNT = 28
_RIEUL = 8


def _ends_in_rieul(syllable: str) -> bool:
    if not ("가" <= syllable <= "힣"):
        return False
    return (ord(syllable) - _HANGUL_BASE) % _JONGSEONG_COUNT == _RIEUL


def _without_rieul(syllable: str) -> str:
    return chr(ord(syllable) - _RIEUL)


def _has_final_consonant(syllable: str) -> bool:
    """Whether this Hangul syllable ends in a consonant (받침)."""
    if not ("가" <= syllable <= "힣"):
        return False
    return (ord(syllable) - _HANGUL_BASE) % _JONGSEONG_COUNT != 0


# Exact ending -> exact replacement. Ordered longest-first within each
# family so "이에요" is tried before "예요" and "겠네요" before "네요".
_ENDINGS: tuple[tuple[str, str], ...] = (
    # 아니다 is not 아니 + 이다: "아니에요" is 아닙니다. Measured live, the
    # rule below it made "갤럭시는 애플에서 만든 폰이 아니입니다".
    ("아니에요", "아닙니다"),
    ("아니예요", "아닙니다"),
    ("이에요", "입니다"),
    ("이네요", "입니다"),
    ("예요", "입니다"),
    ("에요", "입니다"),
    ("있어요", "있습니다"),
    ("없어요", "없습니다"),
    ("있네요", "있습니다"),
    ("없네요", "없습니다"),
    ("겠어요", "겠습니다"),
    ("겠네요", "겠습니다"),
    # "결과가 중요하겠죠." -- the presumptive with a confirming 죠, measured
    # drifting in the paired run. The question form returns before this.
    ("겠죠", "겠습니다"),
    # The same presumptive in 반말: "힘들었겠지." Measured as a whole reply.
    ("겠지요", "겠습니다"),
    ("겠지", "겠습니다"),
    # Hearsay: "내일 비가 온대요" is 온다고 해요, and 대요 attaches after
    # the same syllable 다고 does -- 온대요 -> 온다고 합니다, 비싸대요 ->
    # 비싸다고 합니다, 했대요 -> 했다고 합니다. Measured drifting in the
    # paired run; the question form returns before this.
    ("대요", "다고 합니다"),
    ("좋아요", "좋습니다"),
    ("싫어요", "싫습니다"),
    ("같아요", "같습니다"),
    ("돼요", "됩니다"),
    ("해요", "합니다"),
    ("드려요", "드립니다"),
    # Regular for a stem ending in 리: 느려요 -> 느립니다, 기다려요 ->
    # 기다립니다, 어울려요 -> 어울립니다. Listed after 드려요 so that one
    # keeps its own entry rather than being caught by this.
    ("려요", "립니다"),
    ("주세요", "주십시오"),
    ("하세요", "하십시오"),
    ("보세요", "보십시오"),
    # The honorific copula, before the general 세요 rule below can reach
    # it. "선생님이세요" is "you are a teacher", so it takes 이십니다 --
    # turning it into an imperative would tell them to *be* one.
    ("이세요", "이십니다"),
    ("아니세요", "아니십니다"),
    # 없다 has no imperative, so 없으세요 is always a statement about them.
    # The general rule below made "걱정 없으세요" into "걱정 없으십시오".
    ("없으세요", "없으십니다"),
    # Every other 세요 is an imperative, and 세요 and 십시오 attach after
    # the same honorific 시-, so the swap needs no stem surgery:
    # 갖으세요 -> 갖으십시오, 주무세요 -> 주무십시오. Three specific cases
    # were already listed above; this is the rule they were instances of.
    #
    # Only reached for statements: questions return earlier, which is what
    # keeps "잘 지내세요?" from becoming an order instead of 지내십니까.
    #
    # Measured on an unseen Korean arc, register_drift was 7 of 12 turns
    # and forms like "갖으세요" were most of it -- the largest single
    # Korean failure class, and ours rather than the model's.
    ("세요", "십시오"),
    # A farewell, said the way this register says it. Same speech act,
    # same meaning, and she really does produce the 해요체 form.
    ("잘자요", "안녕히 주무십시오"),
    ("잘 자요", "안녕히 주무십시오"),
)

# Phrases that stopped being grammar. 안녕하세요 is -세요 by shape, and the
# -세요 rule turned it into "안녕하십시오" -- which is a word, and is not
# what anyone says. A rule about verb endings has to know which endings are
# no longer verb endings.
_LEXICALISED = (
    "안녕하세요",
    "안녕히 계세요",
    "안녕히 가세요",
    "어서 오세요",
    "안녕하십니까",
)

# What separates a sentence from its punctuation, kept so the mark comes
# back exactly as it was.
_TRAILING = re.compile(r"[\s.!?~]*$")
_HANGUL = re.compile(r"[가-힣]")


def to_formal_sentence(sentence: str) -> str:
    """One sentence in 습니다체, or unchanged when the ending is not known."""
    text = str(sentence or "")
    if not _HANGUL.search(text):
        return text

    tail_match = _TRAILING.search(text)
    tail = tail_match.group(0) if tail_match else ""
    body = text[: len(text) - len(tail)] if tail else text
    if not body:
        return text

    if any(body.endswith(fixed) for fixed in _LEXICALISED):
        return text

    # A question keeps its own forms where this module cannot build them.
    # "할까요?" is already how this register asks permission, and most
    # 해요체 questions need morphology this module does not have --
    # "배고파요?" becomes "배고프신가요?" -- where a wrong conjugation would
    # be worse than the drift it replaced.
    #
    # Three shapes are mechanical, and between them they were most of the
    # drift measured in questions: "영화는 보셨어요?", "그만두셨어요?",
    # "저녁은 김치찌개 어때요?". They become 나요? and 신가요?, which the
    # person picked as her natural question forms -- neither the stiffer
    # 습니까? nor the 해요체 she otherwise does not speak.
    if "?" in tail:
        return _formal_question(body) + tail

    # "도와드릴게요" -> "도와드리겠습니다".
    if body.endswith("게요"):
        stem = body[:-2]
        if stem and _ends_in_rieul(stem[-1]):
            stem = stem[:-1] + _without_rieul(stem[-1])
        if stem:
            return stem + "겠습니다" + tail

    # "~나 보세요" and "~가 보세요" are evidential -- "it seems" -- not an
    # instruction to look. The 세요 -> 십시오 rule below turned a paired
    # dogfood reply into "회사에서 하루 종일 회의만 했나 보십시오", an order
    # to observe that someone had meetings, which is worse than the drift it
    # replaced. The person has also said the evidential itself does not
    # sound like her -- and leaving it for the re-say did not work: in the
    # paired baseline "힘드셨나 보죠" came back from the re-say still
    # drifting, three times. What she does say is the presumptive,
    # "힘드셨겠습니다", and where the stem is unambiguous the swap is
    # mechanical.
    presumed = _presumptive(body)
    if presumed:
        return presumed + tail
    if body.endswith(("나 보세요", "가 보세요", "나보세요", "가보세요")):
        return text

    # "안 마시시는 거죠" -- confirming what they said -- is 는군요 in her
    # register: "안 마시시는군요". Only after 는, where nothing about the
    # stem has to be known.
    for ending in ("는 거죠", "는거죠"):
        if body.endswith(ending):
            return body[: -len(ending)] + "는군요" + tail

    for ending, formal in _ENDINGS:
        if body.endswith(ending):
            return body[: -len(ending)] + formal + tail

    # The regular case, once the irregular ones above have had their turn.
    # A stem ending in a consonant takes 습니다 where 해요체 takes 어요 or
    # 아요: 알겠어요 -> 알겠습니다, 깊어요 -> 깊습니다, 먹어요 -> 먹습니다.
    # A stem ending in a vowel does not -- 와요 becomes 옵니다, which needs
    # more than this module knows -- so the 받침 test is what keeps it from
    # guessing.
    for ending in ("어요", "아요"):
        if body.endswith(ending):
            stem = body[: -len(ending)]
            if stem and _ends_in_rieul(stem[-1]):
                # ㄹ drops before ㅂ니다: 살아요 -> 삽니다, 만들어요 -> 만듭니다,
                # 알아요 -> 압니다. Measured live: "부산에 살아요" came out
                # "부산에 살습니다". A stem that could as well be a ㄷ-irregular
                # (들어요: 듣다 or 들다, 걸어요: 걷다 or 걸다) is left as written
                # rather than conjugated as the wrong verb.
                if any(stem.endswith(ambiguous) for ambiguous in _RIEUL_OR_DIGEUT):
                    return text
                return stem[:-1] + _with_bieup(stem[-1]) + "니다" + tail
            if stem and _has_final_consonant(stem[-1]):
                return stem + "습니다" + tail

    return text


_RIEUL_OR_DIGEUT = ("걸", "들", "물", "실", "불", "깨달")
_RIEUL_FINAL = 8
_BIEUP_FINAL = 17


def _with_bieup(syllable: str) -> str:
    """The same syllable with ㅂ in place of its ㄹ: 살 -> 삽."""
    return chr(ord(syllable) - _RIEUL_FINAL + _BIEUP_FINAL)


def _ends_in_ssangsiot(syllable: str) -> bool:
    """Whether a syllable ends in ㅆ -- the mark of the past in 봤, 셨, 했."""
    code = ord(syllable)
    return 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 == 20


_NIEUN = 4

# The evidential, in the spellings the model writes it.
_EVIDENTIAL = (
    "나 보죠", "나 봐요", "나 보세요", "나보죠", "나봐요", "나보세요",
    "가 보죠", "가 봐요", "가 보세요", "가보죠", "가봐요", "가보세요",
)


def _presumptive(body: str) -> str:
    """"~나 보죠" as "~겠습니다", where the stem is certain; else nothing.

    V-나 보다 and A-(으)ㄴ가 보다 both become the stem plus 겠습니다:
    했나 보죠 -> 했겠습니다, 깨시나 봐요 -> 깨시겠습니다, 피곤하신가 보죠 ->
    피곤하시겠습니다, 좋은가 봐요 -> 좋겠습니다. Only the stems whose shape
    is known are touched. "만나 보세요" is the verb 만나다, "try meeting",
    and an irregular adjective like 그런가 (그렇다) would need the ㅎ back --
    both are left alone.
    """
    for ending in _EVIDENTIAL:
        if not body.endswith(ending):
            continue
        stem = body[: -len(ending)]
        if len(stem) < 2 or not ("가" <= stem[-1] <= "힣"):
            return ""
        last = stem[-1]
        if ending[0] == "나":
            # Past (했나, 셨나, 많았나), honorific (깨시나), 없나.
            if _ends_in_ssangsiot(last) or last in ("시", "없"):
                return stem + "겠습니다"
            return ""
        # 가 follows the ㄴ of an adjective or copula, or 는 of a verb.
        if last in ("은", "는"):
            return stem[:-1] + "겠습니다"
        if last in ("한", "신", "인"):
            return stem[:-1] + chr(ord(last) - _NIEUN) + "겠습니다"
        return ""
    return ""


def _formal_question(body: str) -> str:
    """A 해요체 question in the forms she uses, where that is mechanical."""
    if body.endswith("어때요"):
        return body[:-3] + "어떠신가요"
    # Checking she understood: "영화를 말씀하시는 거죠?" -> "말씀하시는
    # 건가요?". After 는 only, where nothing about the stem is needed.
    for plain, formal in (("는 거죠", "는 건가요"), ("는거죠", "는건가요")):
        if body.endswith(plain):
            return body[: -len(plain)] + formal
    for plain, formal in (("있어요", "있나요"), ("없어요", "없나요")):
        if body.endswith(plain):
            return body[:-len(plain)] + formal
    # The past: 나요 attaches straight to a syllable carrying ㅆ.
    # 봤어요 -> 봤나요, 보셨어요 -> 보셨나요, 먹었어요 -> 먹었나요.
    if body.endswith("어요") and len(body) > 2 and _ends_in_ssangsiot(body[-3]):
        return body[:-2] + "나요"
    return body


def to_formal(text: str) -> str:
    """Every sentence of this reply in 습니다체, where the ending is known.

    Sentence by sentence, because a reply mixes them: the measured drift
    was usually one 해요체 clause sitting between two correct ones.
    """
    said = str(text or "")
    if not _HANGUL.search(said):
        return said

    pieces = re.split(r"(?<=[.!?])(\s+)", said)
    rebuilt = [
        piece if index % 2 else to_formal_sentence(piece)
        for index, piece in enumerate(pieces)
    ]
    return "".join(rebuilt)
