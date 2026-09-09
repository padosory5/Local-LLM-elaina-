"""What a reply is *doing*, and how a person doing that would say it.

Every layer beneath this one decides *what is true*: the router decides what
was asked, the planners decide what happened, the grounding guards decide
what may be claimed. None of them decide *how it sounds*, and for most of
this project's life nothing did -- the words came from whichever layer
happened to produce the sentence. A greeting came from a variety selector, a
tool outcome came from a planner's own model call under a different prompt, an
acknowledgement came from a string literal, and a plain answer came from
personality.txt. Four authors, one voice, and it showed: the same turn could
sound like a friend, a status bar and a support ticket within three messages.

This module is the missing authority. It says nothing about truth and cannot
change a value; it names the **conversational act** a reply performs, states
the **style contract** for that act, and detects the **failure classes** that
mean a draft is not in her voice.

Three rules keep it from becoming a phrase blocklist.

**An act, not a phrase.** Contracts are keyed to what the reply is doing --
receipting, answering, reporting, asking, offering, closing -- because that
is what determines how long it should be, whether it may carry an offer, and
whether it may be reworded at all. A sentence is only ever judged as an
instance of an act.

**Detection is measurement, repair is not.** :class:`RoboticTells` finds
failures so that they can be *counted*, and so a bad draft can be sent back
to be said again. Only findings marked ``structural`` -- artifacts no
rewording can justify, like a leaked internal marker or a stray quote --
are ever deleted deterministically. Everything else is a signal to
re-realize, never a signal to string-edit, because deleting a robotic
sentence usually leaves a reply that is merely shorter and still robotic.

**Truth outranks style.** Nothing here may add, drop, or alter a value, a
name, a number or an action status. The style layer runs before the
grounding guards precisely so that those still get the last word.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field


# Both, unevenly, and the unevenness is stated in each rule. The
# structural repairs and the counting rules work on any script; the
# register lists are English phrases except for the Korean register and
# service checks, which are marked where they are defined.
LANGUAGES = ("en", "ko")


# --------------------------------------------------------------------------
# Conversational acts
# --------------------------------------------------------------------------
#
# What the *reply* does, which is not what the user asked for. "What windows
# do I have open" is a question; the reply to it is a REPORT, because its
# content came from a machine and its risk is sounding like one. "ok" is an
# acknowledgement; the reply to it is a RECEIPT, whose entire job is to be
# brief and not restart the conversation.

GREET = "greet"
RECEIPT = "receipt"          # she is receiving something: "ok", "thanks", "no"
ANSWER = "answer"            # information she knows or found
REPORT = "report"            # what a tool, planner or the machine did
ASK = "ask"                  # a clarification question she needs answered
OFFER = "offer"              # proposing to do something
CONFIRM = "confirm"          # asking permission before a real commit
DECLINE = "decline"          # saying no, or saying she cannot
REACT = "react"              # responding to feeling or small talk
CLOSE = "close"              # farewell

ACTS = (GREET, RECEIPT, ANSWER, REPORT, ASK, OFFER, CONFIRM, DECLINE,
        REACT, CLOSE)


@dataclass(frozen=True)
class StyleContract:
    """How a person performing this act would say it.

    ``may_reword`` is the load-bearing field and the reason this is a table
    rather than one rule. A clarification question and a consent question
    carry their meaning in their exact words -- the consent classifier reads
    the answer against the question that was asked, and a reworded question
    is a different question. Those acts are locked. A receipt or a greeting
    carries no meaning beyond being said at all, so it is free.

    ``max_sentences`` is a ceiling that only ever *tightens* the configured
    response length, never loosens it. Two sentences of "got it" is one
    sentence too many regardless of what config.yaml allows.
    """

    act: str
    max_sentences: int
    offers_allowed: int
    may_reword: bool
    may_restate_request: bool = False
    # A brevity bound in words, for the acts where being short is the
    # whole point. Measured: a sentence count is the wrong instrument for
    # this. "I'm here. You don't have to talk if you don't want to." is
    # two sentences and twelve words, and is exactly what a person says;
    # "Alright, then maybe we'll find something else. Just let me know when
    # you're ready to chill and I'll suggest something fun." is also two
    # sentences and twenty-three words, and is the failure. Counting
    # sentences called the first one wrong and -- worse -- rejected every
    # rewrite offered for the second, because a good short reply is
    # usually two clauses too.
    max_words: int = 0


# The table. Read it as: what does this act need in order to sound like a
# person, and what is the most it may ever be.
CONTRACTS: dict[str, StyleContract] = {
    # "hey" back. A greeting that advertises services is the single most
    # assistant-sounding thing in the product.
    GREET: StyleContract(GREET, max_sentences=2, offers_allowed=0,
                         may_reword=True, max_words=20),
    # "ok", "thanks", "no". Anything longer than a clause is the model
    # restarting a conversation the person just closed.
    RECEIPT: StyleContract(RECEIPT, max_sentences=2, offers_allowed=0,
                           may_reword=True, max_words=20),
    ANSWER: StyleContract(ANSWER, max_sentences=4, offers_allowed=1,
                          may_reword=True),
    # The tool-result boundary. Reworded, but under the trusted-result
    # rules -- values and action status survive verbatim, mechanics do not.
    REPORT: StyleContract(REPORT, max_sentences=3, offers_allowed=1,
                          may_reword=True),
    # Locked: the answer is classified against these exact words.
    ASK: StyleContract(ASK, max_sentences=2, offers_allowed=0,
                       may_reword=False),
    OFFER: StyleContract(OFFER, max_sentences=2, offers_allowed=1,
                         may_reword=True),
    # Locked, and for a stronger reason than ASK: consent.
    CONFIRM: StyleContract(CONFIRM, max_sentences=2, offers_allowed=1,
                           may_reword=False),
    DECLINE: StyleContract(DECLINE, max_sentences=2, offers_allowed=1,
                           may_reword=True),
    REACT: StyleContract(REACT, max_sentences=2, offers_allowed=0,
                         may_reword=True, max_words=45),
    # Two short clauses, because that is what a goodbye is: "Alright, take
    # care. See you later." A one-sentence ceiling condemned ordinary
    # speech, which is the instrument being wrong rather than the reply.
    CLOSE: StyleContract(CLOSE, max_sentences=2, offers_allowed=0,
                         may_reword=True, max_words=20),
}


def contract_for(act: str) -> StyleContract:
    """The contract for this act, or the answer contract if it is unknown.

    An unknown act must not be treated as unconstrained: ANSWER is the
    ordinary case and the safe default.
    """
    return CONTRACTS.get(str(act or "").strip().lower(), CONTRACTS[ANSWER])


# --------------------------------------------------------------------------
# Failure classes
# --------------------------------------------------------------------------

SERVICE_PHRASING = "service_phrasing"
ROBOTIC_ACKNOWLEDGEMENT = "robotic_acknowledgement"
REQUEST_RESTATED = "request_restated"
DUPLICATE_OFFER = "duplicate_offer"
EMPTY_RESPONSE = "empty_response"
STIFF_FOLLOWUP = "stiff_followup"
REACTIVATION_AWKWARD = "reactivation_awkward"
TOOL_NARRATION = "tool_narration"
INTERNAL_LANGUAGE = "internal_language"
UNNATURAL_CONFIRMATION = "unnatural_confirmation"
TOO_VERBOSE = "too_verbose"
STRUCTURAL_ARTIFACT = "structural_artifact"
SELF_REPETITION = "self_repetition"

# How alike two clauses have to be before the second one is the first one
# said again. Set from the measured Korean case (a dropped particle scores
# about 0.95) and checked against sentences that merely share a topic,
# which land far below it. High on purpose: the cost of a false positive
# here is a rewrite of a perfectly good sentence.
_NEARLY_THE_SAME_SENTENCE = 0.85
LIST_RECITAL = "list_recital"
REGISTER_DRIFT = "register_drift"

FAILURE_CLASSES = (
    SERVICE_PHRASING,
    ROBOTIC_ACKNOWLEDGEMENT,
    REQUEST_RESTATED,
    DUPLICATE_OFFER,
    EMPTY_RESPONSE,
    STIFF_FOLLOWUP,
    REACTIVATION_AWKWARD,
    TOOL_NARRATION,
    INTERNAL_LANGUAGE,
    UNNATURAL_CONFIRMATION,
    TOO_VERBOSE,
    STRUCTURAL_ARTIFACT,
    SELF_REPETITION,
    LIST_RECITAL,
    REGISTER_DRIFT,
)


@dataclass(frozen=True)
class StyleFinding:
    """One reason a draft does not sound like her.

    ``structural`` separates the two kinds. A leaked ``[Task Planner]``
    marker or a stray closing quote is an artifact -- no phrasing choice
    produces it, and removing it is a repair. "I'd be happy to assist you"
    is a *register*, and the fix for a register is to say the thing again,
    not to cut the sentence out and leave the rest sounding the same.
    """

    failure: str
    evidence: str
    structural: bool = False


# --- customer service -----------------------------------------------------
#
# The register of a support desk: deference, apology for nothing, and an
# offer of further assistance. What makes these detectable is that they are
# all *about the service relationship* rather than about the subject.
_SERVICE = (
    r"\bis\s+there\s+anything\s+else\b",
    r"\banything\s+else\s+(?:i\s+can|you(?:'d| would)\s+like)\b",
    r"\bhow\s+(?:may|can)\s+i\s+(?:help|assist)\b",
    r"\bi(?:'m| am)\s+(?:here\s+to|happy\s+to|glad\s+to)\s+(?:help|assist)\b",
    r"\bhappy\s+to\s+assist\b",
    r"\bat\s+your\s+service\b",
    r"\bthank\s+you\s+for\s+your\s+(?:patience|understanding)\b",
    r"\bi\s+apologi[sz]e\s+for\s+(?:the\s+)?(?:inconvenience|confusion|any)\b",
    r"\bplease\s+(?:feel\s+free\s+to|do\s+not\s+hesitate|don't\s+hesitate)\b",
    r"\bplease\s+let\s+me\s+know\b",
    # "Let me know what you need" is the front desk asking how it may
    # direct your call. Distinct from "let me know which one you'd
    # prefer", which names a real thing in the conversation.
    r"\blet\s+me\s+know\s+(?:what|how)\s+(?:you|i)\s+"
    r"(?:need|want|can|should)\b",
    r"\bi(?:'m|’m| am)\s+here\s+(?:for\s+you|to\s+support|if\s+you\s+need)\b",
    r"\bfeel\s+free\s+to\s+(?:ask|reach|let)\b",
    r"\blet\s+me\s+know\s+if\s+you\s+(?:need|want|require)\s+"
    r"(?:help\s+with\s+)?anything\b",
    r"\bassist\s+you\s+(?:with|today)\b",
    r"\bhow\s+can\s+i\s+be\s+of\s+(?:help|assistance)\b",
    r"\byour\s+request\s+has\s+been\b",
    r"\bwe\s+apologi[sz]e\b",
)

# --- canned receipts ------------------------------------------------------
#
# A whole sentence whose only content is "message received". Not the words
# themselves -- "got it, the 12 hour one" is fine and says something. The
# failure is the bare form standing alone, which is what the anchors test.
_CANNED_RECEIPT = (
    r"^\s*(?:got\s+it|understood|noted|acknowledged|affirmative|"
    r"copy\s+that|roger(?:\s+that)?|message\s+received|duly\s+noted)\s*[.!]?\s*$",
    r"^\s*(?:sure\s+thing|absolutely|certainly|of\s+course|very\s+well|"
    r"as\s+you\s+wish)\s*[.!]?\s*$",
    r"^\s*(?:okay|ok|alright|right)\s*[.!]?\s*$",
)

# --- restating the request ------------------------------------------------
#
# Opening by saying back what was just said. Distinct from a genuine
# clarifying question ("do you mean the 27 inch one?") because it adds no
# information: it is the user's own sentence with a pronoun flipped.
_RESTATEMENT_OPENER = (
    r"^\s*so,?\s+you(?:'re|\s+are|\s+want|\s+need|\s+said|'ve|\s+have)\b",
    r"^\s*you(?:'re|\s+are)\s+(?:asking|wondering|looking|saying|"
    r"interested\s+in)\b",
    r"^\s*(?:you\s+want|you'd\s+like|you\s+would\s+like)\s+me\s+to\b",
    # "I hear you're looking for a monitor" gives the request back. "I hear
    # you." is sympathy, and one of the most human things she says -- so
    # the frame only counts when something follows the pronoun.
    r"^\s*(?:it\s+sounds\s+like\s+you|"
    r"i\s+(?:see|hear)\s+(?:that\s+)?you)"
    r"(?:'re|\s+are|\s+want|\s+need|\s+have|\s+would)\b",
    r"^\s*(?:regarding|as\s+for|about)\s+your\s+(?:question|request|message)\b",
    r"^\s*(?:your|the)\s+(?:question|request)\s+(?:is|was)\s+about\b",
)

# --- stiff follow-ups -----------------------------------------------------
#
# The bureaucratic form of an offer. She is allowed to offer -- "want me to
# pull that up?" is fine and stays -- but not in the register of a form.
_STIFF_FOLLOWUP = (
    r"\bwould\s+you\s+like\s+me\s+to\s+(?:proceed|continue|go\s+ahead)\b",
    r"\bdo\s+you\s+want\s+me\s+to\s+go\s+ahead\b",
    r"\bshall\s+i\s+proceed\b",
    r"\bplease\s+confirm\b",
    r"\blet\s+me\s+know\s+(?:how\s+you(?:'d| would)\s+like\s+to\s+proceed|"
    r"if\s+you(?:'d| would)\s+like\s+to\s+proceed)\b",
    r"\bif\s+you\s+have\s+any\s+(?:other\s+)?(?:questions|concerns)\b",
    r"\bkindly\s+\w+",
    r"\bat\s+your\s+earliest\s+convenience\b",
    r"\bshould\s+you\s+(?:require|need|wish)\b",
)

# --- awkward task reactivation -------------------------------------------
#
# Coming back to a subject the conversation left. A person says "right, the
# mouse"; these say it the way a ticket system would, and the last two are
# the shape that leaks the internal model of a task being on or off.
_REACTIVATION = (
    r"\b(?:returning|going\s+back|reverting)\s+to\s+your\s+"
    r"(?:previous|earlier|original)\b",
    r"\bas\s+per\s+your\s+(?:earlier|previous|original)\b",
    r"\byour\s+(?:previous|earlier|original)\s+(?:request|task|query|goal)\b",
    r"\bresuming\s+the\s+(?:previous|earlier|original|task)\b",
    r"\bi\s+(?:was|wasn't|was\s+not)\s+(?:using|working\s+on|tracking)\s+"
    r"(?:the|that|it)\b.{0,40}\bby\s+default\b",
    r"\bthe\s+(?:active|current|open)\s+task\b",
    r"\btask\s+(?:has\s+been\s+)?(?:reactivated|resumed|closed|cleared)\b",
)

# --- tool narration -------------------------------------------------------
#
# Describing the machinery instead of the finding. personality.txt already
# forbids exposing retrieval mechanics; this is what it looks like when the
# instruction does not hold.
_TOOL_NARRATION = (
    r"\bthe\s+(?:search|query|lookup|scan)\s+"
    r"(?:returned|found|gave|produced|yielded|showed)\b",
    r"\b(?:according\s+to|based\s+on)\s+the\s+"
    r"(?:results?|search|data|information\s+(?:i\s+)?(?:found|retrieved))\b",
    r"\bi\s+(?:ran|performed|executed|conducted|initiated)\s+"
    r"(?:a\s+|the\s+)?(?:search|query|lookup|scan|command|action)\b",
    r"\bthe\s+(?:tool|system|planner|agent|browser|api)\s+"
    r"(?:returned|reported|responded|says?|indicates?)\b",
    r"\bi\s+(?:have\s+)?(?:completed|performed|executed)\s+the\s+"
    r"(?:action|task|request|operation)\b",
    r"\bthe\s+results?\s+(?:show|indicate|suggest|include)\b",
    r"\bfrom\s+(?:the\s+)?(?:search\s+results|retrieved\s+(?:data|pages))\b",
    r"\bi\s+found\s+\d+\s+results?\b",
    r"\bno\s+results?\s+(?:were\s+)?(?:returned|found)\b",
    r"\bquery\s+(?:was\s+)?(?:sent|issued|executed)\b",
)

# --- internal / system vocabulary ----------------------------------------
#
# Words from the inside of the program. Two shapes: a literal marker that
# only exists in a log line, and the vocabulary of the architecture spoken
# aloud. The second list is deliberately short -- "state", "task" and
# "candidate" are ordinary English and cannot be banned; only their
# unmistakably technical collocations are.
_INTERNAL_MARKER = (
    r"\[[A-Z][A-Za-z ]{2,24}\]",                    # [Task Planner], [Router]
    r"^\s*(?:assistant|user|system)\s*:",           # role labels
    r"\b(?:sub[- ]?goal|verification\s+level|intent\s+(?:label|router)|"
    r"capability\s+(?:check|selection)|action\s+contract|"
    r"consent\s+gate|locked\s+response|forced\s+response)\b",
    r"\bnum_predict\b|\bkeep_alive\b|\btemperature\s*=\s*\d",
    r"\b(?:none|null|n/?a|undefined)\s*$",
    r"^\s*step\s+\d+\s*[:.]",                       # "Step 1: Identify..."
    r"\bas\s+an?\s+(?:ai|language\s+model|assistant)\b",
    r"\bi\s+am\s+an?\s+(?:ai|language\s+model)\b",
    r"\bmy\s+(?:training\s+data|knowledge\s+cutoff|system\s+prompt)\b",
    r"\b(?:json|payload|schema|parameter|boolean)\b",
    # An identifier, not a word. "Happy to dig into a product_recommendation
    # if that helps" reached the user verbatim, because the offer is
    # appended *after* the speech filter has run and nothing turned the
    # underscore back into a space.
    r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b",
    # "[id=6e663719-e0]" -- an accessibility-tree handle, spoken aloud.
    r"\[\s*\w+\s*=[^\]]*\]",
    # "Completed: Window: ChatGPT Button: 닫기" -- a record printed rather
    # than a sentence said. One label is punctuation; two of them in one
    # breath is a data structure being read out.
    r"(?:[A-Z][A-Za-z]{2,14}:\s+\S+\s+){2,}",
)

# --- unnatural confirmation ----------------------------------------------
#
# Saying a thing succeeded the way a return code would. The register, not
# the claim: whether the claim is true is the grounding guards' business.
_UNNATURAL_CONFIRMATION = (
    r"\b(?:action|task|operation|request|process)\s+"
    r"(?:completed|complete|executed|performed)\s*"
    r"(?:successfully)?\s*[.!]",
    r"\bsuccessfully\s+(?:completed|executed|performed|opened|closed|"
    r"clicked|typed|launched|created)\b",
    # A bare completion token as a whole sentence, wherever it sits.
    # Anchoring it to the whole reply missed "Finished. Movie
    # recommendations: ..." -- the status light was still there, it just
    # had a sentence after it.
    r"(?:^|(?<=[.!?]\s))\s*(?:confirmed|done|complete|completed|success|"
    r"all\s+set|finished)\s*[.!]",
    r"^\s*(?:confirmed|done|complete|completed|success|all\s+set|"
    r"finished)\s*[.!]?\s*$",
    # "15% of 84 is 12.60. The calculation is done." A person gives
    # the answer; they do not then announce that producing it has
    # concluded.
    #
    # The noun is a closed set on purpose, and the set is the machine's
    # own activities. "The soup is done" is the same frame and is
    # ordinary English, so the frame alone cannot decide -- what makes
    # the sentence a status line is that its subject is a piece of work
    # the program did.
    r"\bthe\s+(?:calculation|search|lookup|query|task|action|"
    r"operation|request|process|update|download|upload|analysis|"
    r"scan)\s+(?:is|has\s+been|was)\s+"
    r"(?:done|complete|completed|finished|processed|successful)\b",
    r"\bthe\s+(?:operation|action)\s+was\s+successful\b",
    r"\bstatus\s*:\s*(?:success|complete|done|ok)\b",
)

# --- structural artifacts -------------------------------------------------
#
# Damage rather than register. Every one of these is something no speaker
# would produce, and every one is safe to repair without judgement.
_UNBALANCED_QUOTE = re.compile(r'"')
_STRAY_QUOTE_TAIL = re.compile(r'["”]\s*[.!?]?\s*$')
_MARKDOWN_LEFTOVER = re.compile(r"\*\*|__|^\s*[-*+]\s+|^\s*#{1,6}\s+|```",
                                re.MULTILINE)
# A reply built by joining sentences with semicolons -- what
# ``merge_extra_sentences`` produces when it meets its target by stitching.
#
# Two shapes, because a semicolon is legitimate punctuation and the rule
# has to say which uses are not. Several of them in one breath is a list
# wearing a disguise. And one of them followed by a capital letter is a
# full stop that was overwritten: measured live, "just the right amount of
# light; Just make sure to keep the volume down" -- the capital J is the
# sentence boundary saying where it used to be.
#
# Korean has no capitals, so the capital-letter half of that rule was
# one of the guards that silently did nothing in Korean. Measured live:
# "관객들의 호응이 좋습니다; 극장에서 보는 게 제일 좋습니다." A semicolon
# followed by a Hangul syllable is the same lost full stop.
_SEMICOLON_CHAIN = re.compile(r";[^;]{1,120};|;\s+[A-Z]|;\s*[가-힯]")
_DOUBLE_PUNCT = re.compile(r"[.!?]{2,}(?<!\.\.\.)|\s+[,;]\s*[.!?]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"\b[\w'$%-]+\b", re.UNICODE)


# --- register ------------------------------------------------------------
#
# LANGUAGES: en, ko -- and the Korean half is the only one implemented,
# because English has no honorific system to drift within. Stated out loud
# because a guard that quietly covers one language is worse than no guard:
# it reports clean and enforces nothing.
#
# personality_ko.txt specifies 습니다체. Measured live, qwen3:8b drifts to
# 해요체 constantly and the prompt does not hold it -- "퇴근하셨다니
# 힘들었겠어요", "직접 검색해서 보시는 게 더 좋아요". Both are polite and
# both are the wrong person.
#
# -ㄹ까요 is deliberately absent from this list. "진행할까요?" is the normal
# way to ask permission inside 습니다체 and appears in her own line banks;
# flagging it would condemn the register it is trying to enforce.
# 습니다체, stated as what it *is* rather than as what it is not.
#
# A declarative ends in -니다 (입니다 / 합니다 / 겠습니다). A question ends
# in -니까 or, when asking permission, -ㄹ까요 -- which is standard inside
# this register and appears in her own line banks, so it is allowed rather
# than flagged. An instruction ends in -십시오.
#
# Everything else ending in a Hangul syllable is drift, which catches both
# directions at once: 해요체 ("고마워요", "천만에요", "좋아요") and 반말
# ("쉬고 있어", "도와줄게"). The first attempt listed the wrong endings
# instead and missed 반말 entirely -- a rewrite turned 해요체 into 반말 and
# passed, which is a worse register going out under a clean review.
_FORMAL_ENDING = re.compile(
    r"(?:니다|니까|십시오|시죠|까요|을까|ㄹ까)\s*[.!?~]*$"
)
# Said on their own, and correct in this register whatever their form.
# "네." would otherwise read as drift for ending in a bare syllable, and
# the greetings are lexicalised: 안녕하세요 is -세요 by shape and is the
# standard polite greeting in every register including this one. A rule
# about grammar has to know which phrases stopped being grammar.
_FORMAL_ALONE = frozenset({
    "네", "예", "아니요", "아니오", "물론",
    "안녕하세요", "안녕하십니까", "안녕히 계세요", "안녕히 가세요",
    "어서 오세요", "좋은 아침입니다",
})


def _drifts_from_the_register(text: str) -> str:
    """A Korean sentence not written in the 습니다체 she is specified in."""
    for sentence in sentences(text):
        stripped = sentence.strip()
        if not _HANGUL_SYLLABLE.search(stripped):
            continue
        bare = re.sub(r"[^\w가-힯]+$", "", stripped)
        if bare in _FORMAL_ALONE:
            continue
        # The sentence has to *end* in Hangul to be judged at all: a line
        # ending in a number, a price or a Latin product name says nothing
        # about register.
        if not re.search(r"[가-힯][\s.!?~]*$", stripped):
            continue
        if _FORMAL_ENDING.search(stripped):
            continue
        return stripped[:90]
    return ""


# Korean customer service. The same register failure as the English list,
# in the language where it actually sounds like a call centre.
_KOREAN_SERVICE = (
    r"더\s*궁금하신\s*(?:점|것|거)",
    r"필요하시면\s*언제든",
    r"언제든(?:지)?\s*(?:말씀|문의|연락)",
    r"무엇이든\s*도와",
    r"도움이\s*필요하시면",
    r"다른\s*(?:질문|문의|도움)(?:은|이)?\s*있으",
    r"불편(?:을)?\s*드려\s*죄송",
    r"기꺼이\s*도와",
)
_KOREAN_SERVICE_RE = re.compile("|".join(_KOREAN_SERVICE))

_HANGUL_SYLLABLE = re.compile(r"[가-힯]")


def _compiled(patterns) -> re.Pattern:
    return re.compile("|".join(patterns), re.IGNORECASE)


_SERVICE_RE = _compiled(_SERVICE)
_CANNED_RECEIPT_RE = _compiled(_CANNED_RECEIPT)
_RESTATEMENT_RE = _compiled(_RESTATEMENT_OPENER)
_STIFF_RE = _compiled(_STIFF_FOLLOWUP)
_REACTIVATION_RE = _compiled(_REACTIVATION)
_TOOL_NARRATION_RE = _compiled(_TOOL_NARRATION)
_INTERNAL_RE = re.compile("|".join(_INTERNAL_MARKER),
                          re.IGNORECASE | re.MULTILINE)
_CONFIRMATION_RE = _compiled(_UNNATURAL_CONFIRMATION)

# An offer to *act*, counted so that two of them in one reply can be. Kept
# separate from ClosingOfferGuard's list, which decides what to strip; this
# one only counts.
_OFFER_RE = re.compile(
    r"\bwant\s+me\s+to\b|\bwould\s+you\s+like\s+me\s+to\b|"
    r"\bshall\s+i\b|\bshould\s+i\s+(?:look|search|check|find|pull|show|open)\b|"
    r"\bi\s+(?:can|could|'ll|will)\s+(?:also\s+)?"
    r"(?:look|search|check|find|pull|show|open|dig)\b|"
    r"\bwould\s+you\s+like\s+(?:help|me)\b",
    re.IGNORECASE,
)

# Content-free replies. "Done." with nothing else is a status light, not an
# answer; whether it is *allowed* depends on the act, which is why this is a
# shape test and the act decides.
_CONTENT_FREE = re.compile(
    r"^\s*(?:all\s+set|done|okay|ok|sure|alright|got\s+it|understood|"
    r"finished|complete[d]?|confirmed|no\s+problem|you(?:'re|\s+are)\s+"
    r"welcome)\s*[.!]?\s*$",
    re.IGNORECASE,
)


# Reciting a catalogue. A person names two or three things and stops;
# "browser control, web search, desktop control, screen vision, multi-step
# tasks, memory, calendar, project access" is a registry printed as prose,
# and it is what an inventory sounds like when nothing turns it into speech.
#
# The threshold is deliberately high, and it is a shape rather than a
# count. Three or four commas is ordinary English -- "the M330 is a great
# option, it's light, has long battery life, and works with both" is how
# anyone describes a product, and calling that a recital would condemn
# normal speech. What distinguishes a catalogue is that it is *long* and
# that its items are bare labels: five or more of them, most only a word
# or two, with no clause among them.
_LIST_ITEM_WORDS = 5
_LIST_ITEMS = 5


def _reads_as_catalogue(sentence: str) -> str:
    """The catalogue in this sentence, or an empty string."""
    parts = [part.strip() for part in str(sentence).split(",")]
    parts = [part for part in parts if part]
    if len(parts) < _LIST_ITEMS:
        return ""
    short = sum(
        1 for part in parts
        if len(_WORD.findall(part)) <= _LIST_ITEM_WORDS
    )
    # All but the lead-in have to be bare labels. One long tail is fine;
    # a sentence of real clauses is not a catalogue however many commas
    # it happens to carry.
    return sentence.strip() if short >= len(parts) - 1 else ""
# "whether it's someone to talk to, a distraction, or just a quiet moment" --
# a menu of options offered in place of a response. The give-away is the
# menu frame, not the commas.
_OPTION_MENU = re.compile(
    # Both apostrophes: the model writes the curly one, and a pattern
    # that only knows the straight one silently matches nothing she
    # ever actually says.
    r"whether\s+(?:it(?:['’]s| is)|you(?:['’]d| would)?)"
    r"[^.?!]*,[^.?!]*or",
    re.IGNORECASE,
)


# Words that carry no subject matter, so repeating them says nothing about
# whether a sentence is repeating the person. A closed class, kept small:
# it only has to be good enough that a run of three survivors means the
# sentence is genuinely saying the same thing back.
_FUNCTION_WORDS = frozenset("""
a an the this that these those my your our their its his her
is are was were be been am do does did done have has had
i you he she it we they me him us them
and or but so then than as if of in on at to for from with by
about into over under out up down off again just too also very
not no nor own same such only more most much many few
can could will would shall should may might must
what which who whom whose when where why how
""".split())


# How many of the person's opening words a reply may repeat before it is
# giving them back rather than answering. One is a coincidence in any
# language ("Yes." answering "Yes?"); two is the sentence starting again.
_ECHOED_OPENING_WORDS = 2


def _opens_with_their_words(sentence: str, said: str) -> str:
    """The person's own opening, handed back at the start of the reply."""
    theirs = re.findall(r"[^\W_]+", str(said).casefold(), flags=re.UNICODE)
    mine = re.findall(r"[^\W_]+", str(sentence).casefold(), flags=re.UNICODE)
    shared = 0
    for theirs_word, mine_word in zip(theirs, mine):
        if theirs_word != mine_word:
            break
        shared += 1
    if shared < _ECHOED_OPENING_WORDS:
        return ""
    return " ".join(mine[:shared])


