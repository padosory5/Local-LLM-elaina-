import unittest

from tools.computer_control.session_action_memory import SessionActionMemory


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.memory = SessionActionMemory(clock=self.clock)

    def test_a_recorded_action_is_retrievable(self):
        self.memory.record(
            app="Spotify", family="text_input", subject="Blinding Lights",
            window_title="Spotify Premium",
        )
        last = self.memory.last_action()
        self.assertEqual(last.app, "Spotify")
        self.assertEqual(last.subject, "Blinding Lights")

    def test_an_action_without_an_app_is_not_recorded(self):
        self.assertIsNone(
            self.memory.record(app="", family="activation", subject="x")
        )
        self.assertIsNone(self.memory.last_action())

    def test_an_action_without_a_family_is_not_recorded(self):
        self.assertIsNone(self.memory.record(app="Spotify", family=""))

    def test_window_title_stands_in_for_a_missing_app_name(self):
        self.memory.record(
            app="", family="activation", window_title="Spotify Premium",
        )
        self.assertEqual(self.memory.last_action().app, "Spotify Premium")


class FollowUpResolutionTests(unittest.TestCase):
    """What makes "stop it" target the right thing."""

    def setUp(self):
        self.clock = _Clock()
        self.memory = SessionActionMemory(clock=self.clock)

    def test_stop_it_resolves_to_the_thing_that_was_played(self):
        self.memory.record(
            app="Spotify", family="text_input", subject="Blinding Lights",
        )
        self.clock.advance(2)
        # The Play click names the control, not the track.
        self.memory.record(
            app="Spotify", family="activation", subject="", control_name="Play",
        )
        subject = self.memory.last_subject()
        self.assertEqual(subject.subject, "Blinding Lights")
        self.assertEqual(subject.app, "Spotify")

    def test_a_later_subjectless_action_does_not_hide_the_track(self):
        self.memory.record(app="Spotify", family="text_input", subject="Song A")
        self.clock.advance(1)
        self.memory.record(app="Spotify", family="scroll", subject="")
        self.assertEqual(self.memory.last_subject().subject, "Song A")

    def test_a_newer_subject_wins(self):
        self.memory.record(app="Spotify", family="text_input", subject="Song A")
        self.clock.advance(1)
        self.memory.record(app="Spotify", family="text_input", subject="Song B")
        self.assertEqual(self.memory.last_subject().subject, "Song B")

    def test_actions_can_be_scoped_to_one_app(self):
        self.memory.record(app="Spotify", family="text_input", subject="Song A")
        self.clock.advance(1)
        self.memory.record(app="Notepad", family="text_input", subject="a memo")
        self.assertEqual(
            self.memory.last_subject(app="Spotify").subject, "Song A",
        )
        self.assertEqual(self.memory.last_subject().subject, "a memo")

    def test_stale_actions_stop_being_offered_as_targets(self):
        # "Stop it" half an hour later almost certainly means something else.
        self.memory.record(app="Spotify", family="text_input", subject="Song A")
        self.clock.advance(3600)
        self.assertIsNone(self.memory.last_subject())
        self.assertEqual(self.memory.recent(), ())


class BoundsTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.memory = SessionActionMemory(clock=self.clock)

    def test_history_per_app_is_bounded(self):
        for index in range(10):
            self.clock.advance(1)
            self.memory.record(
                app="Spotify", family="text_input", subject=f"song {index}",
            )
        remembered = self.memory.recent(app="Spotify")
        self.assertLessEqual(len(remembered), 4)
        # The most recent survive, not the oldest.
        self.assertEqual(remembered[-1].subject, "song 9")

    def test_many_apps_are_bounded_overall(self):
        for index in range(20):
            self.clock.advance(1)
            self.memory.record(
                app=f"App{index}", family="launch", subject=f"s{index}",
            )
        self.assertLessEqual(len(self.memory.recent()), 12)


class ContextShapeTests(unittest.TestCase):
    def test_context_matches_the_shape_the_engine_publishes(self):
        memory = SessionActionMemory()
        memory.record(
            app="Spotify", family="text_input", subject="Song A",
            window_title="Spotify Premium", window_handle=42,
        )
        context = memory.recent_context()
        self.assertEqual(len(context), 1)
        self.assertEqual(
            set(context[0]), {"app", "action", "subject", "window", "handle"},
        )
        self.assertEqual(context[0]["handle"], 42)

    def test_the_window_handle_survives_a_retitle(self):
        # Spotify renames its window to the playing track, so the title
        # recorded at play time names nothing later. The handle still does.
        memory = SessionActionMemory()
        memory.record(
            app="Spotify Premium", family="activation", subject="Song A",
            window_title="Spotify Premium", window_handle=99,
        )
        self.assertEqual(memory.last_subject().window_handle, 99)

    def test_clear_empties_the_memory(self):
        memory = SessionActionMemory()
        memory.record(app="Spotify", family="launch")
        memory.clear()
        self.assertEqual(memory.recent_context(), ())


if __name__ == "__main__":
    unittest.main()
