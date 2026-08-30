"""Deterministic, user-facing preflight for research and recommendation tasks.

This is deliberately *not* a hidden chain-of-thought generator.  It answers
one narrow product question before a task planner can take any external action:

    Would a live, specialised source materially improve this recommendation,
    and if so what preferences would make that research useful?

The result is a short conversational offer.  The user chooses whether Elaina
should spend the extra effort; only then does ``TaskPlanner`` run existing
web-search/browser capabilities.  Keeping this policy deterministic gives the
same natural request the same preflight even when the local model is busy or
unavailable.

Deterministic was doing too much work, though.  Every hotel request produced
one byte-identical sentence, and asking it again on the next hotel request
made a reasonable question sound like a recorded message -- reported from a
real session, seen "multiple times today".  Two things separate a good
preflight from a script, and neither changes what it decides:

* ``advise`` still answers *whether* an offer helps, unchanged and pure.
* :meth:`TaskDiscoveryPolicy.offer_for` answers *whether to say it now, and
  in what words* -- suppressing a repeat within the session and rotating the
  phrasing when it does speak.

The hard gate is untouched by all of this.  A booking still cannot proceed
without dates; that lives in ``deliberation/clarification.py`` and
``TaskPlanner``, and is a different question from whether to offer a choice
of source.
"""

from __future__ import annotations

import random
import re
import time
from collections import deque
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class DiscoveryAdvice:
    """A safe, spoken choice offered before external research begins."""

    category: str
    source_kind: str
    preference_hint: str
    offer_text: str
    browser_ready: bool
    # Every equivalent way of saying it. ``offer_text`` is the first of
    # these; ``offer_for`` picks a different one when the last time she said
    # this she used that one.
    phrasings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyReply:
    """Meaning of a reply to a pending discovery offer.

    ``mode`` is intentionally a small closed vocabulary.  It is not a model
    instruction and it never contains a destination URL.
    """

    mode: str  # "specialized" | "overview" | "unclear"
    preferences: dict[str, str]