def _content_run(sentence: str, said: str) -> str:
    """The longest stretch of the person's own words this sentence gives back.

    Restating is not a phrase, it is a relation between two sentences, and
    the pattern list above can only ever catch the frames someone thought
    of. "So, you're back from work?" was in it; "So, just got back from
    work too?" was not, and is the same failure. Three content words in a
    row is the threshold -- two is a coincidence in any conversation about
    one subject.
    """
    theirs = [
        word for word in re.findall(r"[^\W_]{2,}", str(said).casefold())
        if word not in _FUNCTION_WORDS
    ]
    if len(theirs) < 3:
        return ""
    mine = [
        word for word in re.findall(r"[^\W_]{2,}", str(sentence).casefold())
        if word not in _FUNCTION_WORDS
    ]
    theirs_set = set(theirs)
    run, longest = [], []
    for word in mine:
        if word in theirs_set:
            run.append(word)
            if len(run) > len(longest):
                longest = list(run)
        else:
            run = []
    if len(longest) < 3:
        return ""
    # An answer is allowed to name what it is about. What separates giving
    # the words back from using them is whether anything new came with
    # them: "I found a good wireless mouse under $50, the Logitech G305"
    # repeats three of the person's words and then says something.
    fresh = [word for word in mine if word not in theirs_set]
    return " ".join(longest) if len(fresh) <= 1 else ""


