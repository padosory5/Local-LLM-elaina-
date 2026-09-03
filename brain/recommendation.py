"""When to offer something nobody asked for, and how often not to.

Phase 4E.2 worked out that an action would help and that the user had not
asked for it -- ``InteractionDecision.mode == "recommend"``. Nothing read it.
Every offer Elaina made was a *repair*: the model had promised an action it
never performed, or quoted a number it never checked, and an offer was the
remedy. She never simply noticed that something would help.

This decides two things, and deliberately owns nothing else:

* **Whether to offer.** The brief's rule is that level 1 skips permission
  "if clearly necessary to answer the user's request" -- and that *if* is
  the whole distinction. When the lookup is needed, 4E.2 routes it straight
  to ``execute`` and nothing is asked. When it is optional extra effort on
  top of an answer already given, offering is the point: "I'm thinking about
  getting a new monitor" deserves "I can pull up a few current options", and
  that is a level 1 search. So the level does not gate offering; ``mode ==
  recommend`` does, and by construction that means the action was neither
  asked for nor needed. Level 3 keeps the approval walls it already has in
  ``security/``; this never weakens them and never stands in for them.

* **How often not to.** An assistant that ends every answer with "I can look
  that up if you want" is worse than one that never offers, because the
  offer stops meaning anything. A global gap between offers, a longer one
  per capability, and a longer one still after a refusal.

Parking the offer stays with :class:`~security.capability_offer.
CapabilityOfferGate` -- the place that already turns a later "ok" back into
the action. Nothing here duplicates it.

Contentless status lines belong to :mod:`brain.action_status`; these name a
capability and a subject, which is why they are phrased here instead.
"""

from __future__ import annotations

import random
import re
from collections import deque
from dataclasses import dataclass


# Turns between any two proactive offers. Measured against the complaint
# that started Phase 4E: repetition is what makes a fair offer feel canned.
DEFAULT_TURN_GAP = 3

# Longer for the same ability twice, and longer again after a "no thanks" --
# a refusal is information, and re-asking spends it.
DEFAULT_CAPABILITY_GAP = 8
DEFAULT_DECLINED_GAP = 15


@dataclass(frozen=True)
class Recommendation:
    """One offer, ready to say and ready to park."""

    capability: str
    text: str
    goal: str


# Phrasings. Each names the ability and what it is for, because an offer the
# user cannot picture is not an offer. ``{what}`` is the subject; ``{name}``
# is the capability's own name from the registry.
#
# None of these may contain a generic closing marker ("anything else", "feel
# free to ask"), or ClosingOfferGuard would strip them as filler on the way
# out -- a test asserts that.
_PHRASINGS = {
    "en": (
        "I can pull up {what} if you want.",
        "Want me to look into {what}?",
        "I could check {what} for you -- worth it?",
        "Happy to dig into {what} if that helps.",
        "Say the word and I'll go through {what}.",
        "There's more on {what} I could find, if you'd like.",
    ),
    "ko": (
        "원하면 {what} 찾아볼 수 있어.",
        "{what} 한번 알아볼까?",
        "{what} 확인해줄까?",
        "필요하면 {what} 좀 더 볼게.",
        "말만 하면 {what} 찾아볼게.",
    ),
}

DEFAULT_LANGUAGE = "en"


