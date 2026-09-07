"""The optional structured half of a reply, and the contract around it.

The Phase 4F.6 acceptance suite. Three properties carry the whole design,
and each one is a way this could go wrong rather than a feature:

**The backend chooses.** Electron renders and decides nothing. A renderer
that reads a reply, sees "hotel" and produces cards is a second place
decisions get made, and it will disagree with the conversation eventually.

**The payload is data, never code.** No model can reach it: every field is
filled from candidates a search actually returned, and anything carrying
markup or a scheme Electron should not open is dropped rather than escaped.

**Absent means unchanged.** ``none`` is the common case, and a reply without
a surface has to behave exactly as it did before this existed -- which is
what "plain text behaviour remains unchanged" means in practice.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from brain import response_surface as surfaces
from brain import result_state as rs
from core.websocket_server import WebSocketServer
from tests.turn_harness import build_engine, machine_actions


MONITORS = (
    rs.Candidate(name="Dell S2722DGM Curved Gaming Monitor",
                 url="https://dell.com/product/s2722dgm",
                 why="fits gaming", summary="165Hz curved QHD, $279",
                 attributes=("gaming",)),
    rs.Candidate(name="LG UltraGear 27GP850-B",
                 url="https://bestbuy.com/site/lg/6467884.p",
                 why="fits gaming", summary="165Hz IPS, $349",
                 attributes=("gaming",)),
    rs.Candidate(name="Samsung Odyssey G5",
                 url="https://samsung.com/odyssey-g5",
                 why="fits gaming", summary="144Hz VA, $259"),
)


def _engine_holding_results():
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        engine = build_engine(routes={})
        engine._web_search_enabled = True
        engine.task_sessions.note_recommendation_turn(
            "find me a good gaming monitor", subject="gaming monitors",
        )
        engine.task_sessions.record_candidates(
            MONITORS, evidence=("shortlist text",),
        )
        engine.client.reply = "The LG UltraGear is the one I'd start with."
    return engine, quiet


class BuildingOneTests(unittest.TestCase):
    """From candidates a search returned, and from nothing else."""

    def test_a_shortlist_carries_what_a_card_needs(self):
        surface = surfaces.from_candidates(MONITORS, title="Monitors")

        self.assertEqual(surface.type, surfaces.SHORTLIST)
        self.assertEqual(len(surface.items), 3)
        first = surface.items[0]
        self.assertTrue(first.id)
        self.assertTrue(first.name)
        self.assertTrue(first.url)
        self.assertIn(surfaces.OPEN, first.actions)

    def test_the_card_carries_the_identity_not_the_label(self):
        # The whole reason 4F.5 made identity stable. An action coming back
        # names the thing; a label on a button is what it happens to say.
        surface = surfaces.from_candidates(MONITORS)

        self.assertEqual(
            [item.id for item in surface.items],
            [candidate.id for candidate in MONITORS],
        )

    def test_a_candidate_with_nowhere_to_go_offers_no_open(self):
        surface = surfaces.from_candidates((
            rs.Candidate(name="Juk Story Myeongdong"),
            rs.Candidate(name="Han River BBQ"),
        ))

        for item in surface.items:
            with self.subTest(item=item.name):
                self.assertNotIn(surfaces.OPEN, item.actions)

    def test_one_thing_is_not_a_shortlist(self):
        self.assertFalse(surfaces.from_candidates(MONITORS[:1]))

    def test_nothing_is_not_a_shortlist(self):
        self.assertFalse(surfaces.from_candidates(()))

    def test_an_unknown_kind_builds_nothing(self):
        self.assertFalse(
            surfaces.from_candidates(MONITORS, kind="carousel"),
        )


class ACardShowsTheNameNotThePageTitleTests(unittest.TestCase):
    """A hundred-pixel card has room for a name and nothing else.

    Measured live in the 4F.7 run: two floating cards were labelled
    "INDUSTRIE HOTEL - Prices & Reviews (Busan, South Korea)" and "Hotel
    Hongdan, Busan (updated prices 2026)". Both are page titles. Neither is
    what the person asked to see.
    """

    def test_listing_boilerplate_is_dropped(self):
        for title, wanted in (
            ("INDUSTRIE HOTEL - Prices & Reviews (Busan, South Korea)",
             "INDUSTRIE HOTEL"),
            ("Hotel Hongdan, Busan (updated prices 2026)",
             "Hotel Hongdan, Busan"),
            ("Travelodge Dongdaemun Seoul - Booking.com",
             "Travelodge Dongdaemun Seoul"),
            ("LG UltraGear 27GP850-B | Official Site",
             "LG UltraGear 27GP850-B"),
        ):
            with self.subTest(title=title):
                self.assertEqual(surfaces._card_name(title), wanted)

    def test_a_real_name_survives_whole(self):
        for name in (
            "Lotte Hotel World",
            "Hotel Skypark Myeongdong",
            "GLAD Gangnam COEX Center",
            # The dash is part of what the thing is called, not a title
            # separator with boilerplate behind it.
            "Dell S2722DGM - 27 inch Curved Gaming Monitor",
        ):
            with self.subTest(name=name):
                self.assertEqual(surfaces._card_name(name), name)

    def test_a_title_that_is_boilerplate_throughout_is_left_alone(self):
        # Rewriting it would invent a name. That it is a bad candidate is a
        # problem for the layer that chose it.
        self.assertEqual(
            surfaces._card_name("Best Hotels in Seoul 2026 | Top 10"),
            "Best Hotels in Seoul 2026 | Top 10",
        )

    def test_the_tidied_name_is_what_reaches_the_card(self):
        surface = surfaces.from_candidates((
            rs.Candidate(name="INDUSTRIE HOTEL - Prices & Reviews (Busan)",
                         url="https://example.com/a"),
            rs.Candidate(name="Lotte Hotel World",
                         url="https://example.com/b"),
        ))

        self.assertEqual(
            [item.name for item in surface.items],
            ["INDUSTRIE HOTEL", "Lotte Hotel World"],
        )

    def test_identity_does_not_move_when_the_label_is_tidied(self):
        # 4E's lesson, and the reason a card carries an id at all: the
        # label is what it says, the identity is what it is.
        messy = rs.Candidate(name="INDUSTRIE HOTEL - Prices & Reviews",
                             url="https://example.com/a")
        surface = surfaces.from_candidates((
            messy, rs.Candidate(name="Lotte", url="https://example.com/b"),
        ))

        self.assertEqual(surface.items[0].id, messy.id)


class ACardShowsAThingNotAPageTests(unittest.TestCase):
    """A photograph of "The 10 best guest houses in Busan" is not a hotel.

    It is whatever picture that article leads with, and a wall of those
    floating around Elaina is worse than no cards at all. So a candidate
    that names a *page about several things* is refused a card however well
    it scored -- and scoring is exactly why this cannot be left to the fit
    layer. Measured live, all of these came back FITS alongside four real
    hotels:

        FITS  'The 10 best accommodation in Busan, South Korea | Booking.com'
        FITS  'The 10 best guest houses in Busan, South Korea | Booking.com'
        FITS  'Romantic Getaways Busan: Find AU$40 Romantic... | lastminute'
    """

    THINGS = (
        "Lotte Hotel World",
        "Hotel Skypark Myeongdong",
        "INDUSTRIE HOTEL",
        "Hotel Entra Gangnam (Seoul)",
        "IBIS AMBASSADOR SEOUL MYEONGDONG",
        "Borjomi Seoul Boutique Hotel in Seoul, South Korea",
        "Dell S2722DGM Curved Gaming Monitor",
        "LG UltraGear 27GP850-B",
        "Samsung Odyssey G5",
        "banana",
        # A qualifier on a *singular* thing is part of its name. Both of
        # these are real brands and must survive the listing rule.
        "Budget Inn Seoul",
        "Luxury Collection Hotel Seoul",
        # Singular there, so it is a place and not a list of places.
        "Borjomi Seoul Boutique Hotel in Seoul",
        "Busan Tourist Hotel in Busan",
    )

    PAGES = (
        "The 10 best accommodation in Busan, South Korea",
        "The 49 best hotels in Seoul",
        "The 10 best Places To Stay in Busan",
        "5 Best Korean Monitors in 2026",
        "Best Hotels in Gangnam, Seoul, South Korea",
        "The best hotels in Gangnam-Gu, Seoul",
        "How to Choose a Monitor in Korea",
        "Honest Advice for Where to Stay in Seoul for First-Timers",
        "Booking.com | Official site",
        "Tripadvisor: Over a billion reviews & contributions",
        "Hotels: Find Cheap Hotel Deals & Discounts",
        "Budget Hotels: Find the best cheap Hotels Near me",
        "makemytrip.com/hotels/hotels-near-me",
        "Gaming Monitors - Newegg.com",
        # A qualifier on a plural category is a listing of them.
        "Cheap HOTELS in Jungmun, South Korea",
        "Budget Hotels in Seoul",
        "Discount Monitors",
        # A title written in the first person is somebody's article.
        "I asked my most stylish friends where they stay in Seoul",
        "We tried every hotel in Hongdae",
        "Here's where to stay in Busan",
        # A headline is a sentence about a thing, not the thing.
        "Aman Seoul to Bring Ultra-Luxury Hospitality to South Korea",
        "Four Seasons announces a new Seoul property",
        "11-Day South Korea Itinerary: Seoul, Jeju Island & Busan Tour",
        # A brand's shopfront is not one of its products.
        "Keychron | Custom Mechanical Keyboards for Mac, Windows and Linux",
        "Redragon Official Store | Mechanical Gaming Keyboards, Mice",
        "Custom Mechanical Keyboards for Mac",
        "Hotels in Seoul",
    )

    def test_a_specific_thing_may_have_a_card(self):
        for name in self.THINGS:
            with self.subTest(name=name):
                self.assertTrue(surfaces.names_a_specific_thing(name))

    def test_a_page_about_several_things_may_not(self):
        for name in self.PAGES:
            with self.subTest(name=name):
                self.assertFalse(surfaces.names_a_specific_thing(name))

    def test_the_refusal_says_why(self):
        candidates = (rs.Candidate(name="The 49 best hotels in Seoul"),)

        (name, reason), = surfaces.refusals(candidates)

        self.assertEqual(name, "The 49 best hotels in Seoul")
        self.assertTrue(reason)

    def test_pages_never_reach_a_card(self):
        surface = surfaces.from_candidates((
            rs.Candidate(name="The 49 best hotels in Seoul",
                         url="https://mag.example.com/49"),
            rs.Candidate(name="Lotte Hotel World",
                         url="https://lottehotel.com/world"),
            rs.Candidate(name="Booking.com | Official site",
                         url="https://booking.com"),
            rs.Candidate(name="Hotel Skypark Myeongdong",
                         url="https://skypark.example.com"),
        ))

        self.assertEqual(
            [item.name for item in surface.items],
            ["Lotte Hotel World", "Hotel Skypark Myeongdong"],
        )

    def test_the_filter_runs_before_the_limit(self):
        # Otherwise six roundup articles use up the room the real results
        # were going to take, and a good candidate in seventh place is lost.
        junk = [
            rs.Candidate(name=f"The 10 best hotels number {index}",
                         url=f"https://mag.example.com/{index}")
            for index in range(surfaces.MAX_ITEMS)
        ]
        real = [
            rs.Candidate(name="Lotte Hotel World", url="https://a.example.com"),
            rs.Candidate(name="THE MAY HOTEL", url="https://b.example.com"),
        ]

        surface = surfaces.from_candidates(junk + real)

        self.assertEqual(
            [item.name for item in surface.items],
            ["Lotte Hotel World", "THE MAY HOTEL"],
        )

    def test_nothing_specific_means_no_surface_at_all(self):
        # Better an empty window than a wall of magazine cover photographs.
        self.assertFalse(surfaces.from_candidates((
            rs.Candidate(name="The 49 best hotels in Seoul", url="https://a.b"),
            rs.Candidate(name="Top 10 Places To Stay", url="https://c.d"),
            rs.Candidate(name="How to Choose a Hotel", url="https://e.f"),
        )))

    def test_boilerplate_is_stripped_before_the_rule_judges_it(self):
        # "THE MAY HOTEL - Updated 2026 Prices & Reviews (Seoul, South
        # Korea)" is a real hotel wearing a page title. Judging the raw
        # string would refuse it for the year in the boilerplate.
        for raw, wanted in (
            ("THE MAY HOTEL - Updated 2026 Prices & Reviews (Seoul)",
             "THE MAY HOTEL"),
            ("Aloft Seoul Gangnam Reviews: 229 Verified Reviews",
             "Aloft Seoul Gangnam"),
            ("Busan Tourist Hotel in Busan, South Korea from £29: Deals",
             "Busan Tourist Hotel in Busan, South Korea"),
            # The site that listed it is not part of its name.
            ("Mon Oncle a Seoul, Seoul | HotelsCombined",
             "Mon Oncle a Seoul, Seoul"),
        ):
            with self.subTest(raw=raw):
                surface = surfaces.from_candidates((
                    rs.Candidate(name=raw, url="https://a.example.com"),
                    rs.Candidate(name="Lotte Hotel World",
                                 url="https://b.example.com"),
                ))
                self.assertEqual(surface.items[0].name, wanted)


class ACardIsOneOfThemNotTheCategoryTests(unittest.TestCase):
    """"Seoul" is not a hotel in Seoul, and its photo is a skyline.

    Measured live, answering *find good hotels in Seoul*:

        FITS  'Seoul'
        FITS  'Seoul Hotels with Reviews & Address'

    Both name the category being searched for rather than one of the things
    in it. Neither is caught by any pattern, because there is nothing wrong
    with the words -- what is wrong is that they only say back what was
    asked. So the request is what they are judged against.
    """

    HOTELS = "find good hotels in Seoul"

    def test_a_name_that_only_repeats_the_request_is_refused(self):
        for name in (
            "Seoul",
            "Seoul Hotels with Reviews & Address",
            "Hotels in Seoul",
            "Good Hotels",
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    surfaces.names_a_specific_thing(name, self.HOTELS),
                )

    def test_one_distinguishing_word_is_enough(self):
        for name in (
            "THE MAY HOTEL",
            "IBIS AMBASSADOR SEOUL MYEONGDONG",
            "Hotel Thomas Myeongdong (Seoul)",
            "Lotte Hotel World",
            "Borjomi Seoul Boutique Hotel in Seoul, South Korea",
        ):
            with self.subTest(name=name):
                self.assertTrue(
                    surfaces.names_a_specific_thing(name, self.HOTELS),
                )

    def test_a_plural_and_its_singular_are_the_same_word(self):
        # "hotels" in the request must cancel "hotel" in the name, or every
        # real hotel keeps a free distinguishing word it did not earn.
        self.assertFalse(
            surfaces.names_a_specific_thing("The Hotel", "find a good hotel"),
        )

    def test_it_works_for_things_as_well_as_places(self):
        monitors = "a good gaming monitor"

        self.assertFalse(surfaces.names_a_specific_thing("Gaming Monitors", monitors))
        self.assertFalse(surfaces.names_a_specific_thing("Monitors", monitors))
        self.assertTrue(surfaces.names_a_specific_thing(
            "Dell S2722DGM Curved Gaming Monitor", monitors,
        ))
        self.assertTrue(surfaces.names_a_specific_thing(
            "LG UltraGear 27GP850-B", monitors,
        ))

    def test_a_korean_name_is_a_name(self):
        # Measured live: an ASCII-only word pattern found no words at all in
        # a Korean result, so it looked like it added nothing to the request
        # and was refused. Every Korean-named place would have been.
        for name in ("롯데호텔 월드", "호텔 스카이파크 명동", "네이버지도"):
            with self.subTest(name=name):
                self.assertTrue(
                    surfaces.names_a_specific_thing(name, self.HOTELS),
                )

    def test_a_korean_request_still_cancels_its_own_words(self):
        self.assertFalse(
            surfaces.names_a_specific_thing("서울 호텔", "서울 호텔 찾아줘"),
        )

    def test_with_no_request_to_compare_against_it_stands_aside(self):
        # Guessing without a subject would refuse perfectly good cards.
        self.assertTrue(surfaces.names_a_specific_thing("Seoul"))

    def test_the_category_never_reaches_a_card(self):
        surface = surfaces.from_candidates(
            (
                rs.Candidate(name="Seoul", url="https://a.example.com"),
                rs.Candidate(name="THE MAY HOTEL", url="https://b.example.com"),
                rs.Candidate(name="Seoul Hotels with Reviews",
                             url="https://c.example.com"),
                rs.Candidate(name="Lotte Hotel World",
                             url="https://d.example.com"),
            ),
            subject=self.HOTELS,
        )

        self.assertEqual(
            [item.name for item in surface.items],
            ["THE MAY HOTEL", "Lotte Hotel World"],
        )


class TheWireContractTests(unittest.TestCase):
    """Serialised, sent, and read back without losing or gaining anything."""

    def test_a_surface_survives_a_round_trip(self):
        surface = surfaces.from_candidates(MONITORS, title="Monitors")

        self.assertEqual(surfaces.validate(surface.payload()), surface)

    def test_it_survives_actual_json(self):
        surface = surfaces.from_candidates(MONITORS, title="Monitors")

        wire = json.loads(json.dumps(surface.payload()))

        self.assertEqual(surfaces.validate(wire), surface)

    def test_the_websocket_carries_the_event(self):
        self.assertIn("assistant_surface", WebSocketServer(
            __import__("core.event_bus", fromlist=["EventBus"]).EventBus(),
        )._event_names)


class MalformedPayloadsFailSafelyTests(unittest.TestCase):
    """Every failure is a plain-text reply, never a broken window."""

    def test_nothing_recognisable_is_dropped(self):
        for payload, why in (
            (None, "not a dict"),
            ("shortlist", "not a dict"),
            ({}, "no type"),
            ({"type": "shortlist"}, "no items"),
            ({"type": "shortlist", "items": "two"}, "items is not a list"),
            ({"type": "carousel", "items": []}, "a type nobody implements"),
            ({"type": "none", "items": []}, "none is not a surface"),
        ):
            with self.subTest(why=why):
                self.assertFalse(surfaces.validate(payload))

    def test_an_item_with_no_identity_is_dropped(self):
        surface = surfaces.validate({
            "type": "shortlist",
            "items": [
                {"name": "No id here"},
                {"id": "a", "name": "Real one"},
                {"id": "b", "name": "Other one"},
            ],
        })

        self.assertEqual(len(surface.items), 2)

    def test_markup_never_reaches_a_card(self):
        # Dropped rather than escaped: escaping implies the value was meant
        # to be there, and nothing that reaches this is allowed to be code.
        surface = surfaces.validate({
            "type": "shortlist",
            "items": [
                {"id": "a", "name": "<img src=x onerror=alert(1)>"},
                {"id": "b", "name": "Fine", "description": "<script>x</script>"},
                {"id": "c", "name": "Also fine"},
            ],
        })

        self.assertEqual([item.name for item in surface.items],
                         ["Fine", "Also fine"])
        self.assertEqual(surface.items[0].description, "")

    def test_only_http_addresses_survive(self):
        surface = surfaces.validate({
            "type": "shortlist",
            "items": [
                {"id": "a", "name": "A", "url": "javascript:alert(1)"},
                {"id": "b", "name": "B", "url": "file:///etc/passwd"},
                {"id": "c", "name": "C", "url": "https://example.com/ok"},
            ],
        })

        self.assertEqual(
            [item.url for item in surface.items],
            ["", "", "https://example.com/ok"],
        )

    def test_an_action_nobody_implements_is_dropped(self):
        surface = surfaces.validate({
            "type": "shortlist",
            "items": [
                {"id": "a", "name": "A", "actions": ["open", "rm -rf", "buy"]},
                {"id": "b", "name": "B", "actions": ["compare"]},
            ],
        })

        self.assertEqual(surface.items[0].actions, ("open",))

    def test_a_surface_is_bounded(self):
        surface = surfaces.validate({
            "type": "shortlist",
            "items": [
                {"id": str(index), "name": f"Item {index}"}
                for index in range(30)
            ],
        })

        self.assertLessEqual(len(surface.items), surfaces.MAX_ITEMS)


class TheBackendChoosesTests(unittest.TestCase):
    """And it is conservative: `none` is the common case."""

    def test_a_follow_up_on_a_result_set_gets_a_shortlist(self):
        engine, quiet = _engine_holding_results()
        seen = []
        engine.events.subscribe("assistant_surface", lambda e: seen.append(e.data))

        with contextlib.redirect_stdout(quiet):
            engine.chat("which one would you choose?")

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["type"], "shortlist")
        self.assertEqual(len(seen[0]["items"]), 3)

    def test_ordinary_conversation_gets_nothing(self):
        engine, quiet = _engine_holding_results()
        seen = []
        engine.events.subscribe("assistant_surface", lambda e: seen.append(e.data))
        engine.client.reply = "Sure."

        with contextlib.redirect_stdout(quiet):
            engine.chat("thanks")

        self.assertEqual(seen, [])

    def test_a_comparison_is_asked_for_rather_than_guessed(self):
        engine, quiet = _engine_holding_results()
        seen = []
        engine.events.subscribe("assistant_surface", lambda e: seen.append(e.data))

        with contextlib.redirect_stdout(quiet):
            engine.chat("compare them")

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["type"], "comparison")

    def test_no_results_means_no_surface(self):
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            engine = build_engine(routes={})
            engine.client.reply = "Paris."
            seen = []
            engine.events.subscribe(
                "assistant_surface", lambda e: seen.append(e.data),
            )
            engine.chat("what's the capital of France?")

        self.assertEqual(seen, [])

    def test_the_text_reply_is_unchanged_either_way(self):
        engine, quiet = _engine_holding_results()

        with contextlib.redirect_stdout(quiet):
            reply = engine.chat("which one would you choose?")

        self.assertIn("LG UltraGear", reply)


class OpeningACardIsNotATurnTests(unittest.TestCase):
    """Following a link is not a decision, so it does not become one.

    Electron opens the page itself -- routing a click through the router
    would have Elaina narrate it, and would fail outright whenever browser
    control is unavailable. What the backend still has to know is *which*
    card, or the window and the conversation drift apart.
    """

    def test_it_resolves_by_identity(self):
        engine, quiet = _engine_holding_results()

        with contextlib.redirect_stdout(quiet):
            named = engine.surface_opened(MONITORS[1].id)

        self.assertEqual(named, "LG UltraGear 27GP850-B")

    def test_it_runs_no_turn_and_touches_nothing(self):
        engine, quiet = _engine_holding_results()
        said = []
        engine.events.subscribe("assistant_finished", lambda e: said.append(e))

        with contextlib.redirect_stdout(quiet):
            engine.surface_opened(MONITORS[0].id)

        self.assertEqual(said, [])
        self.assertEqual(machine_actions(engine), [])

    def test_a_stale_card_names_nothing(self):
        engine, quiet = _engine_holding_results()

        with contextlib.redirect_stdout(quiet):
            named = engine.surface_opened("deadbeef00")

        self.assertEqual(named, "")

    def test_an_empty_identity_names_nothing(self):
        engine, quiet = _engine_holding_results()

        with contextlib.redirect_stdout(quiet):
            self.assertEqual(engine.surface_opened(""), "")


class ACardPressIsAnOrdinaryTurnTests(unittest.TestCase):
    """It goes through the router and the guards, like anything spoken."""

    def test_open_opens_that_candidate(self):
        engine, quiet = _engine_holding_results()

        with contextlib.redirect_stdout(quiet):
            engine.surface_action("open", MONITORS[1].id)

        self.assertEqual(
            [t for k, t in machine_actions(engine) if k.startswith("open_url")],
            ["https://bestbuy.com/site/lg/6467884.p"],
        )

    def test_a_card_for_something_no_longer_held_does_nothing(self):
        # A stale card must not open something the conversation has moved
        # on from. Identity is what makes that checkable.
        engine, quiet = _engine_holding_results()

        with contextlib.redirect_stdout(quiet):
            said = engine.surface_action("open", "deadbeef00")

        self.assertEqual(said, "")
        self.assertEqual(machine_actions(engine), [])

    def test_an_action_nobody_implements_does_nothing(self):
        engine, quiet = _engine_holding_results()

        with contextlib.redirect_stdout(quiet):
            said = engine.surface_action("rm -rf", MONITORS[0].id)

        self.assertEqual(said, "")
        self.assertEqual(machine_actions(engine), [])

    def test_open_on_something_with_nowhere_to_go_does_nothing(self):
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            engine = build_engine(routes={})
            engine.task_sessions.note_recommendation_turn(
                "somewhere to eat", subject="dinner",
            )
            nameless = rs.Candidate(name="Juk Story Myeongdong")
            engine.task_sessions.record_candidates(
                [nameless, rs.Candidate(name="Han River BBQ")],
                evidence=("shortlist",),
            )
            said = engine.surface_action("open", nameless.id)

        self.assertEqual(said, "")


if __name__ == "__main__":
    unittest.main()