def sentences(text: str) -> list[str]:
    return [
        part.strip()
        for part in _SENTENCE_SPLIT.split(str(text or "").strip())
        if part.strip()
    ]


def word_count(text: str) -> int:
    return len(_WORD.findall(str(text or "")))


class RoboticTells:
    """Find the reasons a draft does not sound like one consistent person.

    The instrument, and only incidentally an enforcement input. It is
    written so the same call can answer both "how many replies in this
    conversation were robotic, and of what kind" and "is this particular
    draft worth saying again" -- which matters, because a quality metric
    that measures something other than what the system enforces will
    disagree with it eventually.
    """

    @classmethod
    def inspect(
        cls,
        text: str,
        *,
        act: str = ANSWER,
        user_input: str = "",
        previous_reply: str = "",
        earlier_replies: tuple[str, ...] = (),
        language: str = "en",
    ) -> tuple[StyleFinding, ...]:
        draft = str(text or "").strip()
        if not draft:
            return ()

        contract = contract_for(act)
        found: list[StyleFinding] = []

        def note(failure: str, match, structural: bool = False) -> None:
            evidence = (
                match.group(0).strip() if hasattr(match, "group")
                else str(match).strip()
            )
            found.append(StyleFinding(failure, evidence[:120], structural))

        for failure, pattern in (
            (SERVICE_PHRASING, _SERVICE_RE),
            (STIFF_FOLLOWUP, _STIFF_RE),
            (REACTIVATION_AWKWARD, _REACTIVATION_RE),
            (TOOL_NARRATION, _TOOL_NARRATION_RE),
            (UNNATURAL_CONFIRMATION, _CONFIRMATION_RE),
        ):
            match = pattern.search(draft)
            if match:
                note(failure, match)

        if str(language or "").strip().lower().startswith("ko"):
            drift = _drifts_from_the_register(draft)
            if drift:
                note(REGISTER_DRIFT, drift)
            service = _KOREAN_SERVICE_RE.search(draft)
            if service:
                note(SERVICE_PHRASING, service)

        marker = _INTERNAL_RE.search(draft)
        if marker:
            # Structural: a role label or a bracketed log tag is damage,
            # and no rewording of it is the correct version.
            note(INTERNAL_LANGUAGE, marker, structural=True)

        opener = sentences(draft)[0] if sentences(draft) else draft
        if not contract.may_restate_request:
            restated = _RESTATEMENT_RE.search(opener)
            if restated:
                note(REQUEST_RESTATED, restated)
            else:
                given_back = (
                    _opens_with_their_words(opener, user_input)
                    or _content_run(opener, user_input)
                )
                if given_back:
                    note(REQUEST_RESTATED, given_back)

        # A canned receipt is only a failure where something was owed. "Got
        # it." answering "ok" is what a person says, and calling it robotic
        # would be the instrument disagreeing with ordinary English; the
        # same words answering a question are the reply going missing. What
        # makes a stock receipt sound generated is saying it *again*, which
        # is the repetition check below and not this one.
        if (
            act not in {RECEIPT, GREET, CLOSE}
            and len(sentences(draft)) == 1
            and _CANNED_RECEIPT_RE.search(draft)
        ):
            note(ROBOTIC_ACKNOWLEDGEMENT, draft)

        offers = len(_OFFER_RE.findall(draft))
        if offers > max(1, contract.offers_allowed):
            note(DUPLICATE_OFFER, f"{offers} offers in one reply")
        elif offers > contract.offers_allowed:
            note(DUPLICATE_OFFER,
                 f"{offers} offer(s) where the {act} act allows "
                 f"{contract.offers_allowed}")

        # Content-free. A receipt is *allowed* to be content-free -- that is
        # what a receipt is -- so this only fires for the acts that owe the
        # person something.
        if act not in {RECEIPT, GREET, CLOSE} and _CONTENT_FREE.match(draft):
            note(EMPTY_RESPONSE, draft)

        if len(sentences(draft)) > contract.max_sentences:
            note(TOO_VERBOSE,
                 f"{len(sentences(draft))} sentences where the {act} act "
                 f"allows {contract.max_sentences}")
        elif 0 < contract.max_words < word_count(draft):
            note(TOO_VERBOSE,
                 f"{word_count(draft)} words where the {act} act allows "
                 f"{contract.max_words}")

        # A catalogue read out. Only in the sentence that carries it, so a
        # long answer with one ordinary list is not condemned for the rest.
        for sentence in sentences(draft):
            catalogue = _reads_as_catalogue(sentence)
            if catalogue:
                note(LIST_RECITAL, catalogue)
                break
        menu = _OPTION_MENU.search(draft)
        if menu:
            note(LIST_RECITAL, menu)

        for finding in cls._artifacts(draft):
            found.append(finding)

        echo = cls._repeats_previous(draft, previous_reply)
        if echo:
            note(SELF_REPETITION, echo)
        else:
            # Not only the turn before. A stock line coming back later in
            # the same session is the tell that there is a bank behind it:
            # measured live, "Sure thing." answered "ok" and then answered
            # "thanks" six turns later, and nothing adjacent was repeated.
            said = " ".join(_WORD.findall(draft.casefold()))
            for earlier in earlier_replies:
                if said and said == " ".join(_WORD.findall(earlier.casefold())):
                    note(SELF_REPETITION, f"said already this session: {draft}")
                    break

        return tuple(found)

    # -- structural ---------------------------------------------------------

    @classmethod
    def _artifacts(cls, draft: str) -> list[StyleFinding]:
        artifacts: list[StyleFinding] = []
        if len(_UNBALANCED_QUOTE.findall(draft)) % 2 == 1:
            artifacts.append(StyleFinding(
                STRUCTURAL_ARTIFACT, "unbalanced quote", structural=True))
        if _MARKDOWN_LEFTOVER.search(draft):
            artifacts.append(StyleFinding(
                STRUCTURAL_ARTIFACT, "markdown left in spoken text",
                structural=True))
        if _SEMICOLON_CHAIN.search(draft):
            artifacts.append(StyleFinding(
                STRUCTURAL_ARTIFACT, "sentences chained with semicolons",
                structural=True))
        if _DOUBLE_PUNCT.search(draft):
            artifacts.append(StyleFinding(
                STRUCTURAL_ARTIFACT, "doubled punctuation", structural=True))
        if draft and draft[-1] not in ".!?…":
            artifacts.append(StyleFinding(
                STRUCTURAL_ARTIFACT, "no terminal punctuation",
                structural=True))
        return artifacts

    @staticmethod
    def _repeats_previous(draft: str, previous: str) -> str:
        """Whether this reply says again what the last one already said.

        Not similarity of the whole reply -- ``ResponseQualityGuard``
        already owns that. Two narrower tells, both of which make a series
        of replies sound generated rather than spoken: the same first four
        words turn after turn, and a whole sentence carried over verbatim.
        The second is what a canned caution does -- "Check the price again
        before buying to make sure it's still in range" arrived twice in
        two consecutive answers.
        """
        if not previous:
            return ""

        def opening(text: str) -> str:
            words = _WORD.findall(str(text).casefold())
            return " ".join(words[:4])

        current, before = opening(draft), opening(previous)
        if current and current == before:
            return current

        def key(sentence: str) -> str:
            return " ".join(_WORD.findall(sentence.casefold()))

        # Short sentences repeat legitimately ("Sure.", "Nice."); a repeated
        # clause of real length is a template, not a coincidence.
        #
        # "Real length" is not the same number of words in both languages.
        # Korean packs a clause into fewer of them, and a flat six-word
        # floor let "편안한 밤 보내시길 바랍니다." -- a whole sentence, four
        # words -- repeat across two consecutive turns unnoticed.
        def long_enough(sentence: str) -> bool:
            words = len(_WORD.findall(sentence))
            if _HANGUL_SYLLABLE.search(sentence):
                return words >= 3
            return words >= 6

        said_before = {
            key(sentence) for sentence in sentences(previous)
            if long_enough(sentence)
        }
        for sentence in sentences(draft):
            current = key(sentence)
            if current in said_before:
                return sentence[:80]
            # Exact equality was the whole test, and it is too strict for
            # the way a model actually repeats itself -- it re-words
            # slightly rather than copying. Measured on an unseen Korean
            # dogfood arc, three consecutive sympathy turns:
            #
            #   잠시 쉬시고, 필요하시면 도움을 드리겠습니다.
            #   잠시 쉬시고, 필요하시면 도움 드리겠습니다.
            #   잠시 쉬시고, 도움이 필요하시면 언제든 말씀해주십시오.
            #
            # One dropped particle and one reordering, and none of the
            # three saw the others. Korean makes this worse because a
            # particle can go without changing the sentence at all, but
            # English does the same thing with articles and adverbs.
            if not long_enough(sentence):
                continue
            for earlier in said_before:
                if difflib.SequenceMatcher(None, current, earlier).ratio() >= (
                    _NEARLY_THE_SAME_SENTENCE
                ):
                    return sentence[:80]
        return ""


