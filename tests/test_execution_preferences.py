from __future__ import annotations

import io
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from brain import acquisition, preferences, recommendation_state
from brain.chat_engine import ChatEngine
from brain.deliberation import front_door
from brain.deliberation.clarification import decide
from brain.deliberation.goal import SOURCE_UTTERANCE, Goal, Slot
from brain.deliberation.pending import ClarificationGate
from brain.deliberation.profile import (
    SOURCE_FOR,
    STATED,
    TOOL_FOR,
    UserProfile,
    context_key,
)
from brain.recommendation_state import RecommendationProblem
from brain.skills.media import skill_for
from brain.task_session import TaskSessionStore
from tests.turn_harness import build_engine


def _profile() -> UserProfile:
    directory = tempfile.mkdtemp(prefix="elaina-execution-preference-")
    return UserProfile(path=Path(directory) / "profile.json")


class ToolExecutionPreferenceTests(unittest.TestCase):

    def test_saved_spotify_turns_an_app_less_title_into_a_spotify_goal(self):
        profile = _profile()
        profile.observe(
            TOOL_FOR, "Spotify", key=context_key("music"), source=STATED,
        )
        resolved = preferences.resolve(profile, TOOL_FOR, "music", default="Spotify")

        route = front_door.read(
            "Play Blinding Lights.",
            profile=profile,
            media_application=resolved.choice,
        )

        self.assertIsNotNone(route)
        self.assertEqual(route.operation, "ui_action")
        self.assertEqual(route.goal.kind, "play_track")
        self.assertEqual(route.goal.value("title"), "Blinding Lights")
        self.assertEqual(route.goal.value("provider"), "Spotify")

    def test_one_task_provider_survives_the_song_question(self):
        profile = _profile()
        profile.observe(
            TOOL_FOR, "Spotify", key=context_key("music"), source=STATED,
        )
        override = preferences.resolve(
            profile, TOOL_FOR, "music", override="YouTube Music",
            default="Spotify",
        )
        route = front_door.read(
            "Use YouTube Music for this one and play another song.",
            profile=profile,
            media_application=override.choice,
        )
        self.assertTrue(route.asks)

        pending = ClarificationGate().offer(
            goal=route.decision.goal,
            slot=route.decision.missing,
            question=route.question,
            template=route.decision.template,
        )
        completed = pending.completed("Levitating by Dua Lipa")

        self.assertEqual(completed.value("provider"), "YouTube Music")
        self.assertEqual(completed.value("title"), "Levitating")
        self.assertEqual(completed.value("artist"), "Dua Lipa")
        self.assertEqual(
            preferences.resolve(profile, TOOL_FOR, "music").choice,
            "Spotify",
        )

    def test_exact_track_skill_targets_the_resolved_provider(self):
        goal = Goal(
            kind="play_track",
            utterance="Play Levitating in YouTube Music",
            slots={
                "title": Slot("title", "Levitating"),
                "provider": Slot("provider", "YouTube Music"),
            },
        )
        asked_for = []

        class Surface:
            can_activate = True

            def window(self, application):
                asked_for.append(application)
                return None

        result = skill_for(goal).run(goal, Surface())

        self.assertEqual(asked_for, ["YouTube Music"])
        self.assertTrue(result.handed_back)

    def test_play_chess_is_not_claimed_by_a_music_default(self):
        route = front_door.read("play chess", media_application="Spotify")
        self.assertIsNone(route)


class WholeTurnToolExecutionTests(unittest.TestCase):

    def setUp(self):
        self.engine = build_engine()

    def tearDown(self):
        self.engine.close()

    def test_saved_provider_executes_override_is_scoped_and_default_returns(self):
        observer = self.engine.desktop_action_planner.observer
        observer.catalogue += (("Blinding Lights", "The Weeknd"),)
        requested_apps = []
        original_find = observer.find_window

        def find_window(application):
            requested_apps.append(str(application))
            return original_find(application)

        observer.find_window = find_window

        self.engine.chat(
            "From now on use Spotify whenever I ask you to play music."
        )
        first = self.engine.chat("Play Blinding Lights.")
        question = self.engine.chat(
            "Use YouTube Music for this one and play another song."
        )
        override = self.engine.chat("After LIKE by IVE")
        final = self.engine.chat("Play Bang Bang by IVE.")

        self.assertIn("Playing Blinding Lights", first)
        self.assertIn("Which song", question)
        self.assertIn("Playing After LIKE", override)
        self.assertIn("Playing Bang Bang", final)
        self.assertIn("Spotify", requested_apps)
        self.assertIn("YouTube Music", requested_apps)
        self.assertEqual(requested_apps[-1], "Spotify")
        self.assertEqual(
            preferences.resolve(self.engine.user_profile, TOOL_FOR, "music").choice,
            "Spotify",
        )


