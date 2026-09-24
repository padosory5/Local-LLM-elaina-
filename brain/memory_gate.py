"""Whether a turn carries something worth keeping, or needs what we kept.

There is a real memory subsystem behind this -- SQLite, FAISS, embeddings,
extraction, consolidation, ranking. It works. And in ``ChatEngine`` both
ends of it sit behind the same gate:

    storing     route.intent == "conversation" and route.memory_candidate
    retrieval   route.intent == "conversation" and route.memory_relevant

``conversation`` is **one of twenty-four intents**. Everything else --
``web_search``, ``calendar_action``, ``recommendation``, ``project_question``
and twenty more -- skips memory entirely, in both directions. So:

    "I'm allergic to shellfish, find me somewhere for dinner"
        -> web_search -> the allergy is never stored

    "what's a good restaurant near my school?"
        -> web_search -> nothing is recalled, and the gap is filled from
           the model, which is where an invented university comes from

That second one is not a hypothetical. It was the recall failure this
phase was written for, and the cause is structural: the turns most likely
to *contain* a durable fact about someone are the turns where they are
asking for something, and those are never routed as conversation.

The two booleans are also model-set, on a router call, which this project
has a standing rule about. A model that mislabels ``memory_candidate``
loses the fact silently, and a lost fact is indistinguishable from one
that was never said.

So memory becomes a property of **the sentence**, not of the route. This
module answers both questions deterministically, and the engine now asks
it alongside the router rather than instead of it -- either saying yes is
enough, because the cost of a wrong yes is a little latency and the cost
of a wrong no is her inventing your university.

What durable means
------------------

The hard part is not finding first-person statements, it is telling
"I'm allergic to shellfish" from "I'm tired". The social arc is full of
the second kind, and storing those as facts about someone is how a memory
becomes a caricature. ``_TRANSIENT`` is checked first and wins.
"""

from __future__ import annotations

import re

LANGUAGES = ("en", "ko")


# How someone is right now. Never stored -- it is true for an hour and the
# arc that produced it ("i had a rough night", "kind of tired honestly")
# is a conversation, not a profile.
_TRANSIENT = re.compile(
    r"\b(?:tired|exhausted|sleepy|hungry|thirsty|busy|late|sick|ill|cold|hot|"
    r"bored|stressed|annoyed|angry|upset|happy|sad|fine|ok|okay|good|great|"
    r"nervous|excited|worried|confused|lost|ready|done|back|home|out)\b"
    r"|피곤|배고프|바쁘|졸리|아프|괜찮",
    re.IGNORECASE,
)

# Predicates that describe a life rather than a mood. A statement using one
# of these about oneself is worth keeping.
# "I'm" and "I am" are the same sentence, and which one arrives is not up
# to the person: the transcriber picks. Writing only the contraction lost
# "I am allergic to shellfish" -- which is the registry's own example of
# what the memory capability is for.
_I_AM = r"\bi(?:'?m|\s+am)"