# --------------------------------------------------------------------------
# Deterministic repair -- structural only
# --------------------------------------------------------------------------

def repair_structure(text: str) -> str:
    """Fix what is damage rather than style, and touch nothing else.

    Every edit here is one a proof-reader would make without knowing what
    the sentence is about: an unpaired quote, a leaked log marker, a list
    stitched together with semicolons. Register is not repaired -- that is
    what re-realization is for, and cutting a customer-service sentence out
    of a customer-service reply leaves a shorter customer-service reply.
    """
    said = str(text or "")
    if not said.strip():
        return said

    # Log markers and role labels: never part of a spoken sentence.
    said = re.sub(r"\[[A-Z][A-Za-z ]{2,24}\]\s*", "", said)
    said = re.sub(r"(?im)^\s*(?:assistant|user|system)\s*:\s*", "", said)
    said = re.sub(r"(?im)^\s*step\s+(\d+)\s*[:.]\s*", "", said)

    # Markdown that TextFilter's own pass does not reach.
    said = re.sub(r"\*\*|__", "", said)
    said = re.sub(r"(?m)^\s*[-*+]\s+", "", said)
    said = re.sub(r"(?m)^\s*#{1,6}\s+", "", said)

    # A semicolon chain is a list. Say it as sentences.
    if _SEMICOLON_CHAIN.search(said):
        said = re.sub(r"\s*;\s*", ". ", said)
        said = re.sub(r"\.\s*\.", ".", said)
        # Capitalise what became a sentence opening.
        said = re.sub(r"(?<=[.!?]\s)([a-z])",
                      lambda m: m.group(1).upper(), said)

    # An unpaired quote is always the survivor of a truncation.
    if len(_UNBALANCED_QUOTE.findall(said)) % 2 == 1:
        tail = _STRAY_QUOTE_TAIL.search(said)
        if tail:
            said = said[:tail.start()] + tail.group(0).replace('"', "")
        else:
            said = said.replace('"', "", 1)

    # Any run of terminal punctuation, not only a repeated one. Measured
    # live: "Just open Netflix and start watching!." -- two different marks,
    # which the repeated-character form left alone. An ellipsis is spared,
    # being one mark spelled with three characters.
    said = re.sub(
        r"[.!?]{2,}",
        lambda run: run.group(0) if run.group(0) == "..." else run.group(0)[0],
        said,
    )
    said = re.sub(r"\s+([,;])\s*([.!?])", r"\2", said)
    said = re.sub(r"[ \t]{2,}", " ", said)
    said = said.strip()

    # A reply starts with a capital letter. Measured live, a whole arc of
    # replies came back lowercase ("i'm here. you don't have to talk if you
    # don't want to.") -- texting register from the model, inconsistent with
    # every other reply in the same conversation, and one voice is the
    # point. Only the first letter, and only when the word is not already
    # deliberately cased (an identifier, an acronym, "iPhone").
    if said[:1].islower() and not re.match(r"^\w+[A-Z]", said):
        said = said[0].upper() + said[1:]

    if said and said[-1] not in ".!?…":
        said = said + "."
    return said


