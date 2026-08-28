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
            {"real_mouse": 0, "real_key": 0, "injected": 0},
        )

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

if __name__ == "__main__":
    unittest.main()
