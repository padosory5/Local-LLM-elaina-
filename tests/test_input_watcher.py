import unittest

from tools.screen_control.input_watcher import InputWatcher


class _Clock:
    def __init__(self):
        self.now = 500.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class IdleReportingTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.watcher = InputWatcher(clock=self.clock)

    def test_no_input_seen_reports_none_not_zero(self):
        # None means "nothing observed", which is different from "the user
        # just acted". Callers must not read it as either.
        self.assertIsNone(self.watcher.seconds_since_user_input())
        self.assertFalse(self.watcher.user_active_within(3.0))

    def test_real_input_starts_the_clock(self):
        self.watcher._record_real(mouse=True)
        self.assertEqual(self.watcher.seconds_since_user_input(), 0.0)
        self.assertTrue(self.watcher.user_active_within(3.0))

    def test_activity_lapses_after_the_window(self):
        self.watcher._record_real(mouse=True)
        self.clock.advance(5.0)
        self.assertFalse(self.watcher.user_active_within(3.0))
        self.assertTrue(self.watcher.user_active_within(10.0))

    def test_keyboard_input_counts_as_activity(self):
        # The whole reason for hooks over pointer-watching: typing never
        # moves the mouse.
        self.watcher._record_real(mouse=False)
        self.assertTrue(self.watcher.user_active_within(3.0))
        self.assertEqual(self.watcher.counters()["real_key"], 1)


class InterruptionTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.watcher = InputWatcher(clock=self.clock)

    def test_no_input_since_the_mark_is_no_interruption(self):
        mark = self.watcher.mark()
        self.clock.advance(2.0)
        self.assertFalse(self.watcher.user_input_since(mark))

    def test_input_after_the_mark_is_an_interruption(self):
        mark = self.watcher.mark()
        self.clock.advance(1.0)
        self.watcher._record_real(mouse=True)
        self.assertTrue(self.watcher.user_input_since(mark))

    def test_input_at_the_same_instant_as_the_mark_is_not_double_counted(self):
        # A real monotonic clock has sub-microsecond resolution, so a tie is
        # only reachable with a frozen clock. Strictly-after keeps input that
        # happened before begin_run from aborting the new run.
        self.watcher._record_real(mouse=True)
        self.assertFalse(self.watcher.user_input_since(self.watcher.mark()))

    def test_input_before_the_mark_is_not_an_interruption(self):
        # Input that preceded a run must not abort a newly requested task.
        self.watcher._record_real(mouse=True)
        self.clock.advance(1.0)
        mark = self.watcher.mark()
        self.assertFalse(self.watcher.user_input_since(mark))


class AvailabilityTests(unittest.TestCase):
    def test_a_watcher_that_never_started_reports_unavailable(self):
        # It must not look like "the user is idle" -- that would let Elaina
        # grab the mouse from someone actively using it.
        watcher = InputWatcher()
        self.assertFalse(watcher.available)
        self.assertIsNone(watcher.seconds_since_user_input())

    def test_counters_start_empty(self):
        watcher = InputWatcher()
        self.assertEqual(
            watcher.counters(),
            {"real_mouse": 0, "real_key": 0, "injected": 0, "self_echo": 0},
        )

    def test_an_unflagged_echo_of_our_own_event_is_not_the_person(self):
        """The acceptance run cancelled six browser actions and blamed the
        person, who had not touched the mouse.

        The injected flag is the primary test and it is the right one --
        measured, every event CursorDriver generated arrived flagged. But
        that was measured on one machine, and a pointing-device driver, an
        overlay or a remote session can pass one of our own events through
        unflagged. Believing it costs the whole feature and blames the
        wrong person for it.
        """
        clock = [100.0]
        watcher = InputWatcher(clock=lambda: clock[0])

        watcher.note_self_input()
        clock[0] += 0.05          # the echo of what we just injected
        watcher._record_real(mouse=True)

        self.assertFalse(watcher.user_input_since(99.0))
        self.assertEqual(watcher.counters()["self_echo"], 1)
        self.assertEqual(watcher.counters()["real_mouse"], 0)

    def test_a_real_event_after_the_grace_window_still_counts(self):
        # The over-correction to watch: a genuine interruption a moment
        # later must still stop the run.
        clock = [100.0]
        watcher = InputWatcher(clock=lambda: clock[0])

        watcher.note_self_input()
        clock[0] += 1.0
        watcher._record_real(mouse=True)

        self.assertTrue(watcher.user_input_since(99.0))
        self.assertEqual(watcher.counters()["real_mouse"], 1)

    def test_the_last_event_can_be_named(self):
        # A cancellation that says "you moved the mouse" has to be able to
        # show the event it is talking about.
        clock = [100.0]
        watcher = InputWatcher(clock=lambda: clock[0])

        watcher._record_real(mouse=False)

        kind, when = watcher.last_real_event()
        self.assertEqual(kind, "key")
        self.assertEqual(when, 100.0)

    def test_stop_is_safe_before_start(self):
        InputWatcher().stop()