def speakable_fragment(text: str) -> str:
    """This fragment if a person could say it, or an empty string.

    For the places that build a sentence by inlining a record the program
    kept for itself -- a step the planner took, an element it matched, a
    window it saw. Those records are written for a log, and a log line
    dropped into speech is the loudest possible way to sound like a
    machine. Measured live, asked what windows were open, she said:

        You took control, so I stopped. Completed: Window: ChatGPT Button:
        최소화 [id=6e663719-e0] Button: 최대화 [id=6e663719-e1] ...

    which is the accessibility tree, read out. The rule is not that those
    records are wrong -- they are exactly right, for a log -- but that the
    boundary between them and speech needs something standing on it.

    Returns the fragment unchanged when it is ordinary language, so a
    genuinely useful step ("opened the settings page") still gets said.
    """
    fragment = " ".join(str(text or "").split())
    if not fragment:
        return ""
    if _INTERNAL_RE.search(fragment):
        return ""
    # A record is mostly labels and identifiers; a sentence is mostly
    # words. Anything with a run of two labelled fields, a bracketed
    # handle, or no verb-like word at all is the first kind.
    if re.search(r"\[[^\]]*\]|\b\w+=\S+", fragment):
        return ""
    if len(re.findall(r"[A-Za-z가-힣]{2,}:\s", fragment)) >= 1:
        return ""
    return fragment


