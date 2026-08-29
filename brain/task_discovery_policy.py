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
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveryAdvice:
    """A safe, spoken choice offered before external research begins."""

    category: str
    source_kind: str
    preference_hint: str
    offer_text: str
    browser_ready: bool


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
            re.compile(r"\b(?:restaurant|restaurants|cafe|cafes|food|dining|eat)\b|맛집|식당|카페", re.I),
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
        if browser_ready and sites:
            # Leading with the real site names is both shorter and more
            # useful than describing the category of source in the
            # abstract -- and it shows the user immediately whether Elaina
            # has the right market in mind.
            named = " and ".join(sites[:2])
            if kind == "hotel" and "dates" not in cls.extract_preferences(goal):
                offer = (
                    f"{named} can check live availability and prices in "
                    f"{market}. What dates are you staying, or should I give "
                    "a general shortlist without live rates?"
                )
            else:
                offer = (
                    f"{named} are what people in {market} actually use for "
                    f"this. Want me to check there, or is a quick overview "
                    "enough?"
                )
        elif browser_ready:
            offer = (
                f"Live {source_kind} would narrow this better than a "
                f"general overview. Want that, or a quick overview? "
                f"{cls._short_hint(preference_hint)} helps if you have one."
            )
        else:
            offer = (
                f"For {kind} options, live {source_kind} would help with "
                f"filters, but Desktop Control Mode is off. I can give a "
                "quick web overview instead—want that?"
            )
        return DiscoveryAdvice(
            category=kind,
            source_kind=source_kind,
            preference_hint=preference_hint,
            offer_text=offer,
            browser_ready=browser_ready,
        )

    @classmethod
    def missing_required_preferences(
        cls, category: str, preferences: dict[str, str],
    ) -> tuple[str, ...]:
        """Inputs required before live listings can be compared truthfully."""
        if str(category).casefold() == "hotel" and not preferences.get("dates"):
            return ("dates",)
        return ()

    @classmethod
    def required_preference_prompt(
        cls, category: str, preferences: dict[str, str],
    ) -> str:
        if cls.missing_required_preferences(category, preferences) == ("dates",):
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