class SourceSurfaceSelectionTests(unittest.TestCase):

    def test_selected_source_allows_only_same_site_redirects(self):
        self.assertTrue(acquisition.same_site_host(
            "m.place.example.com", "maps.example.com",
        ))
        self.assertTrue(acquisition.same_site_host(
            "booking.example.co.kr", "maps.example.co.kr",
        ))
        self.assertFalse(acquisition.same_site_host(
            "maps.attacker.com", "maps.example.com",
        ))

    def test_only_pages_from_the_selected_surface_are_kept(self):
        selected = acquisition.select_surface_results(
            [
                {
                    "title": "Mapo Korean BBQ - Naver Maps",
                    "url": "https://map.naver.com/p/entry/place/123456",
                    "summary": "Seoul address, 4.7 rating and reviews",
                },
                {
                    "title": "A Yelp restaurant",
                    "url": "https://www.yelp.com/biz/example",
                    "summary": "unrelated surface",
                },
            ],
            "Naver Maps",
            known_hosts=("naver.com", "yelp.com"),
        )

        self.assertTrue(selected.applied)
        self.assertEqual(selected.selected, "Naver Maps")
        self.assertEqual(len(selected.results), 1)
        self.assertIn("map.naver.com", selected.results[0]["url"])

    def test_an_unattributable_source_falls_back_without_fake_success(self):
        selected = acquisition.select_surface_results(
            [{
                "title": "A Yelp restaurant",
                "url": "https://www.yelp.com/biz/example",
                "summary": "address and reviews",
            }],
            "Naver Maps",
            known_hosts=("naver.com", "yelp.com"),
        )

        self.assertFalse(selected.applied)
        self.assertEqual(selected.results, ())
        self.assertEqual(selected.fallback, "ordinary acquisition")