def speakable_list(fragments, limit: int = 2) -> str:
    """The fragments a person could say, joined, or an empty string."""
    said = [
        fragment for fragment in (
            speakable_fragment(item) for item in (fragments or ())
        )
        if fragment
    ]
    return ", ".join(said[-limit:]) if said else ""


# --------------------------------------------------------------------------
# Whether a draft is worth saying again
# --------------------------------------------------------------------------

# Register failures. These are the ones a rewrite can actually fix, because
# the fix is to say the same thing differently. A structural artifact is
# repaired instead, and a length overrun is handled by the length policy.
_REWORDABLE = frozenset({
    SERVICE_PHRASING,
    ROBOTIC_ACKNOWLEDGEMENT,
    REQUEST_RESTATED,
    STIFF_FOLLOWUP,
    REACTIVATION_AWKWARD,
    TOOL_NARRATION,
    UNNATURAL_CONFIRMATION,
    DUPLICATE_OFFER,
    EMPTY_RESPONSE,
    SELF_REPETITION,
    LIST_RECITAL,
    REGISTER_DRIFT,
})


# Acts that carry no facts. Length is a register problem for these and a
# content problem everywhere else: saying "yeah, rough one" in place of
# three sentences of sympathy loses nothing, while shortening an answer or
# a tool report risks dropping the value the person asked for -- which is
# the condenser's job, under its own faithfulness rules.
_LENGTH_IS_STYLE = frozenset({GREET, RECEIPT, REACT, CLOSE})