class RecommendationPolicy:
    """Decide whether to offer, and phrase it differently each time.

    One per session. The counters have to outlive a turn, because that is
    the only scale at which "she keeps offering" is visible at all.
    """

    def __init__(
        self,
        *,
        language: str = DEFAULT_LANGUAGE,
        turn_gap: int = DEFAULT_TURN_GAP,
        capability_gap: int = DEFAULT_CAPABILITY_GAP,
        declined_gap: int = DEFAULT_DECLINED_GAP,
        rng: random.Random | None = None,
    ) -> None:
        self.language = language if language in _PHRASINGS else DEFAULT_LANGUAGE
        self.turn_gap = max(0, int(turn_gap))
        self.capability_gap = max(0, int(capability_gap))
        self.declined_gap = max(0, int(declined_gap))
        self._rng = rng if rng is not None else random.Random()
        self._turn = 0
        self._last_offer_turn: int | None = None
        self._last_by_capability: dict[str, int] = {}
        self._declined_until: int | None = None
        self._recent: deque[str] = deque(maxlen=4)

    # ------------------------------------------------------------- turns

    def begin_turn(self) -> None:
        """Count one exchange. Cooldowns are measured in turns, not seconds.

        A conversation that pauses for lunch should not become a licence to
        start offering again; what matters is how many things she has said
        since the last one.
        """
        self._turn += 1

    def note_declined(self) -> None:
        """The user said no. Back off further than an ordinary gap."""
        self._declined_until = self._turn + self.declined_gap

    def note_accepted(self) -> None:
        """The user said yes -- the offer was welcome, so no penalty."""
        self._declined_until = None

    def reset(self) -> None:
        self._turn = 0
        self._last_offer_turn = None
        self._last_by_capability.clear()
        self._declined_until = None
        self._recent.clear()

    # ------------------------------------------------------------ decide

    def should_offer(self, decision, capability_id: str) -> bool:
        """Whether an offer now would add something rather than nag."""
        if str(getattr(decision, "mode", "")) != "recommend":
            return False
        if not str(capability_id or "").strip():
            return False
        if self._declined_until is not None and self._turn < self._declined_until:
            return False
        if (
            self._last_offer_turn is not None
            and self._turn - self._last_offer_turn < self.turn_gap
        ):
            return False
        last = self._last_by_capability.get(capability_id)
        if last is not None and self._turn - last < self.capability_gap:
            return False
        return True

    def offer(
        self,
        decision,
        *,
        capability_id: str,
        capability_name: str = "",
        subject: str = "",
    ) -> Recommendation | None:
        """The offer to make now, or ``None`` to stay quiet."""
        if not self.should_offer(decision, capability_id):
            return None

        what = self._what(subject, capability_name)
        if not what:
            return None

        text = self._phrase(what)
        self._last_offer_turn = self._turn
        self._last_by_capability[capability_id] = self._turn
        return Recommendation(
            capability=capability_id,
            text=text,
            goal=str(subject or capability_name or "").strip(),
        )

    # ----------------------------------------------------------- phrasing

    @staticmethod
    def _what(subject: str, capability_name: str) -> str:
        """What the offer is *about*, in words that fit mid-sentence.

        An offer naming only the ability ("I can use browser control") tells
        the user about Elaina; one naming the subject tells them what they
        would get.
        """
        subject = " ".join(str(subject or "").split())
        if subject:
            # A whole sentence as the subject reads badly inside an offer.
            if len(subject.split()) > 8:
                subject = " ".join(subject.split()[:8])
            subject = subject.rstrip("?.!")
            # The router's topic is written like a filing label -- "monitor
            # purchase consideration" -- which reads as a report title in
            # the middle of a spoken sentence. Drop the abstract nouns and
            # keep the thing itself.
            words = [
                word for word in subject.split()
                if word.casefold() not in _FILING_WORDS
            ]
            cleaned = " ".join(words) or subject
            # Stripping can leave a bare singular noun, which reads wrong
            # mid-sentence: "look into monitor". An article fixes it without
            # pretending to know more grammar than that.
            # "Happy to dig into used car if that helps" -- the article was
            # only added for a single word, so every compound noun lost it.
            words = cleaned.split()
            head = words[-1].casefold() if words else ""
            leads = words[0].casefold() if words else ""
            # Only a bare noun wants an article. "hotels in Seoul" is already
            # a natural phrase, and "a hotels in Seoul" is worse than none.
            bare_noun = (
                0 < len(words) <= 2
                and not any(
                    word.casefold() in _PREPOSITIONS for word in words
                )
            )
            if (
                bare_noun
                and not head.endswith("s")
                and leads not in _DETERMINER_LED
                and leads not in {"a", "an", "the", "my", "your", "our"}
            ):
                article = "an" if head[:1] in "aeiou" else "a"
                cleaned = f"{article} {cleaned}"
            return cleaned
        return " ".join(str(capability_name or "").split())

    def _phrase(self, what: str) -> str:
        options = _PHRASINGS[self.language]
        fresh = [text for text in options if text not in self._recent]
        template = self._rng.choice(fresh or list(options))
        self._recent.append(template)
        return template.format(what=what)

    @staticmethod
    def reads_as_offer(text: str) -> bool:
        """Whether a line is already an offer, so one is not appended twice."""
        return bool(_ALREADY_OFFERS.search(str(text or "")))