class CursorIntegrationTests(unittest.TestCase):
    """CursorDriver must consult the watcher, not just pointer position."""

    def setUp(self):
        from tools.screen_control.cursor_driver import CursorDriver

        self.clock = _Clock()
        self.watcher = InputWatcher(clock=self.clock)
        # Pretend the hooks installed, so the query path is exercised.
        self.watcher._installed.set()
        self.position = (100, 100)
        self.driver = CursorDriver(
            sender=lambda inputs: len(inputs),
            cursor_reader=lambda: self.position,
            sleeper=lambda seconds: None,
            input_watcher=self.watcher,
        )

    def test_typing_is_seen_as_a_takeover_without_the_mouse_moving(self):
        self.driver.begin_run()
        self.driver._parked_at = self.position
        self.assertFalse(self.driver.user_took_over())
        self.clock.advance(0.5)
        self.watcher._record_real(mouse=False)  # the user types
        self.assertTrue(self.driver.user_took_over())

class TheEvidenceForACancellationTests(unittest.TestCase):
    """A takeover that blames somebody has to show its working.

    From the acceptance run, the whole of what was known about six
    cancelled browser actions:

        [Input Watch] takeover reason=real_input event=mouse
                      at=1072878.968 mark=1072877.171
                      counters={'real_mouse': 4457, ...}

    A timestamp and a lifetime tally cannot answer whether that was a
    hand on a mouse. These fields can.
    """

    def setUp(self):
        self.clock = _Clock()
        self.watcher = InputWatcher(clock=self.clock)
        self.watcher._installed.set()

    def test_an_event_carries_what_it_actually_was(self):
        self.watcher.note_self_input((500, 400))
        self.clock.advance(2.0)
        self.watcher._record_real(
            mouse=True, what="move", x=931, y=502, flags=0x00,
            at_tick=1000,
        )

        event = self.watcher.last_real_detail()
        self.assertEqual(event.what, "move")
        self.assertEqual((event.x, event.y), (931, 502))
        self.assertFalse(event.lower_il)
        self.assertEqual(event.self_point, (500, 400))
        self.assertEqual(event.distance_from_self, 431)
        self.assertAlmostEqual(event.since_self_ms, 2000.0)

    def test_a_lower_integrity_injection_is_recorded_as_one(self):
        # The flag that separates "something else automated this" from "a
        # person did it". Blaming the person needs it clear.
        self.watcher._record_real(
            mouse=True, what="move", flags=0x02, lower_il=True,
        )
        self.assertTrue(self.watcher.last_real_detail().lower_il)

    def test_a_button_held_down_shows_in_the_next_move(self):
        self.watcher._buttons_down = 0x1
        self.watcher._record_real(mouse=True, what="move")
        self.assertEqual(self.watcher.last_real_detail().buttons_down, 0x1)

    def test_counters_can_be_scoped_to_one_action(self):
        # 4457 across a session says nothing. The same number during one
        # click says the hook is seeing something it should not.
        for _ in range(5):
            self.watcher._record_real(mouse=True, what="move")
        self.watcher.begin_action("click_element:about")
        self.watcher._record_real(mouse=True, what="move")

        self.assertEqual(self.watcher.counters()["real_mouse"], 6)
        self.assertEqual(self.watcher.action_counters()["real_mouse"], 1)
        self.assertEqual(self.watcher.last_real_detail().action,
                         "click_element:about")

    def test_only_events_after_the_mark_are_the_evidence(self):
        self.watcher._record_real(mouse=True, what="move")
        mark = self.watcher.mark()
        self.clock.advance(0.5)
        self.watcher._record_real(mouse=True, what="left_down")

        after = self.watcher.real_events_since(mark)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0].what, "left_down")

    def test_the_line_names_what_the_hook_saw(self):
        mark = self.watcher.mark()
        self.clock.advance(0.5)
        self.watcher._record_real(mouse=True, what="left_down", x=12, y=34)

        line = self.watcher.evidence_since(mark)
        self.assertIn("left_down", line)
        self.assertIn("(12,34)", line)
        self.assertIn("action_counters", line)

    def test_a_self_echo_is_not_evidence_of_anything(self):
        self.watcher.note_self_input((10, 10))
        self.watcher._record_real(mouse=True, what="move", x=10, y=10)

        self.assertEqual(self.watcher.real_events_since(0.0), ())
        self.assertEqual(self.watcher.counters()["self_echo"], 1)


if __name__ == "__main__":
    unittest.main()