class TaskDiscoveryPolicy:
    """Recognise recommendation tasks that benefit from live filters.

    Categories describe *source types*, not endorsed domains.  A user who
    opts into live research is taken to a fixed search engine first and Elaina
    can only follow an observed result link; a model never gets permission to
    invent a third-party URL from this policy.
    """

    _CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
        (
            "hotel",
            re.compile(
                r"\b(?:hotels?|hostels?|resorts?|accommodations?|"
                r"place to stay|lodging)\b|호텔|숙소|펜션|게스트하우스",
                re.I,
            ),
            "booking listings",
            "dates, area, nightly budget, or guest count",
        ),
        (
            "restaurant",
            # Meal and dish words count too. "I want Korean BBQ" is a food
            # request by any reading, and without them the category came
            # back empty -- which left a retired preference with no general
            # noun to fall back on ("soft places" instead of "soft
            # restaurants").
            re.compile(
                r"\b(?:restaurant|restaurants|cafe|cafes|food|dining|eat|"
                r"eatery|eateries|bbq|barbecue|barbeque|"
                r"dinner|lunch|breakfast|brunch|supper|meal|meals)\b"
                r"|맛집|식당|카페|밥|저녁|점심|아침",
                re.I,
            ),
            "local review listings",
            "area, cuisine, budget, or dietary needs",
        ),
        (
            "gpu",
            re.compile(
                r"\b(?:gpus?|graphics cards?|video cards?|rtx\s*\d+|radeon)\b|"
                r"그래픽카드|외장그래픽",
                re.I,
            ),
            "price-comparison and retailer listings",
            "budget, country, new versus used, or target games/resolution",
        ),
        (
            "car",
            re.compile(r"\b(?:car|cars|vehicle|vehicles|suv|sedan|truck)\b|자동차|중고차", re.I),
            "vehicle marketplace listings",
            "region, budget, new versus used, or vehicle needs",
        ),
        (
            "flight",
            re.compile(r"\b(?:flight|flights|airfare|plane ticket)\b|항공권|비행기표", re.I),
            "live travel listings",
            "dates, departure city, cabin, or budget",
        ),
        # Second-hand is its own category, not a flavour of "shopping":
        # the sites serving it are entirely different ones, and they are
        # the most region-specific of all (see brain/user_locale.py --
        # Craigslist in the US, 당근마켓/번개장터 in Korea, メルカリ in Japan).
        # Both patterns are deliberately narrow. A bare "buy" or "used"
        # would fire on half of ordinary conversation, and every match
        # here escalates a turn to the task planner.
        (
            "secondhand",
            re.compile(
                r"\b(?:second[- ]?hand|secondhand|pre[- ]?owned|thrift|"
                r"resale|resell)\b"
                r"|\b(?:buy|sell|selling|find|get)\s+(?:a\s+|an\s+|some\s+)?used\b"
                r"|\bused\s+(?:market|marketplace|goods|items?)\b"
                r"|중고",
                re.I,
            ),
            "second-hand marketplace listings",
            "region, budget, item condition, or pickup versus delivery",
        ),
        (
            "shopping",
            re.compile(
                r"\b(?:where\s+to\s+buy|best\s+place\s+to\s+buy|"
                r"cheapest\s+place|online\s+store|retailer|"
                r"shopping\s+site|best\s+deal|good\s+deals?)\b"
                r"|쇼핑몰|최저가",
                re.I,
            ),
            "retailer and price-comparison listings",
            "budget, country, brand, or delivery timing",
        ),
    )
    _SELECTION_LANGUAGE = re.compile(
        r"\b(?:find|search|shortlist|recommend|best|top|compare|list|"
        r"options?|cheapest|buy|purchase|shop|deal|price|under|below|"
        r"available|availability|book|reserve|stay)\b"
        # The user speaks Korean too, and an all-English signal list
        # silently dropped every Korean recommendation request onto the
        # plain conversation path.
        r"|추천|어디서|찾아|알아봐|최고|최저|저렴|싼\s*곳|살까|사고\s*싶",
        re.I,
    )
    _DIRECT_EXECUTION = re.compile(
        r"\b(?:search|check|look)\b.{0,24}\b(?:now|right away|immediately)\b"
        r"|\b(?:do not ask|don't ask|skip (?:the )?(?:questions|details))\b",
        re.I,
    )
    _NEGATIVE = re.compile(
        r"^(?:no|nope|nah)\b|\b(?:quick|general) overview\b|"
        r"\b(?:don't|do not|skip)\s+(?:use|check|open|browse)\b|"
        r"\bnot (?:necessary|needed)\b",
        re.I,
    )
    _POSITIVE = re.compile(
        r"^(?:yes|yeah|yep|sure|okay|ok)\b|\b(?:go ahead|please|use (?:a |the )?"
        r"(?:site|listings?)|live (?:search|listings?)|check (?:a |the )?(?:site|listings?))\b",
        re.I,
    )
    _PREFERENCE_SIGNAL = re.compile(
        r"[$₩€£¥]\s?\d|\b(?:under|below|less than|up to|around|near|in|"
        r"between|from|to|tonight|tomorrow|weekend|weekday|new|used|"
        r"adults?|guests?|people|stars?)\b|\d{4}-\d{1,2}-\d{1,2}",
        re.I,
    )
    _BUDGET = re.compile(
        r"(?:under|below|less than|up to|budget(?: of)?|around)\s*"
        r"((?:[$₩€£¥]\s?\d[\d,.]*|\d[\d,.]*\s*(?:won|dollars?|usd|eur|euros?|pounds?)))",
        re.I,
    )
    _AREA = re.compile(
        r"\b(?:near|around|in)\s+([A-Za-z][A-Za-z0-9' -]{1,48}?)(?="
        r"\s*(?:,|\.|$|under\b|below\b|for\b|with\b|and\b|"
        r"tonight\b|tomorrow\b|this\s+weekend\b|next\s+weekend\b|"
        r"\d{4}-\d{1,2}-\d{1,2}))",
        re.I,
    )
    _DATE = re.compile(
        r"\b(?:tonight|tomorrow|this weekend|next weekend|"
        r"\d{4}-\d{1,2}-\d{1,2}(?:\s*(?:to|-|through)\s*\d{4}-\d{1,2}-\d{1,2})?)\b",
        re.I,
    )

    @classmethod
    def category_for(cls, text: str) -> tuple[str, str, str] | None:
        text = str(text)
        matches: dict[str, tuple[str, str, str]] = {}
        for category, pattern, source_kind, preference_hint in cls._CATEGORY_PATTERNS:
            if pattern.search(text):
                matches[category] = (category, source_kind, preference_hint)
        # Specific verticals retain their own marketplaces. For a product,
        # however, "second hand" changes the source class completely: a used
        # RTX card belongs on 당근/번개장터, not new-product GPU retailers.
        for preferred in (
            "hotel", "restaurant", "car", "flight", "secondhand", "gpu", "shopping",
        ):
            if preferred in matches:
                return matches[preferred]
        return None

    @classmethod
    def needs_discovery_conversation(cls, text: str) -> bool:
        """Whether this is an options/recommendation request, not a fact.

        A bare category noun is not enough: ``what is a GPU`` and ``hotel
        check-in time`` should retain the ordinary conversation path.
        """
        return bool(cls.category_for(text) and cls._SELECTION_LANGUAGE.search(str(text)))

    @classmethod
    def requests_immediate_execution(cls, text: str) -> bool:
        return bool(cls._DIRECT_EXECUTION.search(str(text)))

    @classmethod
    def advise(
        cls,
        goal: str,
        *,
        browser_ready: bool,
        has_prior_candidates: bool = False,
        locale: object | None = None,
    ) -> DiscoveryAdvice | None:
        """Return an offer only when the added source would help.

        A follow-up that already names or refers to candidates should proceed
        to verification rather than asking the user to choose a source again.
        """
        if (
            has_prior_candidates
            or cls.requests_immediate_execution(goal)
            # A person who picked the overview branch has already made the
            # source/detail choice.  Asking the exact same choice again is
            # what caused the Guam loop in the recorded conversation.
            or re.search(r"\b(?:quick|general)\s+overview\b", str(goal), re.I)
            # Said plainly: general information, not live rates. Offering the
            # live-source choice again reopens a loop they just closed.
            or cls.wants_general_information(goal)
        ):
            return None
        category = cls.category_for(goal)
        if category is None or not cls.needs_discovery_conversation(goal):
            return None
        kind, source_kind, preference_hint = category
        # Naming the sites the user's own country actually uses is the
        # difference between a useful offer and one they cannot act on:
        # "a second-hand marketplace" means Craigslist in the US and
        # 당근마켓/번개장터 in Korea. The names come from configuration
        # (brain/user_locale.py), never from the model or a webpage.
        sites, market = cls._local_sites(kind, goal, locale)
        phrasings = cls._phrasings(
            kind=kind,
            source_kind=source_kind,
            preference_hint=preference_hint,
            sites=sites,
            market=market,
            browser_ready=browser_ready,
            needs_dates=(
                kind == "hotel"
                and "dates" not in cls.extract_preferences(goal)
            ),
        )
        return DiscoveryAdvice(
            category=kind,
            source_kind=source_kind,
            preference_hint=preference_hint,
            # The first phrasing, always. ``advise`` stays pure and
            # repeatable; ``offer_for`` is what varies.
            offer_text=phrasings[0],
            browser_ready=browser_ready,
            phrasings=phrasings,
        )

    @classmethod
    def _phrasings(
        cls,
        *,
        kind: str,
        source_kind: str,
        preference_hint: str,
        sites: tuple[str, ...],
        market: str,
        browser_ready: bool,
        needs_dates: bool,
    ) -> tuple[str, ...]:
        """Every way of saying the same offer.

        Each variant carries the same load-bearing content -- the real site
        names, the dates question, the overview alternative, the honest
        reason when Desktop Control Mode is off. Only the wrapper changes,
        so rotating them cannot alter what is being offered or agreed to.
        ``tests/test_task_discovery_policy.py`` asserts that per variant.
        """
        if browser_ready and sites:
            # Leading with the real site names is both shorter and more
            # useful than describing the category of source in the
            # abstract -- and it shows the user immediately whether Elaina
            # has the right market in mind.
            named = " and ".join(sites[:2])
            if needs_dates:
                return (
                    f"{named} can check live availability and prices in "
                    f"{market}. What dates are you staying, or should I give "
                    "a general overview without live rates?",
                    f"What dates are you looking at? With those I can pull "
                    f"live rates from {named} in {market} -- otherwise I'll "
                    "keep it to a general overview.",
                    f"I can get live prices from {named} in {market} if you "
                    "tell me your dates. Or say overview and I'll skip the "
                    "live rates.",
                    f"{named} would have real availability for {market}. "
                    "What dates are you staying? A general overview works "
                    "too if you'd rather.",
                )
            return (
                f"{named} are what people in {market} actually use for "
                f"this. Want me to check there, or is a quick overview "
                "enough?",
                f"In {market} that usually means {named}. Should I look "
                "there, or is a quick overview enough?",
                f"{named} is where I'd look for this in {market}. Want me "
                "to, or is a quick overview fine?",
                f"I could check {named} -- that's what {market} actually "
                "uses. Or a quick overview, if you'd rather.",
            )
        if browser_ready:
            hint = cls._short_hint(preference_hint)
            return (
                f"Live {source_kind} would narrow this better than a "
                f"general overview. Want that, or a quick overview? "
                f"{hint} helps if you have one.",
                f"I can pull live {source_kind} for this, or keep it to a "
                f"quick overview. Which would you rather? {hint} helps if "
                "you have one.",
                f"Want me to check live {source_kind}, or is a quick "
                f"overview enough? {hint} helps if you have one.",
                f"Live {source_kind} or a quick overview -- your call. "
                f"{hint} helps if you have one.",
            )
        return (
            f"For {kind} options, live {source_kind} would help with "
            f"filters, but Desktop Control Mode is off. I can give a "
            "quick web overview instead—want that?",
            f"Desktop Control Mode is off, so I can't pull live "
            f"{source_kind}. Want a quick web overview instead?",
            f"Desktop Control Mode is off, so live {source_kind} is not "
            "available. A quick web overview is what I can do -- want that?",
            f"Desktop Control Mode is off, so live {source_kind} is out. "
            "I can still do a quick web overview if that helps.",
        )

    # ------------------------------------------------- asking, not deciding

    def __init__(self, *, repeat_window_seconds: int = 10 * 60) -> None:
        self.repeat_window_seconds = max(0, int(repeat_window_seconds))
        # When each category was last offered, so the same question is not
        # put twice in one sitting.
        self._offered_at: dict[str, float] = {}
        # What she has already said, so she does not say it the same way.
        self._recent_phrasings: deque[str] = deque(maxlen=4)
        self._rng = random.Random()

    def question_for(self, advice: DiscoveryAdvice) -> str | None:
        """The words to ask this offer in, or ``None`` to not ask again.

        ``advise`` decides whether an offer would help; this decides whether
        to put it to the user *again*, and how to word it. Asked once, a
        choice of source is a useful question. Asked on every hotel request
        in the same sitting, in the same words, it reads as a recorded
        message -- which is what was reported.

        Deliberately separate from the advice itself, so the caller can act
        on everything the advice established -- which market's sites to
        prefer, above all -- while staying quiet. Suppressing the question
        must never mean forgetting the answer.
        """
        if self.recently_offered(advice.category):
            return None
        self._offered_at[advice.category] = time.monotonic()
        return self._phrase(advice)

    def offer_for(
        self,
        goal: str,
        *,
        browser_ready: bool,
        has_prior_candidates: bool = False,
        locale: object | None = None,
    ) -> DiscoveryAdvice | None:
        """``advise`` plus the repeat and phrasing rules, in one call."""
        advice = self.advise(
            goal,
            browser_ready=browser_ready,
            has_prior_candidates=has_prior_candidates,
            locale=locale,
        )
        if advice is None:
            return None
        question = self.question_for(advice)
        if question is None:
            return None
        return replace(advice, offer_text=question)

    def recently_offered(self, category: str) -> bool:
        """Whether this same choice was already put to the user just now."""
        if self.repeat_window_seconds <= 0:
            return False
        last = self._offered_at.get(str(category))
        if last is None:
            return False
        return (time.monotonic() - last) < self.repeat_window_seconds

    def forget_offers(self) -> None:
        """Start asking again. For a new session, and for tests."""
        self._offered_at.clear()
        self._recent_phrasings.clear()

    def _phrase(self, advice: DiscoveryAdvice) -> str:
        options = advice.phrasings or (advice.offer_text,)
        fresh = [text for text in options if text not in self._recent_phrasings]
        chosen = self._rng.choice(fresh or list(options))
        self._recent_phrasings.append(chosen)
        return chosen

    # Dates change the answer only when the answer is about a stay: what is
    # free on a given night, what it costs then, or booking it. They change
    # nothing about which hotels are famous, well regarded, or worth knowing.
    # Requiring them regardless turned "what are some famous hotels in Seoul"
    # into a booking interrogation.
    _DATE_SENSITIVE = re.compile(
        r"\bavailab(?:le|ility)\b|\bvacan(?:t|cy|cies)\b|\brooms?\s+(?:for|on)\b"
        r"|\b(?:book|booking|reserve|reservation|stay(?:ing)?|check[- ]?in|"
        r"check[- ]?out|nights?|per\s+night)\b"
        r"|\b(?:rate|rates|price|prices|cost|cheap(?:est)?)\b"
        r"|\d{1,2}\s*(?:st|nd|rd|th)?\s*[-~to]+\s*\d{1,2}"
        r"|예약|숙박|1박|가격|요금",
        re.I,
    )

    # The opposite signal: the person has said, in so many words, that they
    # want general information rather than live data. "Just tell me the
    # hotels that are famous in Seoul" is a refusal of the live-rate path,
    # and asking for dates after it restarts a loop they just exited.
    _WANTS_GENERAL = re.compile(
        r"\bjust\s+(?:tell|give|show|list)\b"
        r"|\b(?:famous|well[- ]known|renowned|iconic|popular|best[- ]known)\b"
        r"|\b(?:general|quick)\s+(?:overview|idea|list|sense)\b"
        r"|\bno\s+(?:need\s+for\s+)?(?:live|real[- ]time|current)\s+(?:rates?|prices?)\b"
        r"|그냥\s*(?:알려|말해)|유명한",
        re.I,
    )

    @classmethod
    def wants_general_information(cls, text: str) -> bool:
        """Whether the person asked for general information, not live data."""
        return bool(cls._WANTS_GENERAL.search(str(text or "")))

    @classmethod
    def dates_would_change_the_answer(cls, text: str) -> bool:
        """Whether dates materially affect what is being asked for."""
        request = str(text or "")
        if cls.wants_general_information(request):
            return False
        return bool(cls._DATE_SENSITIVE.search(request))

    @classmethod
    def missing_required_preferences(
        cls, category: str, preferences: dict[str, str], goal: str = "",
    ) -> tuple[str, ...]:
        """Inputs required before live listings can be compared truthfully.

        ``goal`` is the request itself. Without it this asked every hotel
        question for check-in dates, including ones where the dates could not
        possibly change the answer.
        """
        if str(category).casefold() != "hotel":
            return ()
        if preferences.get("dates"):
            return ()
        if goal and not cls.dates_would_change_the_answer(goal):
            return ()
        return ("dates",)

    @classmethod
    def required_preference_prompt(
        cls, category: str, preferences: dict[str, str], goal: str = "",
    ) -> str:
        if cls.missing_required_preferences(
            category, preferences, goal,
        ) == ("dates",):
            return (
                "What check-in and check-out dates should I use? I need them "
                "to compare real hotel availability and prices; say “general "
                "overview” if you do not want live rates."
            )
        return ""

    @staticmethod
    def _local_sites(
        kind: str, goal: str, locale: object | None,
    ) -> tuple[tuple[str, ...], str]:
        """The sites serving this goal's market, and that market's name.

        Empty for a goal about a country this project has no table for: a
        Korean marketplace is the wrong answer for "hotels in Hong Kong",
        and a wrong-market suggestion is worse than a generic one.
        """
        if locale is None:
            return (), ""
        try:
            return locale.sites_for_goal(kind, goal)
        except Exception:
            return (), ""

    @staticmethod
    def _short_hint(preference_hint: str) -> str:
        """The first couple of preference examples, for a spoken offer."""
        parts = [part.strip() for part in str(preference_hint).split(",")]
        parts = [part for part in parts if part][:2]
        if not parts:
            return "A preference"
        joined = " or ".join(parts)
        return joined[:1].upper() + joined[1:]

    @classmethod
    def interpret_reply(
        cls,
        reply: str,
        *,
        browser_ready: bool,
    ) -> StrategyReply:
        """Handle clear replies locally; ambiguous language stays unapproved.

        A preference-only reply (``under ₩200k near Hongdae``) is a useful
        answer to the immediately preceding offer, so it selects live
        research when it is available.  This avoids dropping exactly the
        information the user supplied while still refusing an unrelated reply.
        """
        reply = " ".join(str(reply).split()).strip()
        preferences = cls.extract_preferences(reply)
        if not reply:
            return StrategyReply("unclear", preferences)
        if cls._NEGATIVE.search(reply):
            return StrategyReply("overview", preferences)
        if cls._POSITIVE.search(reply) or preferences:
            return StrategyReply("specialized" if browser_ready else "overview", preferences)
        return StrategyReply("unclear", preferences)

    @classmethod
    def extract_preferences(cls, reply: str) -> dict[str, str]:
        """Keep user details verbatim where parsing would be speculative."""
        reply = " ".join(str(reply).split()).strip()
        if not reply:
            return {}
        preferences: dict[str, str] = {}
        budget = cls._BUDGET.search(reply)
        if budget:
            preferences["budget"] = budget.group(1).strip()
        area = cls._AREA.search(reply)
        if area:
            preferences["area"] = area.group(1).strip(" ,.")
        date = cls._DATE.search(reply)
        if date:
            preferences["dates"] = date.group(0)
        # Preserve all non-trivial detail for the planner; this is user input,
        # not a model-derived constraint.  A short yes/no alone adds nothing.
        if cls._PREFERENCE_SIGNAL.search(reply) and len(reply) > 3:
            preferences["additional_preferences"] = reply[:300]
        return preferences