# She may have offered in her own words already. Appending a second offer to
# a sentence that ends in one is the exact behaviour this phase is trying to
# avoid.
_ALREADY_OFFERS = re.compile(
    r"want me to\b|shall i\b|should i\b|"
    r"would you like (?:me|help|assistance)\b|"
    r"i can (?:check|look|pull|open|find)\b|let me know if you\b|"
    r"할까\?|알아볼까|찾아볼까",
    re.IGNORECASE,
)


# Musing about acquiring or choosing something concrete. Deliberately narrow:
# a false positive here is exactly what makes an assistant pushy, and the
# categories TaskDiscoveryPolicy already recognises (hotel, restaurant, GPU,
# car, flight, second-hand, shopping) cover the rest without a second table.
_WORTH_OFFERING = re.compile(
    r"\b(?:thinking about|looking (?:for|at)|might (?:get|buy|upgrade)|"
    r"want(?:ing)? (?:a |an |some )?new|shopping for|in the market for)\b"
    r"|\bi wonder (?:if|whether|what|how)\b"
    r"|\b(?:looks?|sounds?|seems?) (?:pretty |really |quite )?"
    r"(?:good|nice|interesting|cool)\b",
    re.IGNORECASE,
)

# Talking about oneself is not a shopping list. These are the cases where an
# offer would be worse than silence.
_NOT_WORTH_OFFERING = re.compile(
    r"\b(?:i feel|i'm feeling|im feeling|i keep|i can't stop|"
    r"procrastinat|anxious|tired|stressed|lonely|sad)\b",
    re.IGNORECASE,
)


# Subjects that are about the person, not about a thing to look up. The
# router names a topic for every turn, and "procrastination" produced
# "I could check a procrastination for you -- worth it?" -- an offer to
# research someone's own habit.
_PERSONAL_SUBJECT = re.compile(
    r"\b(?:procrastinat|motivat|tired|sleep|anxiet|anxious|stress|"
    r"lonel|sad|mood|feeling|habit|focus|productivity|burnout|health)",
    re.IGNORECASE,
)


# Conversation topics, not things anyone could go and look up. "What should
# I eat for dinner?" is answerable and needs no browser; offering to research
# "Dinner" is nonsense, and it happened live.
_ABSTRACT_SUBJECTS = frozenset({
    "dinner", "lunch", "breakfast", "supper", "food", "meal", "meals",
    "cooking", "recipe", "recipes", "weather", "life", "work", "plans",
    "ideas", "advice", "hobbies", "exercise", "activities", "fun",
    "entertainment", "music", "movies", "films", "books", "news",
    "conversation", "chat", "everything", "anything", "personal activities",
})

# A qualifier makes an abstract topic concrete: "dinner" is a subject,
# "restaurants near Hongdae" is a search.
_QUALIFIED = re.compile(
    r"{B}b(?:near|around|in|at|by|from)\s+{B}w"
    r"|{B}b(?:best|top|cheap|cheapest)\s+{B}w+\s+{B}w",
    re.IGNORECASE,
)


def subject_is_offerable(subject: str) -> bool:
    """Whether this is a thing to look up rather than a topic of conversation.

    Two ways to fail: it is about the person ("procrastination"), or it is an
    abstract topic she can simply answer ("Dinner"). A qualifier rescues the
    second -- "restaurants near Hongdae" is a real search.
    """
    text = " ".join(str(subject or "").split())
    if not text:
        return False
    if _PERSONAL_SUBJECT.search(text):
        return False
    if _QUALIFIED.search(text):
        return True
    stripped = text.casefold().strip(" .,!?")
    for article in ("a ", "an ", "the "):
        if stripped.startswith(article):
            stripped = stripped[len(article):]
    return stripped not in _ABSTRACT_SUBJECTS


