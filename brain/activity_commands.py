"""What the person asks about what they did on the computer.

Deterministic and bilingual, like the standing orders (brain/
standing_orders.py) -- and read *before* them, because their "forget
<something>" would otherwise take "forget what I did today".

Everything they do is recorded all the time (tools/screen_control/
activity_recorder.py). There is no "record this" and no naming step: live,
that ceremony was the awkward part -- "record this", "stop recording",
"what should I call it?" -- and a transcriber that mishears short commands
made it worse. Instead:

* repeat the last N things they did -- "repeat my last 5 actions",
  "방금 한 거 다시 해줘", "마지막 3개 다시 해줘";
* what they did right after the PC started, and doing it again -- "what did
  I do after I turned on my PC?", "컴퓨터 켜자마자 뭐 했지?";
* make that the start-up routine -- "do those every time I turn on my PC",
  "켤 때마다 이렇게 해줘" -- which she *offers* when she starts, never runs
  unasked; and stop it again;
* what they did ("what did I do this morning?"), forgetting it, pausing
  and resuming.

Nothing here acts. A replay is always listed first and done only on a yes
(``read_consent``). Deliberately strict: every form needs words only this
feature uses; tests/test_activity_commands.py runs every dogfood arc turn
through it and none may match.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime

from memory.activity_log import CLICKS


@dataclass(frozen=True)
class ActivityCommand:
    kind: str
    count: int = 0             # replay_last: how many (0 = what they just did)
    since: float | None = None
    until: float | None = None
    when: str = ""
    kinds: tuple[str, ...] = ()
    about: str = ""
    everything: bool = False


_LEAD = (
    r"(?:(?:ok(?:ay)?|alright|sure|yes|yeah|hey|elaina|please|so|now|"
    r"그래|응|네|좋아|알았어|엘레나|엘리나|자|이제)[,.!\s]+)*"
)
_END = r"\s*[.!?~]*\s*$"
_VERB_KO = (
    r"(?:해|해줘|해\s*줘|해봐|해\s*봐|해\s*주세요|해주세요|해라|하자|해\s*줄래|해줄래|"
    r"실행(?:해)?(?:\s*줘)?|반복(?:해)?(?:\s*줘)?)"
)


def _en(pattern: str) -> re.Pattern:
    return re.compile(rf"^{_LEAD}{pattern}{_END}", re.IGNORECASE)


def _ko(pattern: str) -> re.Pattern:
    return re.compile(rf"^{_LEAD}{pattern}{_END}")


_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "few": 3, "couple": 2,
    "couple of": 2,
    "한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
}
_N_EN = r"(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten|few|couple(?:\s+of)?)"
_N_KO = r"(?P<n>\d+|한|하나|두|둘|세|셋|네|넷|다섯|여섯|일곱|여덟|아홉|열)"


def _count(match) -> int:
    if match is None:
        return 0
    raw = next((match.groupdict().get(key) for key in ("n", "n2")
                if match.groupdict().get(key)), "")
    raw = " ".join(str(raw or "").casefold().split())
    if raw.isdigit():
        return max(0, min(int(raw), 50))
    return _NUMBERS.get(raw, 0)


# ------------------------------------------------------------- repeat

_REPLAY_LAST = (
    _en(r"(?:can\s+you\s+|could\s+you\s+|would\s+you\s+|please\s+)?"
        r"(?:repeat|redo|replay|do|perform)\s+(?:again\s+)?(?:the\s+|my\s+)?"
        rf"(?:last|previous|past|most\s+recent)\s+(?:{_N_EN}\s+)?"
        r"(?:actions?|steps?|things?|clicks?|moves?)"
        r"(?:\s+(?:that\s+)?i\s+(?:just\s+)?(?:did|made|took))?(?:\s+again)?"
        r"(?:\s+for\s+me)?(?:\s+please)?"),
    _en(r"(?:can\s+you\s+|could\s+you\s+|please\s+)?(?:repeat|redo|replay|do)\s+"
        r"what\s+i\s+(?:just\s+)?did(?:\s+(?:again|just\s+now))?(?:\s+for\s+me)?"),
    _en(r"(?:can\s+you\s+|could\s+you\s+)?do\s+(?:that|it)\s+again\s+"
        r"(?:like|the\s+way)\s+i\s+did(?:\s+it)?"),
    _ko(r"(?:방금|아까|마지막(?:으로)?|이전에|최근에?)?\s*(?:내가\s*)?(?:한|했던)\s*"
        rf"(?:{_N_KO}\s*(?:개|가지|번)(?:의)?\s*)?(?:동작|행동|거|것|일|작업|클릭)\s*"
        r"(?:(?P<n2>\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?:개|가지))?\s*"
        rf"(?:을|를)?\s*(?:다시|그대로|똑같이)\s*{_VERB_KO}(?:\s*요)?"),
    _ko(rf"(?:방금|최근|마지막)\s*{_N_KO}\s*(?:개|가지)?\s*(?:동작|행동|작업|클릭)?\s*"
        rf"(?:을|를)?\s*(?:다시|그대로|똑같이)\s*{_VERB_KO}(?:\s*요)?"),
)

# ---------------------------------------------------------------- boot

_BOOT_EN = (
    r"(?:right\s+|just\s+)?(?:after|when|as\s+soon\s+as|once)\s+i\s+(?:first\s+)?"
    r"(?:turned\s+on|booted(?:\s+up)?|started(?:\s+up)?|switched\s+on|"
    r"logged\s+(?:in|on)(?:\s+to)?)\s*(?:my|the)?\s*(?:pc|computer|laptop|machine)?"
)
_BOOT_KO = (
    r"(?:(?:컴퓨터|pc|피시|노트북)\s*(?:를|을)?\s*"
    r"(?:켜자마자|켜고\s*(?:나서|바로)?|켠\s*(?:뒤|후|다음|직후)(?:에)?)|"
    r"부팅(?:하고\s*(?:나서)?|하자마자|\s*(?:후|직후)(?:에)?))"
)
_BOOT_RECALL = (
    re.compile(rf"\bwhat\s+(?:did|have)\s+i\s+(?:do|done|open(?:ed)?)\s+{_BOOT_EN}", re.IGNORECASE),
    re.compile(rf"\bremember\s+what\s+i\s+did\s+{_BOOT_EN}", re.IGNORECASE),
    re.compile(rf"{_BOOT_KO}.*(?:뭐|뭘|무엇|무슨|어떤|기억)"),
)
_BOOT_REPLAY = (
    re.compile(rf"\b(?:do|repeat|redo|open)\s+(?:what\s+i\s+did|the\s+(?:same\s+)?"
               rf"(?:things|apps|stuff)(?:\s+i\s+(?:did|opened))?|those(?:\s+\w+)?)\s+{_BOOT_EN}",
               re.IGNORECASE),
    # "... what I did when I turned on my PC? I want you to do those actions"
    re.compile(r"\b(?:want|need|like)\s+you\s+to\s+(?:do|open|repeat)\s+(?:those|them|that|the\s+same)",
               re.IGNORECASE),
    re.compile(rf"{_BOOT_KO}.*(?:다시|그대로|똑같이)?\s*(?:해\s*줘|해줘|해\s*주세요|열어\s*줘|실행)"),
)

# ------------------------------------------------------------- startup

_STARTUP_WHEN_EN = (
    r"(?:every\s*time|whenever|each\s+time)\s+i\s+(?:turn\s+on|start(?:\s+up)?|"
    r"boot(?:\s+up)?|log\s+(?:in|on)(?:\s+to)?|switch\s+on)\s*(?:my|the)?\s*"
    r"(?:pc|computer|laptop|machine)?"
)
_SAVE_STARTUP = (
    re.compile(rf"\b(?:do|open|run|start)\s+(?:that|those|these|this|them|it|the\s+same)"
               rf"(?:\s+\w+)?\s+{_STARTUP_WHEN_EN}", re.IGNORECASE),
    re.compile(rf"\b{_STARTUP_WHEN_EN},?\s+(?:please\s+)?(?:do|open|run|start)\s+"
               r"(?:that|those|these|this|them|it|the\s+same)", re.IGNORECASE),
    re.compile(r"\bmake\s+(?:that|those|these|this|it)\s+(?:my|the)\s+(?:start-?\s?up|boot)\s+routine",
               re.IGNORECASE),
    re.compile(r"(?:켤\s*때마다|켜면\s*(?:항상|매번|늘)|부팅할\s*때마다|부팅하면\s*(?:항상|매번|늘)|"
               r"시작할\s*때마다|매번\s*(?:켤|부팅할|시작할)\s*때)\s*(?:이거|이렇게|그거|그렇게|저거)?\s*"
               r"(?:다\s*)?(?:해|해줘|해\s*줘|해\s*주세요|열어\s*줘|열어줘|실행해\s*줘|켜\s*줘)"),
)
_FORGET_STARTUP = (
    re.compile(r"\b(?:stop|don'?t|quit)\s+(?:doing|offering|opening)\s+(?:that|those|it|them)\s+"
               r"(?:at|on|when\s+(?:i|you))\s+(?:start(?:\s*up)?|boot|launch|start-up)",
               re.IGNORECASE),
    re.compile(r"\b(?:forget|delete|remove|cancel)\s+(?:my|the)\s+(?:start-?\s?up|boot)\s+"
               r"(?:routine|actions?)", re.IGNORECASE),
    re.compile(r"(?:시작|부팅|켤)\s*(?:할\s*)?때\s*(?:그거|이거|그렇게|그\s*루틴)?\s*"
               r"(?:하지\s*마|안\s*해도\s*돼|그만)"),
    re.compile(r"(?:시작|부팅)\s*루틴\s*(?:지워|삭제|취소)"),
)

# ------------------------------------------------------ pause, resume, forget

_PAUSE = (
    _en(r"(?:stop|pause|quit)\s+(?:watching|tracking|logging|recording)\s+"
        r"(?:me|what\s+i\s+do|my\s+(?:activity|clicks|screen))(?:\s+for\s+now)?"),
    _en(r"(?:pause|stop|turn\s+off|disable)\s+(?:the\s+)?activity\s+"
        r"(?:log|logging|tracking|recording)"),
    _en(r"don'?t\s+(?:watch|track|log|record)\s+(?:me|what\s+i\s+do|my\s+activity)"
        r"(?:\s+(?:for\s+now|anymore))?"),
    _ko(r"(?:이제\s*)?(?:내가\s*하는\s*(?:거|것)|내\s*활동|활동)\s*(?:을|를)?\s*"
        r"(?:기록|저장|추적)?\s*(?:하지\s*마|그만(?:\s*" + _VERB_KO + r")?|멈춰|꺼|꺼\s*줘|"
        r"중지(?:\s*" + _VERB_KO + r")?)(?:\s*(?:줘|요))?"),
    _ko(r"활동\s*기록\s*(?:꺼|끄기|꺼\s*줘|멈춰|중지|그만)(?:\s*(?:줘|요|해|해\s*줘))?"),
)
_RESUME = (
    _en(r"(?:resume|restart|start|turn\s+on|enable)\s+(?:the\s+)?activity\s+"
        r"(?:log|logging|tracking)(?:\s+again)?"),
    _en(r"(?:you\s+can\s+)?(?:start\s+)?(?:watch|track|log)\s+(?:me|what\s+i\s+do|"
        r"my\s+activity)\s+again"),
    _en(r"resume\s+(?:watching|tracking|logging)(?:\s+me)?"),
    _ko(r"(?:다시\s*)?(?:내\s*)?활동\s*기록\s*(?:다시\s*)?(?:시작|켜|해)"
        r"(?:\s*(?:해|줘|해\s*줘|요))?"),
    _ko(r"(?:내가\s*하는\s*(?:거|것)\s*)?다시\s*기록\s*(?:해|해줘|해\s*줘|해도\s*돼|시작(?:해)?)"
        r"(?:\s*요)?"),
)
_FORGET_ACTIVITY = (
    _en(r"(?:forget|delete|clear|erase|wipe)\s+(?:what\s+i\s+(?:did|was\s+doing)|"
        r"my\s+activity(?:\s+(?:log|history))?|the\s+activity\s+log)(?P<rest>(?:\s+.*)?)"),
    _ko(r"(?P<rest>(?:(?:오늘|아까|어제|방금|지난\s*\S+\s*(?:동안)?)\s*)?)(?:내가\s*)?"
        r"(?:(?:한|했던)\s*(?:거|것|일)|(?:활동|사용)\s*(?:기록|내역))\s*(?:을|를)?\s*"
        r"(?:다\s*|전부\s*|모두\s*)?(?:지워|삭제|잊어)"
        r"(?:\s*(?:줘|버려|줘요|주세요|해\s*줘|해줘|해))?"),
)

# -------------------------------------------------------------- recall

_RECALL_EN_VERB = re.compile(
    r"\bwhat\s+(?:did|have)\s+i\s+(?:just\s+)?(?P<verb>do|done|been\s+doing|"
    r"click(?:ed)?|open(?:ed)?|visit(?:ed)?|look(?:ed)?\s+at|watch(?:ed)?|"
    r"search(?:ed)?(?:\s+for)?|type[d]?|change[d]?)\b(?P<rest>.*)$",
    re.IGNORECASE,
)
_COMPUTER_VERBS = ("click", "open", "visit", "search", "type")
_RECALL_EN = (
    re.compile(r"\b(?:which|what)\s+(?:websites?|sites?|pages?|web\s*pages?|apps?|"
               r"programs?|applications?)\s+(?:did|have|was|were)\s+i\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:was|were)\s+(?:that|the|those)\s+(?:websites?|sites?|pages?|"
               r"web\s*pages?|apps?|programs?)\b", re.IGNORECASE),
    re.compile(r"\b(?:show|tell)\s+me\s+(?:my\s+(?:activity|computer\s+history)|"
               r"what\s+i\s+(?:did|was\s+doing)\s+(?:on|at)\s+(?:my|the)\s+"
               r"(?:computer|pc|laptop))\b", re.IGNORECASE),
)
_NOT_RECALL = re.compile(
    r"\b(?:wrong|to\s+(?:you|deserve|make|upset)|last\s+time\s+we|to\s+her|to\s+him)\b",
    re.IGNORECASE,
)
_PLACE_EN = re.compile(
    r"\b(?:on\s+(?:my|the)\s+(?:computer|pc|laptop|screen)|in\s+(?:chrome|settings|"
    r"the\s+browser|explorer|spotify|edge|whale))\b",
    re.IGNORECASE,
)
_TIME_KO = (
    r"(?:오늘\s*(?:아침|오전|오후|저녁)?(?:에)?|아까|방금|좀\s*전에|어제|오전에|오후에|"
    r"아침에|저녁에|밤에|지난\s*\S+\s*(?:동안)?|\S+\s*(?:분|시간)\s*(?:동안|전에?))"
)
_RECALL_KO = (
    re.compile(rf"(?:내가\s*)?(?:{_TIME_KO})\s*(?:내가\s*)?(?:컴퓨터로|컴퓨터에서|pc로|피시로)?\s*"
               r"(?:뭐|뭘|무엇을|어떤\s*거|무슨\s*(?:거|일))\s*(?:했|클릭했|열었|봤|검색했|"
               r"입력했|눌렀|들어갔)"),
    re.compile(r"(?:들어간|들어갔던|방문한|방문했던|본|봤던|열었던|열어\s*본|열어본|연)\s*"
               r"(?:사이트|웹사이트|페이지|웹페이지|링크)"),
    re.compile(r"(?:사용한|사용했던|썼던|쓴|켰던|열었던|실행한|실행했던)\s*(?:앱|프로그램|어플)"),
    re.compile(r"(?:내|오늘|아까|최근)\s*(?:활동|사용)\s*(?:기록|내역)"),
    re.compile(r"(?:클릭한|클릭했던|눌렀던|누른)\s*(?:거|것|버튼|설정)"),
)
_ASKS_KO = re.compile(r"(?:뭐|뭘|무슨|무엇|어디|알려|보여|기억\s*나|목록|였지|였더라|였어|내역)")


def _kinds_for(said: str) -> tuple[str, ...]:
    text = said.casefold()
    if re.search(r"website|\bsites?\b|\bpages?\b|web\s*page|\blinks?\b|사이트|페이지|링크", text):
        return ("page",)
    if re.search(r"\bapps?\b|program|application|앱|프로그램|어플", text):
        return ("switch", "fullscreen", "private")
    if re.search(r"click|button|setting|클릭|버튼|설정|눌렀|누른", text):
        return CLICKS
    if re.search(r"search|\btyped?\b|검색|입력", text):
        return ("type", "page")
    return ()


def _about(said: str) -> str:
    match = re.search(
        r"\babout\s+(?:the\s+|a\s+)?(?P<about>[^?.!,]+?)"
        r"(?:\s+(?:earlier|today|yesterday|this\s+\w+|just\s+now|again))?\s*[?.!]*$",
        said, re.IGNORECASE,
    )
    if match:
        return match.group("about").strip()
    match = re.search(r"(?P<about>[^\s]+?)\s*(?:관련(?:된)?|에\s*대한|에\s*관한)", said)
    return match.group("about").strip() if match else ""


# ---------------------------------------------------------------- time

_KO_NUMBERS = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "열": 10}


def _number(text: str) -> int:
    text = str(text or "").strip()
    if text.isdigit():
        return int(text)
    return _KO_NUMBERS.get(text, 0)


def time_window(said: str, now: float) -> tuple[float | None, float | None, str] | None:
    """The span of time a sentence asks about, and its label."""
    text = said.casefold()
    day = datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).timestamp()
    hour = 3600.0
    match = re.search(
        r"(?:last|past)\s+(\d+)\s+minutes?|(\d+|한|두|세|네|다섯|열)\s*분\s*(?:동안|전)|"
        r"지난\s*(\d+|한|두|세|네|다섯|열)\s*분", text,
    )
    if match:
        n = _number(next(g for g in match.groups() if g))
        if n:
            return now - n * 60.0, None, f"minutes:{n}"
    match = re.search(
        r"(?:last|past)\s+(?:(\d+)\s+hours?|(hour))|(\d+|한|두|세|네|다섯)\s*시간\s*(?:동안|전)|"
        r"지난\s*(\d+|한|두|세|네|다섯)\s*시간", text,
    )
    if match:
        groups = match.groups()
        n = 1 if groups[1] else _number(next((g for g in groups if g), ""))
        if n:
            return now - n * hour, None, f"hours:{n}"
    if re.search(r"\byesterday\b|어제", text):
        return day - 24 * hour, day, "yesterday"
    if re.search(r"this\s+morning|오늘\s*(?:아침|오전)|아침에|오전에", text):
        return day + 5 * hour, day + 12 * hour, "this_morning"
    if re.search(r"this\s+afternoon|오후", text):
        return day + 12 * hour, day + 18 * hour, "this_afternoon"
    if re.search(r"this\s+evening|tonight|저녁|밤에", text):
        return day + 18 * hour, day + 24 * hour, "this_evening"
    if re.search(r"just\s+now|a\s+(?:minute|moment|few\s+minutes)\s+ago|방금|좀\s*전에", text):
        return now - 600.0, None, "just_now"
    if re.search(r"\bearlier\b|아까", text):
        return now - 3 * hour, None, "earlier"
    if re.search(r"\btoday\b|오늘", text):
        return day, None, "today"
    return None


# ------------------------------------------------------------- consent

_YES = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|alright|well|so|hey)[,.!\s]+)?(?:"
    r"yes|yeah|yep|yup|ya|sure|ok(?:ay)?|alright|go\s+ahead|go\s+for\s+it|please(?:\s+do)?|"
    r"do\s+it|do\s+(?:that|them|those|it)(?:\s+now)?|sounds\s+good|let'?s\s+do\s+it|"
    r"(?:can|could|would)\s+you\s+(?:please\s+)?do\s+(?:that|it|them|those)|"
    r"응|어|네|예|그래|좋아|해\s*줘|해줘|해\s*주세요|해주세요|부탁해|진행해|ㅇㅇ|웅|넵|그렇게\s*해"
    r")(?=$|[\s.,!?~])",
    re.IGNORECASE,
)
_NO = re.compile(
    r"^\s*(?:no|nope|nah|not\s+now|don'?t|never\s*mind|cancel|stop|no\s+thanks|"
    r"아니|아니요|아니오|아뇨|아냐|됐어|괜찮아|하지\s*마|안\s*해도\s*돼|나중에|취소|그만)"
    r"(?=$|[\s.,!?~])",
    re.IGNORECASE,
)


def read_consent(text: str) -> str:
    """"yes", "no", or "" when the reply is about something else."""
    said = " ".join(str(text or "").split())
    if not said or len(said.split()) > 10:
        return ""
    if _NO.search(said):
        return "no"
    if _YES.search(said):
        return "yes"
    return ""


# ---------------------------------------------------------------- read


def _matches(patterns, said: str, *, search: bool = False):
    for pattern in patterns:
        match = pattern.search(said) if search else pattern.match(said)
        if match:
            return match
    return None


def read(text: str, *, now: float | None = None) -> ActivityCommand | None:
    """What this turn asks of the activity feature, if anything."""
    said = " ".join(str(text or "").split()).strip()
    if not said:
        return None
    now = time.time() if now is None else float(now)

    if _matches(_FORGET_STARTUP, said, search=True):
        return ActivityCommand("forget_startup")
    if _matches(_SAVE_STARTUP, said, search=True):
        return ActivityCommand("save_startup")
    boot_recall = _matches(_BOOT_RECALL, said, search=True)
    boot_replay = _matches(_BOOT_REPLAY, said, search=True)
    if boot_replay and (boot_recall or re.search(rf"{_BOOT_EN}|{_BOOT_KO}", said, re.IGNORECASE)):
        return ActivityCommand("replay_boot")
    if boot_recall:
        return ActivityCommand("recall_boot")
    match = _matches(_REPLAY_LAST, said)
    if match:
        return ActivityCommand("replay_last", count=_count(match))
    if _matches(_PAUSE, said):
        return ActivityCommand("pause")
    if _matches(_RESUME, said):
        return ActivityCommand("resume")
    match = _matches(_FORGET_ACTIVITY, said)
    if match:
        window = time_window(match.group("rest") or "", now)
        if window is None:
            return ActivityCommand("forget_activity", everything=True)
        since, until, when = window
        return ActivityCommand("forget_activity", since=since, until=until, when=when)
    return _recall(said, now)


def _recall(said: str, now: float) -> ActivityCommand | None:
    window = time_window(said, now)
    asked = False
    match = _RECALL_EN_VERB.search(said)
    if match and not _NOT_RECALL.search(said):
        verb = match.group("verb").casefold()
        computer_verb = verb.startswith(_COMPUTER_VERBS)
        asked = computer_verb or window is not None or bool(_PLACE_EN.search(said))
    if not asked:
        asked = any(pattern.search(said) for pattern in _RECALL_EN)
    if not asked and re.search(r"[가-힣]", said):
        first, *others = _RECALL_KO
        asked = bool(first.search(said)) or (
            any(pattern.search(said) for pattern in others)
            and bool(_ASKS_KO.search(said))
        )
    if not asked:
        return None
    since, until, when = window if window is not None else (
        datetime.fromtimestamp(now).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ).timestamp(),
        None, "today",
    )
    return ActivityCommand(
        "recall", since=since, until=until, when=when,
        kinds=_kinds_for(said), about=_about(said),
    )