_DURABLE = re.compile(
    _I_AM + r"\s+(?:allergic|vegetarian|vegan|diabetic|colou?rblind|"
    r"left-handed)\b"
    r"|\bi\s+(?:live|work|study|teach|drive|own|use|prefer|hate|love|play|"
    r"speak|commute)\b"
    r"|\bi\s+(?:go|went)\s+to\s+(?:school|college|university|uni)\b"
    + r"|" + _I_AM + r"\s+a[n]?\s+\w+(?:\s+\w+)?\s*(?:student|engineer|"
    r"developer|designer|teacher|nurse|doctor|lawyer|analyst)\b"
    + r"|" + _I_AM + r"\s+(?:a|an)\s+(?:student|engineer|developer|designer|"
    r"teacher|nurse|doctor|lawyer|analyst|writer|artist)\b"
    r"|\bmy\s+(?:name|birthday|wife|husband|partner|kid|kids|son|daughter|"
    r"dog|cat|school|university|college|job|office|company|team|budget|"
    r"allergy|allergies|size|address|neighbou?rhood)\b"
    r"|\bi\s+(?:don'?t|do\s+not|can'?t|cannot)\s+(?:eat|drink|use|drive)\b"
    r"|\bi\s+have\s+(?:a|an|two|three)\s+\w+"
    # Korean: 저는/제/우리, and the predicates that make a statement durable.
    r"|알레르기|채식|비건"
    r"|(?:저|제|나|내)(?:는|가)?\s*\S*\s*(?:삽니다|살아요|다닙니다|다녀요|"
    r"일합니다|일해요|씁니다|써요|좋아합니다|좋아해요|싫어합니다|싫어해요)"
    r"|(?:제|내|우리|저희)\s*(?:이름|생일|아내|남편|아이|아들|딸|학교|대학|"
    r"회사|직장|팀|예산|주소|동네)"
    # Measured by the restart check: of ten facts told in one session, the
    # three it missed were all said the way people say them --
    # "내 여동생은 부산에 살아", "나 수업 끝나고 보통 젠레스 존 제로 해",
    # "다음 주 금요일에 시애틀로 돌아가" -- casual endings, family, a habit,
    # a plan. The gate knew only the polite endings and the office nouns.
    r"|\bmy\s+(?:sister|brother|mom|mother|dad|father|parents|grandma|"
    r"grandpa|friend|best\s+friend|girlfriend|boyfriend|roommate|major|"
    r"hometown|pet|hobby|hobbies)\b"
    r"|\bi\s+(?:usually|always|never|often)\s+\w+"
    + r"|" + _I_AM + r"\s+(?:from|originally\s+from|majoring|studying)\b"
    r"|(?:여동생|남동생|동생|언니|누나|오빠|형|엄마|아빠|어머니|아버지|부모님|"
    r"할머니|할아버지|친구|여자\s*친구|남자\s*친구|룸메이트|강아지|고양이|"
    r"전공|생일|고향|취미)\s*(?:은|는|이|가|을|를)?\s*\S"
    # A frequency word as a word: "늘" is also the end of "오늘", and
    # "오늘 좀 피곤합니다" is a mood.
    r"|(?:^|\s)(?:보통|매일|항상|주로|늘|자주|맨날)\s+\S.*(?:해|해요|합니다|마셔|먹어|봐|가)\s*[.!~]?$"
    r"|(?:살아|다녀|일해|전공해|전공하고\s*있어|좋아해|싫어해)\s*[.!~]?$",
    re.IGNORECASE,
)

# A plan with a day on it is worth keeping too -- "I'm going back to
# Seattle next Friday" is exactly what "when am I going back?" asks later.
# (Not a place they study: _ENROLLED still refuses a trip.)
_PLAN = re.compile(
    r"\bi(?:'?m|\s+am)\s+(?:going|flying|heading|moving|leaving|returning|"
    r"travel(?:l)?ing)\b.*\b(?:next|this|tomorrow|on\s+\w+day|in\s+\w+)\b"
    r"|(?:다음\s*주|다음\s*달|내일|모레|이번\s*주말?|\S+요일에?)\s*.*"
    r"(?:가|돌아가|간다|갈\s*거야|떠나|출발|이사|여행)",
    re.IGNORECASE,
)

# Asking about something is not stating it. Found by the contamination
# re-run: "where is my school again?" matched the durable pattern on "my
# school" and would have been stored as a fact about the person -- so a
# memory fills up with the questions someone asked rather than the answers
# they gave, and every one of those is later retrievable as though they had
# told her something.
_IS_A_QUESTION = re.compile(
    r"\?\s*$"
    r"|^\s*(?:who|what|where|when|why|how|which|do|does|did|is|are|was|were|"
    r"can|could|will|would|should|have|has|tell\s+me)\b"
    r"|(?:나요|까요|나\?|니\?|어디|뭐야|뭔가요|무엇)\s*[?？]?\s*$",
    re.IGNORECASE,
)

# Where someone studies or works, said the way people say it. The gate
# first missed "I'm going to UW in Seattle" -- the setup line of the
# contamination matrix's own correction case -- because it only knew
# "I go to school". A near-future time expression makes it a trip
# instead: "I'm going to Seattle next week" is not where you study.
_SOON = re.compile(
    r"\b(?:next|this)\s+(?:week|month|year|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday)\b"
    r"|\b(?:tomorrow|tonight|later|soon|in\s+an?\s+\w+)\b",
    re.IGNORECASE,
)

# Not IGNORECASE: the capital is the signal that distinguishes an
# institution from a shop. "I" is spelled out both ways instead, because
# writing \bi without the flag matches only the lowercase one -- which is
# why the first version of this pattern never fired at all.
_ENROLLED = re.compile(
    r"\b[Ii](?:'?m|\s+am)\s+(?:going\s+to|at|attending|studying\s+at)\s+"
    r"(?!the\b|a\b|an\b|my\b)[A-Z][\w.&-]*"
    r"|\b[Ii]\s+attend\b",
)


