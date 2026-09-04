"""Where the user actually lives, and what that means for research.

A recommendation is only useful in the market the user can actually buy in.
"Best second-hand marketplace" means Craigslist/eBay in the US and
번개장터/당근마켓 in Korea; "hotels near the city" means Booking.com in
Europe and 야놀자/여기어때 in Korea. Elaina previously had no notion of the
user's region at all, so every recommendation silently defaulted to whatever
the model had seen most of -- US sites -- regardless of who was asking.

Design notes
------------
* **Config-owned, never model-invented.** The site names below come from
  this module's own table or the user's ``config.yaml``, exactly like
  ``computer_control.default_search_url``. They are trusted for the same
  reason: a human wrote them down. A model still never gets to invent a
  destination domain from observed page content -- that boundary
  (``brain/browser_action_planner.py``'s ``open_url`` contract) is
  unchanged.
* **Deterministic.** Region and language selection never depends on the
  local model agreeing with a prompt instruction, per this project's
  standing "structural over prompt" rule.
* **Overridable.** ``user.preferred_sites`` in config merges over the
  built-in table, so a user in an unlisted country -- or one who simply
  prefers a different marketplace -- gets their own choice without a code
  change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# Categories deliberately mirror brain/task_discovery_policy.py's own
# category vocabulary so a goal classified there resolves here with no
# translation layer between them.
DEFAULT_COUNTRY = "US"

_COUNTRY_NAMES: dict[str, str] = {
    "KR": "South Korea",
    "US": "the United States",
    "JP": "Japan",
    "GB": "the United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "DE": "Germany",
    "FR": "France",
    "SG": "Singapore",
    "HK": "Hong Kong",
    "TW": "Taiwan",
    "CN": "China",
    "IN": "India",
}

_COUNTRY_LANGUAGES: dict[str, str] = {
    "KR": "ko",
    "JP": "ja",
    "TW": "zh",
    "CN": "zh",
    "DE": "de",
    "FR": "fr",
}

_COUNTRY_CURRENCIES: dict[str, str] = {
    "KR": "KRW",
    "US": "USD",
    "JP": "JPY",
    "GB": "GBP",
    "CA": "CAD",
    "AU": "AUD",
    "DE": "EUR",
    "FR": "EUR",
    "SG": "SGD",
    "HK": "HKD",
    "TW": "TWD",
    "CN": "CNY",
    "IN": "INR",
}

# Site *names* as a user would say them, not URLs. The browser planner
# reaches them the same way a person would -- searching the name, then
# following a real observed result link -- so a stale entry degrades into
# an ordinary search rather than a broken navigation.
_REGIONAL_SITES: dict[str, dict[str, tuple[str, ...]]] = {
    "KR": {
        "hotel": ("야놀자", "여기어때", "네이버 호텔"),
        "restaurant": ("네이버 지도", "다이닝코드", "캐치테이블"),
        "secondhand": ("당근마켓", "번개장터", "중고나라"),
        "shopping": ("네이버쇼핑", "쿠팡", "다나와"),
        "gpu": ("다나와", "컴퓨존", "에누리"),
        "car": ("엔카", "KB차차차", "보배드림"),
        "flight": ("네이버 항공권", "스카이스캐너"),
        "job": ("사람인", "잡코리아"),
        "realestate": ("직방", "다방", "네이버 부동산"),
    },
    "US": {
        "hotel": ("Booking.com", "Expedia", "Hotels.com"),
        "restaurant": ("Yelp", "Google Maps", "OpenTable"),
        "secondhand": ("Facebook Marketplace", "Craigslist", "OfferUp"),
        "shopping": ("Amazon", "Walmart", "Best Buy"),
        "gpu": ("Newegg", "Best Buy", "PCPartPicker"),
        "car": ("Autotrader", "Cars.com", "CarGurus"),
        "flight": ("Google Flights", "Kayak"),
        "job": ("Indeed", "LinkedIn"),
        "realestate": ("Zillow", "Redfin"),
    },
    "JP": {
        "hotel": ("楽天トラベル", "じゃらん", "Booking.com"),
        "restaurant": ("食べログ", "ぐるなび"),
        "secondhand": ("メルカリ", "ヤフオク"),
        "shopping": ("Amazon.co.jp", "楽天市場", "価格.com"),
        "gpu": ("価格.com", "ドスパラ"),
        "car": ("カーセンサー", "グーネット"),
        "flight": ("スカイスキャナー", "Google Flights"),
        "job": ("リクナビ", "doda"),
        "realestate": ("SUUMO", "HOME'S"),
    },
    "GB": {
        "hotel": ("Booking.com", "Trivago"),
        "restaurant": ("TripAdvisor", "OpenTable", "Google Maps"),
        "secondhand": ("Gumtree", "eBay UK", "Facebook Marketplace"),
        "shopping": ("Amazon.co.uk", "Argos"),
        "gpu": ("Scan", "Overclockers UK"),
        "car": ("Auto Trader UK", "Motors.co.uk"),
        "flight": ("Skyscanner", "Google Flights"),
        "job": ("Indeed", "Reed"),
        "realestate": ("Rightmove", "Zoopla"),
    },
}

# Host suffixes for default configured sources. These are execution policy,
# not recommendations: once the user chooses specialised research, an
# observed search result may be followed only onto the selected source class.
# Custom preferred-site overrides intentionally get no guessed host mapping.
_REGIONAL_SITE_HOSTS: dict[str, dict[str, tuple[str, ...]]] = {
    "KR": {
        "hotel": ("yanolja.com", "goodchoice.kr", "naver.com"),
        "restaurant": ("map.naver.com", "diningcode.com", "catchtable.co.kr"),
        "secondhand": ("daangn.com", "bunjang.co.kr", "joongna.com"),
        "shopping": ("naver.com", "coupang.com", "danawa.com"),
        "gpu": ("danawa.com", "compuzone.co.kr", "enuri.com"),
        "car": ("encar.com", "kbchachacha.com", "bobaedream.co.kr"),
    },
    "US": {
        "hotel": ("booking.com", "expedia.com", "hotels.com"),
        "restaurant": ("yelp.com", "google.com", "opentable.com"),
        "secondhand": ("facebook.com", "craigslist.org", "offerup.com"),
        "shopping": ("amazon.com", "walmart.com", "bestbuy.com"),
        "gpu": ("newegg.com", "bestbuy.com", "pcpartpicker.com"),
        "car": ("autotrader.com", "cars.com", "cargurus.com"),
    },
}

# When a request is about somewhere other than home, the destination's own
# market is what matters ("hotels in Hong Kong" is a Hong Kong search), but
# the *language and currency* the user reads in stay theirs. This split is
# why destination and locale are tracked separately below.
_DESTINATION_HINT = re.compile(
    r"\b(?:in|at|near|around|to|from)\s+"
    r"([A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*){0,2})",
)

# Things whose own name says they are somewhere. A closed list of
# place-kind nouns, capitalised as part of a proper name -- "University of
# Washington", "Incheon Airport", "Seattle Public Library". Deliberately
# not a gazetteer: it recognises the *kind*, which is what makes it work
# for a place nobody has heard of.
_NAMED_PLACE = re.compile(
    r"\b[A-Z][\w'-]*(?:\s+(?:of|the|de|del)\s+[A-Z][\w'-]*|\s+[A-Z][\w'-]*){0,3}"
    r"\b(?:University|College|Institute|Academy|Polytechnic|Airport|"
    r"Station|Hospital|Museum|Stadium|Library|Cathedral|Palace|Campus|"
    r"Terminal|Harbour|Harbor)\b"
    r"|\b(?:University|College|Institute|Academy)\s+of\s+[A-Z][\w'-]*"
)

# Only searches whose answer genuinely depends on where the user is should be
# pinned to the home market. The old code localized *every* placeless query,
# turning global facts such as "latest World Cup winner" into "... in Seoul"
# and exchange-rate lookups into Korean-local searches. Keep this deliberately
# narrower than generic words such as "current", "best", or "price".
_MARKET_SENSITIVE_QUERY = re.compile(
    # The trailing \b closes the whole alternation, so every noun here had
    # to be spelled with its plural or it silently failed to match. Measured
    # live: "hotel" localized and "hotels" did not, and a search for "easy
    # to eat dinner restaurants" by a user in Seoul returned a restaurant in
    # Nha Trang.
    r"\b(?:near\s+me|nearby|local(?:ly)?|weather|forecast|air\s+quality|"
    r"traffic|transit|hotels?|lodging|restaurants?|cafes?|cafés?|bars?|"
    r"marketplaces?|eateries|eatery|diners?|"
    r"second[-\s]?hand|shopping|shops?|stores?|retailers?|dealers?|"
    r"flights?|airfare|jobs?|real\s+estate|apartments?)\b|"
    r"주변|근처|날씨|호텔|숙소|맛집|식당|중고|쇼핑|항공권|취업|부동산",
    flags=re.IGNORECASE,
)

# Cities matter more than countries here: people say "hotels in Seoul", not
# "hotels in South Korea". Only places whose market this module can actually
# serve are listed -- an unlisted destination resolves to no sites at all,
# which correctly produces a plain search instead of a wrong-country
# suggestion.
_PLACE_COUNTRIES: dict[str, str] = {
    "korea": "KR", "south korea": "KR", "seoul": "KR", "busan": "KR",
    "incheon": "KR", "jeju": "KR", "daegu": "KR", "gangnam": "KR",
    "hongdae": "KR", "myeongdong": "KR", "한국": "KR", "서울": "KR",
    "japan": "JP", "tokyo": "JP", "osaka": "JP", "kyoto": "JP",
    "fukuoka": "JP", "sapporo": "JP", "일본": "JP", "도쿄": "JP",
    "america": "US", "usa": "US", "united states": "US",
    "new york": "US", "los angeles": "US", "san francisco": "US",
    "chicago": "US", "seattle": "US", "boston": "US", "miami": "US",
    "guam": "US",
    "britain": "GB", "england": "GB", "united kingdom": "GB",
    "london": "GB", "manchester": "GB", "edinburgh": "GB",
    "hong kong": "HK", "singapore": "SG", "taiwan": "TW", "taipei": "TW",
    "china": "CN", "beijing": "CN", "shanghai": "CN",
    "canada": "CA", "toronto": "CA", "vancouver": "CA",
    "australia": "AU", "sydney": "AU", "melbourne": "AU",
    "germany": "DE", "berlin": "DE", "munich": "DE",
    "france": "FR", "paris": "FR",
    "india": "IN", "mumbai": "IN", "delhi": "IN",
}


# Two different languages meet in this module and must never be confused.
# ``LocaleContext.language`` is the language of the user's *market* -- what
# local sources and shop listings are written in. What Elaina *answers* in is
# ``language.response`` in config.yaml, and only that. Saying "answer in their
# own language" next to "the user is in South Korea" made her greet an
# English-configured user in Korean, so nothing here decides the reply
# language any more: callers pass in the configured one and it is named
# outright.
LANGUAGE_NAMES = {
    "en": "English",
    "ko": "Korean",
}


def language_name(code: str) -> str:
    """A prompt-ready name for a language code, e.g. "en" -> "English"."""
    key = str(code or "").strip().casefold()
    key = key.split("-")[0]
    return LANGUAGE_NAMES.get(key, key.upper() or "English")


@dataclass(frozen=True)
class LocaleContext:
    """Everything a planner or prompt needs to localize a recommendation."""

    country_code: str = DEFAULT_COUNTRY
    country_name: str = "the United States"
    city: str = ""
    language: str = "en"
    currency: str = "USD"
    preferred_sites: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def home(self) -> str:
        return f"{self.city}, {self.country_name}" if self.city else self.country_name

    def sites_for(self, category: str) -> tuple[str, ...]:
        return tuple(self.preferred_sites.get(str(category).strip().lower(), ()))


class UserLocale:
    """Resolve the user's market once, then answer questions about it."""

    def __init__(
        self,
        *,
        country: str = "",
        city: str = "",
        currency: str = "",
        search_language: str = "",
        preferred_sites: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        code = self._normalize_country(country)
        self.context = LocaleContext(
            country_code=code,
            country_name=_COUNTRY_NAMES.get(code, code),
            city=str(city or "").strip(),
            language=(
                str(search_language).strip().lower()
                or _COUNTRY_LANGUAGES.get(code, "en")
            ),
            currency=(
                str(currency).strip().upper()
                or _COUNTRY_CURRENCIES.get(code, "USD")
            ),
            preferred_sites=self._merge_sites(code, preferred_sites),
        )

    # ---------------------------------------------------------------- setup

    @classmethod
    def from_config(cls, config: Any) -> "UserLocale":
        def value(key: str, default: str = "") -> Any:
            try:
                return config.get("user", key, default=default, required=False)
            except Exception:
                return default

        return cls(
            country=str(value("country", "auto") or "auto"),
            city=str(value("city", "") or ""),
            currency=str(value("currency", "") or ""),
            search_language=str(value("search_language", "") or ""),
            preferred_sites=value("preferred_sites", {}) or {},
        )

    @staticmethod
    def _normalize_country(country: str) -> str:
        code = str(country or "").strip().upper()
        if code in {"", "AUTO"}:
            return UserLocale._detect_country()
        if len(code) == 2:
            return code
        for candidate, name in _COUNTRY_NAMES.items():
            if name.lower().replace("the ", "") == code.lower():
                return candidate
        return DEFAULT_COUNTRY

    @staticmethod
    def _detect_country() -> str:
        """Read the OS locale. Never fails the caller -- falls back instead."""
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(  # type: ignore[attr-defined]
                buffer, len(buffer)
            ):
                tag = str(buffer.value)
                if "-" in tag:
                    return tag.rsplit("-", 1)[-1].upper()[:2]
        except Exception:
            pass
        try:
            import locale as _locale

            tag = str(_locale.getdefaultlocale()[0] or "")
            if "_" in tag:
                return tag.rsplit("_", 1)[-1].upper()[:2]
        except Exception:
            pass
        return DEFAULT_COUNTRY

    @staticmethod
    def _merge_sites(
        code: str,
        overrides: Mapping[str, Iterable[str]] | None,
    ) -> dict[str, tuple[str, ...]]:
        merged = {
            category: tuple(sites)
            for category, sites in _REGIONAL_SITES.get(code, {}).items()
        }
        for category, sites in dict(overrides or {}).items():
            key = str(category).strip().lower()
            if isinstance(sites, str):
                values = tuple(
                    part.strip() for part in sites.split(",") if part.strip()
                )
            else:
                values = tuple(str(site).strip() for site in sites if str(site).strip())
            if values:
                merged[key] = values
        return merged

    # --------------------------------------------------------------- lookup

    @property
    def country_code(self) -> str:
        return self.context.country_code

    @property
    def language(self) -> str:
        return self.context.language

    def sites_for(self, category: str) -> tuple[str, ...]:
        """Preferred sites for the user's own market, best first."""
        return self.context.sites_for(category)

    def sites_for_market(self, category: str, country_code: str) -> tuple[str, ...]:
        """Preferred sites for another country, used for travel abroad."""
        code = str(country_code or "").strip().upper()
        if code == self.country_code:
            return self.sites_for(category)
        return tuple(_REGIONAL_SITES.get(code, {}).get(str(category).lower(), ()))

    def market_for(self, text: str) -> str:
        """Which country's market a request is about.

        "hotels in Seoul" is a Korean-market request no matter where the
        user lives, and "second-hand sites" with no place named is a
        home-market one. Returns the user's own country when nothing else
        is named, so the common case needs no special handling.
        """
        for match in _DESTINATION_HINT.finditer(str(text or "")):
            place = " ".join(match.group(1).split()).strip().lower()
            while place:
                code = _PLACE_COUNTRIES.get(place)
                if code:
                    return code
                if " " not in place:
                    break
                place = place.rsplit(" ", 1)[0]
        lowered = str(text or "").lower()
        for place, code in _PLACE_COUNTRIES.items():
            # Whole words only. A bare substring scan read "us" out of
            # "used" and sent a Korean user to Craigslist for a
            # second-hand phone -- the exact failure this module exists
            # to prevent.
            if place.isascii():
                if re.search(rf"(?<![a-z]){re.escape(place)}(?![a-z])", lowered):
                    return code
            elif place in lowered:
                return code
        return self.country_code

    def mentions_foreign_destination(self, text: str, home_country: str = "") -> bool:
        """Whether the request is about a market other than the user's own."""
        home = str(home_country or self.country_code).upper()
        return self.market_for(text) != home

    def sites_for_goal(self, category: str, goal: str) -> tuple[tuple[str, ...], str]:
        """The right sites for whichever market this goal is about.

        Returns ``((), "")`` when the destination's market isn't one this
        module knows -- deliberately, because suggesting the user's home
        marketplaces for a trip abroad is worse than suggesting nothing.
        """
        market = self.market_for(goal)
        sites = self.sites_for_market(category, market)
        if not sites:
            return (), ""
        return sites, _COUNTRY_NAMES.get(market, market)

    def source_hosts_for_goal(self, category: str, goal: str) -> tuple[str, ...]:
        """Allowed host suffixes for this goal's default specialised sites."""
        market = self.market_for(goal)
        category = str(category).strip().lower()
        configured = self.sites_for_market(category, market)
        defaults = tuple(_REGIONAL_SITES.get(market, {}).get(category, ()))
        if not configured or configured != defaults:
            return ()
        return tuple(_REGIONAL_SITE_HOSTS.get(market, {}).get(category, ()))

    @staticmethod
    def _names_a_place(text: str) -> bool:
        """Whether a preposition here really introduces a destination.

        ``_DESTINATION_HINT`` alone matches "easy **to eat** dinner", which
        looks exactly like "flights **to Tokyo**". Reading that as a named
        destination left the query unlocalized, and a Seoul user asking for
        easy-to-eat dinner restaurants got one in Nha Trang.

        A hint counts when it names a place this module knows, or when the
        words after the preposition are capitalised the way a place name is
        -- which keeps somewhere unlisted ("in Nha Trang") respected while
        an infinitive is not.
        """
        # A place does not need a preposition in front of it to be one.
        # Conversation context is appended bare -- "studio apartments $1000
        # to $1500 University of Washington, Seattle" -- so requiring the
        # hint meant this read as placeless and the market was added anyway:
        # every rental query in the first dogfooding session ended "in South
        # Korea" while naming a university in Seattle. Only names this
        # module already knows count here; an unlisted capitalised phrase
        # still needs its preposition, which is what keeps "easy to eat"
        # from reading as a destination.
        words = [
            word.strip(",.;:!?").casefold()
            for word in str(text or "").split()
        ]
        for size in (3, 2, 1):
            for index in range(len(words) - size + 1):
                if " ".join(words[index:index + size]) in _PLACE_COUNTRIES:
                    return True

        for match in _DESTINATION_HINT.finditer(str(text or "")):
            phrase = " ".join(match.group(1).split()).strip()
            place = phrase.lower()
            while place:
                if place in _PLACE_COUNTRIES:
                    return True
                if " " not in place:
                    break
                place = place.rsplit(" ", 1)[0]
            # An article is not part of the name. Measured live: "Korean
            # restaurants near the University of Washington" captured
            # "the University of", whose first letter is lowercase, so the
            # query read as placeless and the search went out as
            # "...near the University of Washington? in South Korea".
            named = re.sub(r"^(?:the|a|an)\s+", "", phrase, flags=re.I)
            if named[:1].isupper():
                return True

        # A named institution or landmark is a place whether or not a
        # preposition introduced it. Its own name says so: a thing called
        # a University, an Airport or a Library is somewhere, and that is
        # the one signal available without a gazetteer.
        return bool(_NAMED_PLACE.search(str(text or "")))

    def localize_query(
        self,
        query: str,
        *,
        category: str = "",
        assume_local: bool = False,
        already_placed: bool = False,
    ) -> str:
        """Add the market only when a query's answer depends on location.

        A query that already names a destination ("hotels in Hong Kong") is
        left exactly as written -- adding the user's home country there
        would actively make the results wrong. Global facts, sports, releases,
        exchange rates, and other placeless but non-local questions are also
        left alone.
        """
        text = " ".join(str(query).split()).strip()
        if not text:
            return text
        if self.country_code == DEFAULT_COUNTRY and self.language == "en":
            return text
        # The caller may already know a place went into this query -- it
        # is holding the anchor or the location it appended. That is worth
        # more than re-deriving it from the words, which cannot recognise a
        # name it has never heard of: "University of Washington" read as
        # placeless and the user's own country was appended to a search
        # about Seattle.
        if already_placed or self._names_a_place(text):
            return text
        category = str(category or "").strip().casefold()
        market_categories = set(_REGIONAL_SITES.get(self.country_code, {}))
        if (
            not assume_local
            and category not in market_categories
            and not _MARKET_SENSITIVE_QUERY.search(text)
        ):
            # ``assume_local`` is for a caller that already knows this is a
            # real-world purchase or place -- an open recommendation for a
            # guitar to actually buy, say, where no noun in the query is
            # market-sensitive on its own but the market still decides what
            # can be bought and at what price.
            return text
        place = self.context.city or self.context.country_name
        return f"{text} in {place}"

    def context_text(self, response_language: str = "en") -> str:
        """A short, factual block for any prompt that makes recommendations.

        ``response_language`` is the configured reply language, not this
        locale's. The two are different for anyone living outside their
        reply language's home country, and this block used to end with
        "always answer the user in their own language" directly under "The
        user is in South Korea" -- which read as an instruction to answer in
        Korean, and was obeyed.
        """
        reply_in = language_name(response_language)
        lines = [
            "USER LOCATION",
            f"The user is in {self.context.home}.",
            (
                f"Recommend services, shops, and websites that actually "
                f"operate in {self.context.country_name}, and quote prices in "
                f"{self.context.currency} when the source gives them."
            ),
            (
                "If the request is about somewhere else, use that "
                "destination's own local options instead of the user's."
            ),
        ]
        if language_name(self.context.language) != reply_in:
            lines.append(
                f"Local sources are often in "
                f"{language_name(self.context.language)}; reading them is "
                f"fine, but write the answer itself in {reply_in}."
            )
        return "\n".join(lines)

    def site_guidance(self, category: str, *, goal: str = "") -> str:
        """Name the right local sites for a category, or say nothing.

        The market comes from the goal, not from where the user lives: a
        Korean user asking about Hong Kong gets Hong Kong's market (and,
        since this module doesn't know it, no site hint at all) rather
        than 야놀자.
        """
        sites, market = self.sites_for_goal(category, goal or "")
        if not sites:
            return ""
        listed = ", ".join(sites)
        return (
            f"For {category} in {market}, these are the sites people there "
            f"actually use, best first: {listed}. Put one of their names "
            "into your search query and follow the real result link onto "
            "it -- never construct its address yourself. A listing the "
            "user cannot actually buy from is not a useful answer."
        )