def worth_offering(request: str) -> bool:
    """Whether a capability would plausibly add something here.

    Read from the request, not from a model call. Combined with the
    category table already used by the discovery preflight, so the two
    cannot disagree about what counts as a lookup-shaped subject.
    """
    text = str(request or "")
    if not text.strip() or _NOT_WORTH_OFFERING.search(text):
        return False
    if _WORTH_OFFERING.search(text):
        return True
    try:
        from brain.task_discovery_policy import TaskDiscoveryPolicy

        return TaskDiscoveryPolicy.category_for(text) is not None
    except Exception:
        return False


# Nouns a classifier adds to name a topic, which nobody says out loud.
_FILING_WORDS = frozenset({
    "consideration", "considerations", "purchase", "purchasing",
    "selection", "options", "topic", "discussion", "inquiry", "request",
    "recommendation", "recommendations", "question",
})

# Words that already carry their own determiner, so one must not be added.
_DETERMINER_LED = frozenset({"it", "this", "that", "these", "those"})


# Naming the thing, when the router named nothing.
#
# ``goal.subject`` falls back to the whole utterance when the router sets no
# topic, and an offer built from that reads "Want me to look into i am
# thinking about getting a new monitor?". Rather than say that, she used to
# stay silent -- safe, but quieter than she should be.
#
# These are the same musing frames ``worth_offering`` already recognises,
# with the tail captured instead of discarded, so the two cannot disagree
# about what counts as musing. Deliberately not parsing: a frame that does
# not match yields nothing and the offer is skipped, which is the behaviour
# this replaces rather than a regression.
_SUBJECT_FRAMES = (
    re.compile(
        r"\bthinking about\s+(?:getting|buying|picking up|upgrading)?\s*(.+)",
        re.IGNORECASE),
    re.compile(r"\blooking (?:for|at)\s+(.+)", re.IGNORECASE),
    re.compile(
        r"\bmight (?:get|buy|upgrade|grab|try|need|want)\s+(.+)",
        re.IGNORECASE),
    re.compile(
        r"\b(?:been |have been |i've been )?looking at\s+(.+)", re.IGNORECASE),
    re.compile(r"\b(?:need|want)(?:ing)? (?:a|an|some) new\s+(.+)",
               re.IGNORECASE),
    re.compile(r"\b(?:in the market for|shopping for)\s+(.+)", re.IGNORECASE),
    re.compile(r"\bwant(?:ing)?\s+(?:a |an |some )?new\s+(.+)", re.IGNORECASE),
    re.compile(
        r"\bi wonder (?:if|whether)\s+(.+?)\s+"
        r"(?:supports?|works?|has|have|does|can|is|are)\b",
        re.IGNORECASE),
    re.compile(
        r"^\s*(?:that|this)\s+(.+?)\s+(?:look|sound|seem)", re.IGNORECASE),
)

# Tails that belong to the sentence, not to the thing.
_TRAILING_NOISE = re.compile(
    r"\s+(?:too|as well|though|actually|really|lately|recently|"
    r"for me|i think|i guess)\b.*$",
    re.IGNORECASE,
)


# Words that qualify a noun without naming it. Dropping them is what turns
# "a good mechanical keyboard" into something sayable.
_DETERMINERS = frozenset({
    "a", "an", "the", "my", "our", "your", "some", "another", "new",
    "this", "that", "these", "those", "any", "one",
})
_VAGUE_ADJECTIVES = frozenset({
    "good", "nice", "better", "best", "great", "decent", "proper", "cheap",
    "cheaper", "cheapest", "expensive", "affordable", "solid", "quality",
    "different", "other", "cool", "fancy",
})

# A noun phrase ends where a preposition begins: "restaurant near Hongdae"
# is about a restaurant, and keeping the tail would leave "near Hongdae".
_PREPOSITIONS = frozenset({
    "near", "in", "on", "at", "for", "with", "from", "around", "by",
    "under", "over", "about", "to", "of",
})

# Nothing anyone could act on.
_NOT_A_SUBJECT = frozenset({
    "it", "that", "this", "them", "one", "some", "thing", "things",
    "stuff", "something", "anything",
})