# An explicit instruction. Always kept, whatever else the sentence does.
_ASKED_TO_REMEMBER = re.compile(
    r"\b(?:remember|keep\s+in\s+mind|note)\s+(?:that\s+)?(?:i|my|we|our)\b"
    r"|\bdon'?t\s+forget\s+(?:that\s+)?(?:i|my)\b"
    r"|기억해\s*(?:줘|주세요|주십시오)?|잊지\s*마",
    re.IGNORECASE,
)

# An explicit instruction to stop keeping something. Separated because
# forgetting has to be a real, visible operation -- A1 found that a bare
# "forget X" could silently eat a turn instead.
# ``i\b`` rather than ``i\s``, so "forget that i'm vegetarian" is heard.
# The same contraction bug as _DURABLE had, in the pattern next to it, and
# it survived the phase because the recall matrix wrote its forget cases
# out in full. Found by a dogfood turn: "forget that i'm vegetarian" was
# answered "확인해 주시겠습니까?" -- she asked for confirmation of nothing
# and kept the fact. ``\b`` still refuses "forget it", where the "i" has a
# letter after it.
_ASKED_TO_FORGET = re.compile(
    r"\b(?:forget|delete|remove)\s+(?:that\s+|what\s+)?"
    r"(?:i\b|my\s|you\s+know|about\s+me|everything)"
    r"|\bstop\s+remembering\b"
    r"|잊어\s*(?:줘|주세요|버려)|기억(?:하지|에서)\s*(?:마|지워)",
    re.IGNORECASE,
)

# The person's own world, referred to but not described -- so answering
# depends on already knowing it. "my school" is the shape that produced an
# invented university.
_ABOUT_THEIR_WORLD = re.compile(
    r"\bmy\s+(?:school|university|college|uni|office|work|job|company|team|"
    r"place|home|house|apartment|flat|neighbou?rhood|area|city|town|"
    r"wife|husband|partner|kid|kids|son|daughter|family|dog|cat|"
    r"budget|size|usual|favou?rite|allergy|allergies|diet)\b"
    r"|\b(?:near|around|close\s+to)\s+(?:me|my\s+\w+|mine)\b"
    r"|\bbased\s+on\s+what\s+you\s+know\s+about\s+me\b"
    r"|\bwhat\s+(?:do\s+you\s+(?:know|remember)\s+about\s+me|"
    r"did\s+i\s+(?:say|tell\s+you|pick|choose))\b"
    r"|\blast\s+time\b|\bthe\s+one\s+i\s+(?:picked|chose|liked|bought)\b"
    r"|\bfor\s+me\b.{0,20}\b(?:usual|preference|taste)\b"
    r"|(?:제|내|우리|저희)\s*(?:학교|대학|회사|직장|집|동네|가족|아내|남편|"
    r"아이|예산|취향|알레르기)"
    r"|저번에|지난번에|제가\s*(?:고른|선택한|말한)"
    r"|저에\s*대해\s*(?:아는|기억)",
    re.IGNORECASE,
)

# Possessives about the machine are not possessives about the person.
# "my screen" is a screen-vision request and "my browser" is a capability
# question; neither needs to know where they went to school.
_THEIR_MACHINE = re.compile(
    r"\bmy\s+(?:screen|display|browser|computer|pc|desktop|laptop|machine|"
    r"keyboard|monitor|tab|tabs|window|windows|file|files|folder|project)\b"
    r"|(?:내|제)\s*(?:화면|브라우저|컴퓨터|노트북|모니터|창|파일|폴더)",
    re.IGNORECASE,
)


# Clause boundaries. A durable fact usually arrives attached to a request,
# so the two halves have to be judged separately.
_CLAUSES = re.compile(r"[.!?;]+\s*|,\s*(?=(?:and\s+)?(?:who|what|where|when|"
                      r"why|how|which|can|could|do|does|is|are)\b)")


def _said(text) -> str:
    return " ".join(str(text or "").split())


def asks_to_remember(text) -> bool:
    """An explicit "remember that I ..."."""
    return bool(_ASKED_TO_REMEMBER.search(_said(text)))


def asks_to_forget(text) -> bool:
    """An explicit "forget what I told you about ..."."""
    return bool(_ASKED_TO_FORGET.search(_said(text)))