@dataclass(frozen=True)
class StyleVerdict:
    """What to do with a draft: keep it, repair it, or say it again."""

    findings: tuple[StyleFinding, ...] = ()
    repaired: str = ""
    act: str = ANSWER

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def needs_rewrite(self) -> bool:
        rewordable = set(_REWORDABLE)
        if self.act in _LENGTH_IS_STYLE:
            rewordable.add(TOO_VERBOSE)
        return any(
            finding.failure in rewordable and not finding.structural
            for finding in self.findings
        )

    @property
    def classes(self) -> tuple[str, ...]:
        seen: list[str] = []
        for finding in self.findings:
            if finding.failure not in seen:
                seen.append(finding.failure)
        return tuple(seen)

    def report(self) -> str:
        if not self.findings:
            return ""
        parts = [f"{f.failure}({f.evidence})" for f in self.findings[:4]]
        return "; ".join(parts)


def review(
    text: str,
    *,
    act: str = ANSWER,
    user_input: str = "",
    previous_reply: str = "",
    earlier_replies: tuple[str, ...] = (),
    language: str = "en",
) -> StyleVerdict:
    """Inspect a draft and repair only what is structural.

    Returns both halves so the caller decides what to do with the rewrite
    signal -- some acts are locked and must be reported rather than redone.
    """
    repaired = repair_structure(text)
    findings = RoboticTells.inspect(
        repaired,
        act=act,
        user_input=user_input,
        previous_reply=previous_reply,
        earlier_replies=earlier_replies,
        language=language,
    )
    return StyleVerdict(findings=findings, repaired=repaired, act=act)