def _singular(word: str) -> str:
    """Enough plural handling for a spoken offer, and no more."""
    lowered = word.casefold()
    if len(lowered) > 3 and lowered.endswith("ies"):
        return word[:-3] + "y"
    if len(lowered) > 3 and lowered.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(lowered) > 3 and lowered.endswith("s") and not lowered.endswith("ss"):
        return word[:-1]
    return word


def subject_phrase(request: str, *, max_words: int = 2) -> str:
    """The thing a musing request is about, or "" when it cannot be told.

    Heuristic and deliberately shallow -- no model call, no parser. It aims
    for the head noun and at most one modifier that belongs to it, because
    that is what fits inside a spoken offer:

        "I'm thinking about getting a new monitor"  -> monitor
        "I might need a new keyboard"               -> keyboard
        "I've been looking at standing desks"       -> standing desk

    Weak extraction returns "" and the offer is skipped. An awkward offer is
    worse than none, and silence is what already happens when the router
    names no topic.
    """
    text = " ".join(str(request or "").split())
    if not text:
        return ""

    for frame in _SUBJECT_FRAMES:
        found = frame.search(text)
        if not found:
            continue
        phrase = _TRAILING_NOISE.sub("", found.group(1)).strip(" .,!?;:")
        if not phrase:
            continue

        words = []
        for word in phrase.split():
            cleaned = word.strip(" .,!?;:'\"")
            if not cleaned:
                continue
            if cleaned.casefold() in _PREPOSITIONS:
                break
            words.append(cleaned)

        kept = [
            word for word in words
            if word.casefold() not in _DETERMINERS
            and word.casefold() not in _VAGUE_ADJECTIVES
        ]
        if not kept:
            return ""

        # The head noun, plus one modifier when it forms a compound
        # ("standing desk", "mechanical keyboard").
        kept = kept[-max_words:]
        kept[-1] = _singular(kept[-1])
        result = " ".join(kept)

        if result.casefold() in _NOT_A_SUBJECT or len(result) < 3:
            return ""
        return result
    return ""


# ------------------------------------------------------- accepting an offer
#
# The consent classifier answers "is this positive about the offer?", and for
# a question it was asked that is the right question. A proactive offer was
# never asked, so the same answer means something else: "that sounds good"
# after a dinner recommendation is approval *of the dinner*, not an
# instruction to go and search. Measured live -- it became
# ``computer_action`` and ran browser control, which then failed.
#
# So a suggestion needs a clear yes on top of the classifier: either a bare
# affirmative, or an instruction naming something for her to do.

_AFFIRMATIVE = re.compile(
    r"^(?:yes|yeah|yep|yup|ok|okay|sure|alright|please|fine|"
    r"go ahead|go for it|do it|please do|why not|of course|"
    r"응|그래|좋아|해줘)"
    r"[\s,.!]*",
    re.IGNORECASE,
)

# Something for her to do, whether or not an affirmative came first.
_ASKS_HER_TO_ACT = re.compile(
    r"\b(?:look|search|check|find|pull up|bring up|open|show|dig|"
    r"google|browse)\b"
    r"|\b(?:do it|do that|go on|go for it|go ahead|please do)\b",
    re.IGNORECASE,
)

# "Go ahead, look it up" is consent. "Can you find the place of that
# name?" is a different question that happens to contain a verb from the
# same list -- and reading it as consent replaced it with the offer's
# stored query, so the yes/no search she had already run ran again and
# produced the same answer.
#
# The rule the docstring already states: consent adds nothing but assent.
# So once the affirmatives, the act verbs and the grammar are taken away,
# a turn that still has content of its own is a request.
_CONSENT_SCAFFOLD = re.compile(
    r"\b(?:yes|yeah|yep|yup|sure|ok|okay|alright|please|do|it|that|"
    r"go|ahead|on|for|now|then|and|the|a|an|you|can|could|would|will|"
    r"look|search|check|find|pull|bring|open|show|dig|google|browse|up|"
    r"me|us|to|thanks|thank|"
    # Words that stand in for the thing rather than naming one. "Search
    # for some" adds no subject: the subject is whatever was offered.
    r"some|any|one|ones|it|them|those|these|few|couple|more|"
    r"options|option|stuff|things|thing|anything|something)\b|[^\w\s]",
    re.IGNORECASE,
)