def carries_something_to_remember(text) -> bool:
    """Whether this turn states a durable fact about the person.

    Transient state loses to nothing: "I'm tired" and "I had a rough
    night" are a conversation, and storing them as facts about someone is
    how a memory turns into a caricature. An explicit instruction still
    wins over that, because being asked is different from being guessed.
    """
    said = _said(text)
    if not said:
        return False
    if asks_to_remember(said):
        return True

    # Clause by clause, because the two halves of one utterance can do
    # different things. "I go to school at UW, where is the international
    # students office?" states a durable fact and then asks a question,
    # and judging the whole thing by its final question mark threw the
    # fact away -- which is exactly the shape this phase exists for, since
    # a durable fact usually arrives attached to a request.
    for clause in _CLAUSES.split(said):
        clause = clause.strip()
        if not clause or _IS_A_QUESTION.search(clause):
            # Asking about something is not stating it. Without this,
            # "where is my school again?" is stored as a fact, and a
            # memory fills up with the questions someone asked rather
            # than the answers they gave -- each later retrievable as
            # though they had told her something.
            continue
        if _ENROLLED.search(clause) and not _SOON.search(clause):
            return True
        if _PLAN.search(clause):
            return True
        if _TRANSIENT.search(clause) and not _DURABLE.search(clause):
            continue
        if _DURABLE.search(clause):
            return True
    return False


# A question about themselves: the answer is something they told her, or
# something she does not know. Measured after a restart: "What's my name?",
# "When's my birthday?", "우리 강아지 이름 뭐였지?" -- the recall gate said no
# to all of them, so nothing was recalled, and she answered "Your birthday
# is September 11, 2026" (today's date) and "제일 좋아하는 색깔은
# 파란색이었습니다" (never told).
_ABOUT_THEMSELVES = re.compile(
    # "what's my name?", "when is my birthday?" -- something of theirs.
    # Not "which one should I go to?" or "what should I eat?": those ask
    # for advice, and taken for a question about themselves one was
    # answered from memory with no search -- "The Silver Star Casino is the
    # only casino on Bainbridge Island", which does not exist.
    r"\b(?:what|when|where|who|which|how)\b[^.!?]*\bmy\b[^.!]*\?"
    r"|\b(?:where|which\s+\w+)\s+do\s+i\s+(?:live|work|study|go(?:\s+to)?)\b"
    r"|\b(?:do|did|am|was|have)\s+i\s+(?:have|follow|like|eat|drink|play|"
    r"live|work|study|own|hate|love|prefer|usually|allergic|vegetarian|vegan)\b"
    r"|\bdo\s+you\s+(?:know|remember)\s+(?:my|what\s+i|where\s+i|when\s+i|who\s+i|me)\b"
    r"|\bremember\s+(?:my|what\s+i|where\s+i|when\s+i)\b"
    # "우리 강아지 이름 뭐였지?" -- asked back, with a recall ending. Not
    # "우리 뭐 먹을까?", which is a suggestion.
    r"|(?:내|제|우리|저희)\s*(?:\S+\s*){1,3}?(?:은|는|이|가)?\s*(?:뭐|무엇|언제|어디|누구|몇)"
    r".*(?:였지|이었지|였어|였더라|더라|했지|했더라|이야|야|예요|에요|입니까|인가요|이지)"
    r"\s*[?？]?\s*$"
    r"|(?:내가|제가)\s+.*(?:했지|였지|더라|했더라|했나|한다고\s*했|라고\s*했|이었지)"
    r"|기억\s*(?:해|나|하니|하세요|하십니까)\s*[?？]?\s*$",
    re.IGNORECASE,
)


def asks_about_themselves(text) -> bool:
    """A question whose answer is a fact about the person."""
    said = _said(text)
    if not said:
        return False
    stripped = _THEIR_MACHINE.sub(" ", said)
    return bool(_ABOUT_THEMSELVES.search(stripped))


# The same thing, said the ways people say it -- in either language, and
# the way the extractor rewrites it ("what school" is answered by "the
# University of Washington").
_TOPICS = (
    ("school", "university", "college", "uni", "학교", "대학", "대학교"),
    ("allerg", "알레르기"),
    ("diet", "vegetarian", "vegan", "채식", "식단", "비건"),
    ("birthday", "born", "생일"),
    ("name", "called", "이름"),
    ("major", "study", "studies", "studying", "majoring", "전공"),
    ("sister", "여동생", "언니", "누나"),
    ("brother", "남동생", "오빠", "형"),
    ("dog", "puppy", "강아지"),
    ("cat", "kitten", "고양이"),
    ("game", "play", "게임"),
    ("coffee", "커피"),
    ("live", "lives", "living", "살", "사는", "산다"),
    ("work", "job", "직장", "회사"),
    ("hometown", "from", "고향"),
    ("seattle", "시애틀"),
    ("back", "returning", "trip", "돌아가", "돌아"),
)
_QUESTION_WORDS = {
    "what", "whats", "when", "whens", "where", "who", "whos", "which", "how",
    "my", "mine", "do", "did", "does", "have", "has", "the", "is", "are",
    "was", "were", "you", "remember", "know", "tell", "me", "i", "am", "any",
    "particular", "again", "still", "can", "should", "could", "would",
    "about", "going", "get", "go", "to", "a", "an", "follow", "of",
}