class RecommendationAcquisitionIntegrationTests(unittest.TestCase):

    class Locale:
        def sites_for_goal(self, category, goal):
            return ("Naver Maps", "Diningcode"), "South Korea"

        def source_hosts_for_goal(self, category, goal):
            return ("naver.com", "diningcode.com")

    class Research:
        def __init__(self, batches):
            self.batches = list(batches)
            self.queries = []

        def research_structured(self, *, search_query, max_results):
            self.queries.append(search_query)
            return tuple(self.batches.pop(0))

    @staticmethod
    def _problem(source_override=""):
        return RecommendationProblem(
            subject="Korean BBQ restaurants",
            domain="restaurant",
            category="restaurant",
            source_override=source_override,
            constraints=(
                Slot(recommendation_state.AREA, "Seoul", SOURCE_UTTERANCE),
            ),
            expires_at=time.monotonic() + 600,
        )

    def _engine(self, batches, *, source="Naver Maps"):
        engine = ChatEngine.__new__(ChatEngine)
        engine.user_profile = _profile()
        engine.user_profile.observe(
            SOURCE_FOR, source,
            key=context_key("restaurant"), source=STATED,
        )
        engine.user_locale = self.Locale()
        engine.task_sessions = TaskSessionStore()
        engine.task_sessions._problem = self._problem()
        engine._source_override = ""
        engine.research_agent = self.Research(batches)
        engine.client = None
        engine.model = "test"
        return engine

    @staticmethod
    def _naver_candidates():
        return [
            {
                "title": "Mapo Korean BBQ - Naver Maps",
                "url": "https://map.naver.com/p/entry/place/123456",
                "summary": "Seoul address, 4.7 rating and 320 reviews",
            },
            {
                "title": "Gangnam Korean BBQ - Naver Maps",
                "url": "https://map.naver.com/p/entry/place/987654",
                "summary": "Seoul address, 4.6 rating and 210 reviews",
            },
            {
                "title": "Yelp result that must not leak",
                "url": "https://www.yelp.com/biz/not-selected",
                "summary": "Seoul address and reviews",
            },
        ]

    def test_saved_source_drives_acquisition_and_returns_concrete_candidates(self):
        engine = self._engine([self._naver_candidates()])
        resolution = engine._source_resolution(
            engine.task_sessions.active_recommendation(),
            "Korean BBQ restaurants in Seoul",
        )

        result = engine._research_for_recommendation(
            "Korean BBQ restaurants in Seoul", resolution=resolution,
        )

        self.assertIn("Naver Maps", engine.research_agent.queries[0])
        self.assertIn("Mapo Korean BBQ", result.evidence)
        self.assertIn("Gangnam Korean BBQ", result.evidence)
        self.assertNotIn("Yelp result", result.evidence)
        self.assertNotIn("[SOURCE] Mapo Korean BBQ", result.evidence)

    def test_incompatible_saved_source_uses_logged_generic_fallback(self):
        yelp = [{
            "title": "A Yelp restaurant",
            "url": "https://www.yelp.com/biz/example",
            "summary": "Seoul address, 4.5 rating and reviews",
        }]
        # preferred attempt, selected-host attempt, then honest generic fallback
        engine = self._engine([yelp, yelp, yelp])
        resolution = engine._source_resolution(
            engine.task_sessions.active_recommendation(),
            "Korean BBQ restaurants in Seoul",
        )

        output = io.StringIO()
        with redirect_stdout(output):
            result = engine._research_for_recommendation(
                "Korean BBQ restaurants in Seoul", resolution=resolution,
            )

        self.assertIsNotNone(result)
        self.assertIn("Fallback: ordinary acquisition", output.getvalue())
        self.assertIn("A Yelp restaurant", result.evidence)

    def test_source_surface_escalates_to_live_browser_and_extracts_named_entities(self):
        engine = self._engine([[
            {
                "title": "A generic indexed result",
                "url": "https://www.yelp.com/biz/example",
                "summary": "The index did not expose the preferred source",
            },
        ]])
        engine.computer_control_mode = SimpleNamespace(enabled=True)
        engine.browser_page_control_enabled = True
        engine._web_search_enabled = True
        engine.screen_monitor = SimpleNamespace(enabled=True)
        engine.project_mcp = object()
        engine.browser_driver = "dom"
        calls = []

        class Planner:
            def act(self, goal, **kwargs):
                calls.append((goal, kwargs))
                return SimpleNamespace(status="done", failure_code="")

        page = (
            "Damongjip Sinnonhyeon, Seoul Gangnam-gu, rating 4.77, "
            "8,729 reviews. Gogi-gun Kim Chun-bae Gangnam, Seoul "
            "Gangnam-gu, rating 4.75, 6,004 reviews."
        )
        engine.browser_action_planner = Planner()
        engine.browser_observer = SimpleNamespace(
            read_text=lambda _tab: SimpleNamespace(
                status="observed", text=page,
                url="https://map.naver.com/p/search/korean-bbq",
            ),
        )
        engine.task_extractor = SimpleNamespace(
            extract=lambda _text, **_kwargs: (
                SimpleNamespace(
                    name="Damongjip Sinnonhyeon",
                    attributes={"address": "Seoul Gangnam-gu", "rating": "4.77"},
                ),
                SimpleNamespace(
                    name="Gogi-gun Kim Chun-bae Gangnam",
                    attributes={"address": "Seoul Gangnam-gu", "rating": "4.75"},
                ),
                # Extraction is not trusted unless the name is also present
                # in the live page text.
                SimpleNamespace(
                    name="Invented Restaurant",
                    attributes={"rating": "5.0"},
                ),
            ),
        )

        fits = engine._candidates_for(
            "Korean BBQ restaurants in Seoul Naver Maps",
            engine.task_sessions.active_recommendation(),
            "place",
            preferred_source="Naver Maps",
        )

        names = [fit.name for fit in fits]
        self.assertIn("Damongjip Sinnonhyeon", names)
        self.assertIn("Gogi-gun Kim Chun-bae Gangnam", names)
        self.assertNotIn("Invented Restaurant", names)
        self.assertEqual(calls[0][1]["allowed_hosts"], ("naver.com",))
        self.assertEqual(calls[0][1]["source_names"], ("Naver Maps",))


class TaskScopedSourceOverrideTests(unittest.TestCase):

    def test_override_survives_clarification_and_expires_on_new_task(self):
        store = TaskSessionStore()
        store._problem = RecommendationProblem(
            subject="sushi restaurants",
            domain="restaurant",
            category="restaurant",
            expires_at=time.monotonic() + 600,
        )
        self.assertTrue(store.note_source_override("Google Maps"))

        store.note_recommendation_turn("Gangnam", subject="sushi restaurants")
        self.assertEqual(store.source_override(), "Google Maps")

        store.note_recommendation_turn(
            "Find an electric guitar", subject="electric guitar", topic_shift=True,
        )
        self.assertEqual(store.source_override(), "")


if __name__ == "__main__":
    unittest.main()