def _CARRIES_ITS_OWN_QUESTION(said: str) -> bool:
    """Whether anything is left once the assent and the asking are gone."""
    return bool(_CONSENT_SCAFFOLD.sub(" ", said).split())


# A turn that specifies its own errand: it names a capability to use, a
# destination to go to, or a target to act on. Those are instructions, and
# an instruction outranks an offer made several turns ago -- accepting it
# as consent swaps in the offer's stored goal and discards what was said.
_NAMES_ITS_OWN_ERRAND = re.compile(
    r"\b(?:use|using)\s+(?:my\s+|the\s+)?"
    r"(?:browser|computer|desktop|screen)\s*control\b"
    r"|\bgo\s+to\s+\S+\.\w{2,}"
    r"|\b(?:open|launch|start)\s+(?!(?:it|that|one|the\s+search)\b)[A-Za-z]"
    r"|\b(?:instead|rather)\b",
    re.IGNORECASE,
)

# "Let me know when you're ready to start." -- so this is the answer, and
# it asks for nothing new. Measured live it was routed as an unsupported
# machine action and answered with a capability list.
_READY = re.compile(
    r"^\s*(?:i'?m\s+|i\s+am\s+|we'?re\s+)?ready"
    r"(?:\s+(?:to\s+(?:start|go|begin)|now|when\s+you\s+are))?"
    r"\s*[.!]?\s*$"
    r"|^\s*(?:let'?s|lets)\s+(?:start|go|do\s+(?:it|this))\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Positive about a thing, not about her doing something. These read as
# agreement to a classifier and as ordinary conversation to a person.
_MERE_APPROVAL = re.compile(
    r"\b(?:sounds?|looks?|seems?)\s+(?:good|nice|great|fine|cool|"
    r"interesting|tasty|lovely)"
    r"|\bthat'?s\s+(?:cool|nice|good|great|interesting|fine)"
    r"|\bi (?:like|love) (?:that|it|this)"
    r"|\b(?:interesting|nice|cool|maybe|perhaps|possibly)$"
    r"|\bi might\b"
    r"|좋(?:네|다|겠)|괜찮(?:네|다)",
    re.IGNORECASE,
)


def reads_as_clear_acceptance(text: str) -> bool:
    """Whether this plainly says yes to the offer, rather than to the topic.

    Deliberately strict, because the cost is asymmetric: mistaking approval
    for consent runs a tool nobody asked for, while missing a real yes costs
    one more turn of the person saying it again.
    """
    said = " ".join(str(text or "").split())
    if not said:
        return False

    # Approval of the subject is never consent to act, even when it opens
    # with an affirmative ("yeah, that sounds good").
    if _MERE_APPROVAL.search(said):
        return False

    # A complete instruction is a request, not an acceptance of an older
    # offer for something else. Measured live:
    #
    #   User: So use my browser control, go to Zelo.com, search up
    #         apartments near University of Washington.
    #   [Consent Resume] capability: web_search, reused payload: yes
    #   [Router] web_search: The user accepted the offered ability.
    #
    # Reading it as consent meant the offer's stored goal *replaced* what
    # was just asked for, so an explicit browser-control request became a
    # web search for something else. Clearing the offer and routing the
    # turn on its own terms is what it deserves.
    if _NAMES_ITS_OWN_ERRAND.search(said):
        return False

    if _ASKS_HER_TO_ACT.search(said) and not _CARRIES_ITS_OWN_QUESTION(said):
        return True

    # Said in answer to "let me know when you're ready". It asks for
    # nothing new and means only yes.
    if _READY.search(said):
        return True

    # "ok, sure, please" is still just yes. Strip affirmatives until none
    # remain, so a stacked one does not read as substantive content.
    remainder = said
    for _ in range(3):
        stripped = _AFFIRMATIVE.sub("", remainder, count=1)
        if stripped == remainder:
            break
        remainder = stripped
    if remainder == said:
        # No affirmative at all, and nothing asked of her.
        return False
    # A bare "yes" accepts; "yeah they are getting expensive" carries on
    # talking about the thing and does not.
    return len(remainder.strip(" ,.!?")) <= 2