# --------------------------------------------------------------------------
# The instruction the model is given
# --------------------------------------------------------------------------

# One paragraph, act-aware, appended to the generation prompt. It exists so
# the *first* draft is usually right; the detector above is what catches the
# times it is not. Kept short deliberately -- measured on this model, a long
# list of style rules competes with the content instructions and loses.
_ACT_GUIDANCE = {
    GREET: "Say hello back in one short line. Do not list what you can do "
           "and do not ask what they need.",
    RECEIPT: "They are acknowledging something, not asking. Answer in one "
             "short clause and do not reopen the topic or offer anything.",
    ANSWER: "Lead with the answer in plain spoken words.",
    REPORT: "Say what happened the way you would tell a friend. Keep every "
            "value and every action status exactly as given, but never "
            "mention searches, tools, results, systems or steps.",
    ASK: "Ask the one thing you need, in one short question.",
    OFFER: "Make one offer, casually, in your own words.",
    CONFIRM: "Ask for the go-ahead in one plain sentence.",
    DECLINE: "Say plainly what you cannot do, without apologising twice.",
    REACT: "React to what they said like a friend would. Do not solve "
           "anything unless they asked.",
    CLOSE: "Say goodbye in one short line.",
}


# --------------------------------------------------------------------------
# Which act is this turn performing?
# --------------------------------------------------------------------------

# Words a turn can be made *entirely* of and still be saying only "I heard
# you". A closed class, in the sense the project already uses elsewhere: the
# test is that nothing outside the set is present, never that some phrase
# inside it is. That is what keeps "no" a receipt and "no, the other one" an
# instruction -- the second carries words this set does not contain.
_RECEIPT_WORDS = frozenset("""
ok okay okey k kk kay alright allright right sure yeah yea yep yup yes
no nah nope not never
thanks thank thanx thx ty cheers
cool nice great good perfect awesome lovely fine fair
got it understood gotcha noted
lol haha hah heh hehe hmm hm mhm mm mmm oh ah aha ooh wow huh
i ll try that do will can well so and but then just now
you your my me a an the is are was s t m re ve d
please much a-lot
응 어 네 예 아니 아니요 그래 좋아 알겠어 알았어 고마워 감사 ㅇㅋ ㅋㅋ ㅎㅎ 응응
""".split())

_RECEIPT_MAX_WORDS = 6


def reads_as_receipt(text: str) -> bool:
    """Whether this turn only acknowledges, and asks for nothing.

    "ok", "thanks", "nah", "ok i'll try that", "that's fine, thanks". Not
    "ok but which one" -- "which" and "one" are outside the set, and the
    turn is a question wearing an "ok".

    A question mark disqualifies outright: whatever else it is doing, a
    turn that asks something wants an answer, and answering it in the one
    clause a receipt allows would be the wrong kind of brief.
    """
    said = str(text or "").strip()
    if not said or "?" in said:
        return False
    # Contractions split into their parts here rather than staying whole:
    # "that's" is "that" and "s", both closed-class, and keeping it as one
    # token would mean listing every contraction of every word in the set.
    words = re.findall(r"[^\W_]+", said.casefold(), flags=re.UNICODE)
    if not words or len(words) > _RECEIPT_MAX_WORDS:
        return False
    return all(word in _RECEIPT_WORDS for word in words)


def act_for_turn(
    *,
    intent: str = "",
    speech_act: str = "",
    user_input: str = "",
    action_performed: bool = False,
    has_tool_result: bool = False,
    awaiting_consent: bool = False,
    awaiting_clarification: bool = False,
    is_greeting: bool = False,
    is_farewell: bool = False,
) -> str:
    """Name the act this turn's reply performs.

    Ordered most specific first, and the order is the whole design. The two
    locked acts are tested before anything else: a turn that is waiting for
    a yes or for a missing detail must never be reclassified into something
    a rewrite is allowed to touch, no matter what else is true about it.
    """
    if awaiting_clarification:
        return ASK
    if awaiting_consent:
        return CONFIRM
    if is_farewell:
        return CLOSE
    if is_greeting:
        return GREET
    if reads_as_receipt(user_input):
        return RECEIPT
    # Something actually happened on the machine, or a tool produced the
    # content. Either way the words came from somewhere that does not
    # speak, which is exactly what the report contract exists for.
    if action_performed or has_tool_result:
        return REPORT
    if speech_act == "social":
        return REACT
    if speech_act == "statement" and intent == "conversation":
        return REACT
    return ANSWER


def style_instruction(act: str = ANSWER, language: str = "en") -> str:
    """The response-style rules for this act, as prompt text."""
    contract = contract_for(act)
    lines = [
        "HOW TO SOUND",
        "You are one person talking, not an interface reporting.",
        _ACT_GUIDANCE.get(contract.act, _ACT_GUIDANCE[ANSWER]),
        (f"At most {contract.max_words} words."
         if contract.max_words else
         f"At most {contract.max_sentences} "
         f"sentence{'s' if contract.max_sentences != 1 else ''}."),
        "Never open by repeating what they just said.",
        "No customer-service lines: no 'anything else', no 'happy to "
        "help', no 'please let me know', no apologising for nothing.",
        "Do not describe searches, tools, systems, steps or internal state.",
    ]
    if contract.offers_allowed == 0:
        lines.append("Do not offer to do anything in this reply.")
    else:
        lines.append("At most one offer, and only if it is genuinely useful.")
    if str(language or "").strip().lower().startswith("ko"):
        # Said in Korean, because a rule about Korean grammar written in
        # English is one translation away from being ignored. Measured:
        # qwen3:8b drifts to 해요체 on most turns and the personality file
        # alone does not hold it.
        lines.append(
            "한국어로 답할 때는 반드시 습니다체로 말합니다. "
            "'~요'로 끝나는 해요체는 쓰지 않습니다. "
            "예: '확인했어요' (X) / '확인했습니다' (O). "
            "허락을 구하는 질문만 '~할까요?' 형태를 씁니다."
        )
    return "\n".join(lines)