_GENERIC_TOPICS = {"name", "names", "이름", "이름은", "이름이"}


def _topic_words(text: str) -> set[str]:
    words = set()
    for word in re.findall(r"[a-z]+|[가-힣]+", str(text or "").casefold()):
        if word in _QUESTION_WORDS or len(word) < 2:
            continue
        words.add(word)
    # "What's my brother's name?" is about the brother. Matched on "name"
    # it would find their own name and answer with it.
    if words - _GENERIC_TOPICS:
        words -= _GENERIC_TOPICS
    return words


# A detail of their own life -- the kind of question whose only honest
# answers are what they said, or that they have not said it. Narrow on
# purpose: "do I need an umbrella?" and "what's my schedule?" are questions
# about them that a tool answers, and are left to the router.
_PERSONAL_DETAIL = re.compile(
    r"\b(?:name|birthday|age|brother|sister|mom|mother|dad|father|parents|family|"
    r"wife|husband|partner|girlfriend|boyfriend|friend|favou?rite|colou?r|pet|dog|"
    r"cat|school|university|college|major|hometown|allerg\w*|diet|hobby|hobbies)\b"
    r"|이름|생일|나이|형|오빠|누나|언니|동생|엄마|아빠|부모님|가족|아내|남편|친구|"
    r"여자\s*친구|남자\s*친구|좋아하는|색깔|강아지|고양이|학교|전공|고향|알레르기|취미",
    re.IGNORECASE,
)


def asks_for_a_personal_detail(text) -> bool:
    """A question about themselves whose answer is a detail of their life."""
    said = _said(text)
    return asks_about_themselves(said) and bool(_PERSONAL_DETAIL.search(said))


def facts_on_topic(question, facts) -> list[str]:
    """The things they told her that are about what this asks."""
    asked = _topic_words(question)
    if not asked:
        return []
    terms = set()
    for word in asked:
        group = next((g for g in _TOPICS if any(word.startswith(t) or t.startswith(word)
                                                  for t in g if len(t) >= 2)), (word,))
        terms.update(term for term in group if len(term) >= 2)
    def mentions(fact: str, term: str) -> bool:
        # A whole English word, or a Korean word's start (Korean attaches
        # its particles). Substrings matched "one" inside "someone".
        if re.fullmatch(r"[a-z]+", term):
            return bool(re.search(rf"\b{re.escape(term)}", fact))
        return term in fact

    return [
        str(fact) for fact in facts
        if any(mentions(str(fact or "").casefold(), term) for term in terms)
    ]


def shares_a_topic(question, facts) -> bool:
    """Whether something they told her is about what this asks."""
    return bool(facts_on_topic(question, facts))


def is_question(text) -> bool:
    """Every clause asks; nothing is stated. Such a turn is never stored --
    measured: "내가 무슨 전공인지 기억해?" left behind "The user studies
    Electrical Engineering", which nobody said."""
    said = _said(text)
    if not said:
        return False
    clauses = [c.strip() for c in _CLAUSES.split(said) if c.strip()]
    if said.rstrip().endswith(("?", "？")) and len(clauses) <= 1:
        return True
    return bool(clauses) and all(_IS_A_QUESTION.search(c) for c in clauses)


def needs_what_we_know(text) -> bool:
    """Whether answering this depends on something they told us before.

    Deliberately about the *sentence*, not the route. The turns most
    likely to need a personal fact are requests -- "somewhere near my
    school", "the one I picked last time" -- and requests are never
    routed as conversation, which is why nothing was ever recalled for
    them.
    """
    said = _said(text)
    if not said:
        return False
    if asks_about_themselves(said):
        return True
    if not _ABOUT_THEIR_WORLD.search(said):
        return False
    # "my screen" and "my browser" are about the machine in front of them.
    stripped = _THEIR_MACHINE.sub(" ", said)
    return bool(_ABOUT_THEIR_WORLD.search(stripped))
