import unittest

from tools.screen_control.cursor_driver import CursorDriver

_SCREEN = (0, 0, 2560, 1440)
_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_MOVE = 0x0001
_LEFTDOWN = 0x0002
_LEFTUP = 0x0004
_WHEEL = 0x0800
_KEYUP = 0x0002
_UNICODE = 0x0004


class _FakeScreen:
    """Records SendInput calls and tracks where the pointer would end up."""

    def __init__(self, screen=_SCREEN, start=(0, 0)):
        self.screen = screen
        self.position = start
        self.events = []

    def send(self, inputs):
        for event in inputs:
            self.events.append(event)
            if event.type == _INPUT_MOUSE and event.union.mi.dwFlags & _MOVE:
                left, top, width, height = self.screen
                self.position = (
                    round(event.union.mi.dx * (width - 1) / 65535) + left,
                    round(event.union.mi.dy * (height - 1) / 65535) + top,
                )
        return len(inputs)

    def read(self):
        return self.position

    def mouse_flags(self):
        return [
            event.union.mi.dwFlags for event in self.events
            if event.type == _INPUT_MOUSE
        ]

    def key_events(self):
        return [event for event in self.events if event.type == _INPUT_KEYBOARD]


def _driver(fake, *, watcher=None):
    driver = CursorDriver(
        sender=fake.send, cursor_reader=fake.read, sleeper=lambda seconds: None,
        input_watcher=watcher,
    )
    driver.virtual_screen = staticmethod(lambda: fake.screen)
    return driver


class _UntrustedDriver(CursorDriver):
    """A machine where DPI awareness could not be established."""

    @property
    def available(self):
        return False



class DoubleClickTests(unittest.TestCase):
    def test_two_presses_are_sent_from_one_move(self):
        fake = _FakeScreen()
        driver = _driver(fake)

        result = driver.double_click((640, 360))

        self.assertTrue(result.succeeded)
        self.assertEqual(fake.read(), (640, 360))
        button_flags = [
            flag for flag in fake.mouse_flags()
            if flag in {_LEFTDOWN, _LEFTUP}
        ]
        self.assertEqual(
            button_flags, [_LEFTDOWN, _LEFTUP, _LEFTDOWN, _LEFTUP],
        )

    def test_the_second_press_is_inside_the_system_double_click_time(self):
        # Windows treats two presses as a double-click only if they arrive
        # within GetDoubleClickTime(), 500 ms by default. Anything slower is
        # two ordinary clicks -- exactly the gesture this replaces.
        fake = _FakeScreen()
        timeline = []
        driver = CursorDriver(
            sender=lambda inputs: (
                timeline.extend(
                    ("event", event.union.mi.dwFlags) for event in inputs
                ),
                fake.send(inputs),
            )[1],
            cursor_reader=fake.read,
            sleeper=lambda seconds: timeline.append(("sleep", seconds)),
        )
        driver.virtual_screen = staticmethod(lambda: fake.screen)

        driver.double_click((640, 360))

        presses = [
            index for index, entry in enumerate(timeline)
            if entry == ("event", _LEFTDOWN)
        ]
        releases = [
            index for index, entry in enumerate(timeline)
            if entry == ("event", _LEFTUP)
        ]
        self.assertEqual(len(presses), 2)
        gap = sum(
            seconds
            for kind, seconds in timeline[releases[0]:presses[1]]
            if kind == "sleep"
        )
        self.assertLess(gap, 0.5)

    def test_a_reclaimed_mouse_stops_before_any_press(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        driver._parked_at = (10, 10)
        fake.position = (900, 900)

        result = driver.double_click((640, 360))

        self.assertEqual(result.status, "user_took_over")
        self.assertNotIn(_LEFTDOWN, fake.mouse_flags())


class CoordinateTests(unittest.TestCase):
    def test_corners_map_to_the_absolute_range_exactly(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        self.assertEqual(driver._to_absolute((0, 0)), (0, 0))
        self.assertEqual(driver._to_absolute((2559, 1439)), (65535, 65535))

    def test_a_move_lands_on_the_requested_pixel(self):
        # The whole feature rests on this: UI Automation gives physical
        # pixels, SendInput takes a normalized range, and the round trip
        # has to come back to the same pixel.
        fake = _FakeScreen()
        driver = _driver(fake)
        for target in [(1, 1), (640, 360), (1280, 720), (2559, 1439)]:
            with self.subTest(target=target):
                fake.position = (0, 0)
                driver._parked_at = None
                self.assertTrue(driver.move(target).succeeded)
                self.assertEqual(fake.position, target)

    def test_offscreen_points_are_refused(self):
        driver = _driver(_FakeScreen())
        result = driver.move((99999, 10))
        self.assertEqual(result.status, "out_of_bounds")

    def test_movement_is_interpolated_so_hover_handlers_fire(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        driver.move((1280, 720))
        moves = [flag for flag in fake.mouse_flags() if flag & _MOVE]
        self.assertGreater(len(moves), 5)


class ClickTests(unittest.TestCase):
    def test_click_presses_and_releases_at_the_target(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        self.assertTrue(driver.click((900, 500)).succeeded)
        self.assertEqual(fake.position, (900, 500))
        buttons = [
            flag for flag in fake.mouse_flags() if flag in (_LEFTDOWN, _LEFTUP)
        ]
        self.assertEqual(buttons, [_LEFTDOWN, _LEFTUP])

    def test_scroll_sends_wheel_notches_in_the_right_direction(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        driver.click((100, 100))
        fake.events.clear()
        self.assertTrue(driver.scroll((100, 100), -3).succeeded)
        wheels = [
            event.union.mi.mouseData for event in fake.events
            if event.type == _INPUT_MOUSE and event.union.mi.dwFlags == _WHEEL
        ]
        self.assertEqual(len(wheels), 3)
        # mouseData is an unsigned DWORD; -120 arrives as its two's complement.
        self.assertEqual(wheels[0], (1 << 32) - 120)


class TakeoverTests(unittest.TestCase):
    def test_pointer_moving_on_its_own_stops_the_run(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        driver.click((900, 500))
        fake.position = (40, 40)  # the user grabbed the mouse
        self.assertTrue(driver.user_took_over())
        result = driver.click((1000, 600))
        self.assertEqual(result.status, "user_took_over")
        # Stopping is right. Blaming the person for it is not: this branch
        # sees a cursor somewhere else and knows nothing about who put it
        # there. Measured live, that sentence was said six times in one
        # session to somebody who had not touched the mouse.
        self.assertEqual(driver.last_takeover, "pointer_drift")
        self.assertNotIn("You moved the mouse", result.message)
        self.assertIn("moved the pointer", result.message)

    def test_real_input_is_the_only_thing_that_blames_the_user(self):
        class _Watcher:
            available = True

            def mark(self):
                return 0.0

            def user_input_since(self, marker):
                return True

            def last_real_event(self):
                return "mouse", 1.0

            def counters(self):
                return {"real_mouse": 1, "real_key": 0, "injected": 0}

            def note_self_input(self):
                pass

        driver = _driver(_FakeScreen(), watcher=_Watcher())
        driver.begin_run()

        result = driver.click((1000, 600))

        self.assertEqual(driver.last_takeover, "real_input")
        self.assertIn("You moved the mouse", result.message)

    def test_small_drift_is_not_treated_as_takeover(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        driver.click((900, 500))
        fake.position = (902, 501)
        self.assertFalse(driver.user_took_over())

    def test_nothing_parked_yet_is_not_takeover(self):
        driver = _driver(_FakeScreen())
        self.assertFalse(driver.user_took_over())

    def test_run_returns_the_pointer_where_the_user_left_it(self):
        fake = _FakeScreen(start=(777, 333))
        driver = _driver(fake)
        driver.begin_run()
        driver.click((1500, 900))
        driver.end_run()
        self.assertEqual(fake.position, (777, 333))

    def test_untrustworthy_coordinates_refuse_to_click(self):
        fake = _FakeScreen()
        driver = _UntrustedDriver(
            sender=fake.send, cursor_reader=fake.read,
            sleeper=lambda seconds: None,
        )
        result = driver.click((100, 100))
        self.assertEqual(result.status, "unsafe_coordinates")
        self.assertEqual(fake.events, [])


class KeyboardTests(unittest.TestCase):
    def test_text_is_typed_as_unicode_scan_codes(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        self.assertTrue(driver.type_text("hi").succeeded)
        events = fake.key_events()
        self.assertEqual(len(events), 4)  # down+up per character
        self.assertTrue(all(event.union.ki.dwFlags & _UNICODE for event in events))
        self.assertEqual([event.union.ki.wScan for event in events[:2]],
                         [ord("h"), ord("h")])

    def test_korean_text_types_correctly(self):
        # A virtual-key path would emit the wrong characters entirely
        # depending on the active keyboard layout.
        fake = _FakeScreen()
        driver = _driver(fake)
        driver.type_text("안녕")
        scans = [
            event.union.ki.wScan for event in fake.key_events()
            if not event.union.ki.dwFlags & _KEYUP
        ]
        self.assertEqual([chr(scan) for scan in scans], ["안", "녕"])

    def test_astral_characters_survive_as_surrogate_pairs(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        driver.type_text("😀")
        downs = [
            event for event in fake.key_events()
            if not event.union.ki.dwFlags & _KEYUP
        ]
        self.assertEqual(len(downs), 2)

    def test_chord_presses_down_in_order_and_releases_in_reverse(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        self.assertTrue(driver.press("ctrl", "a").succeeded)
        events = fake.key_events()
        downs = [e.union.ki.wVk for e in events if not e.union.ki.dwFlags & _KEYUP]
        ups = [e.union.ki.wVk for e in events if e.union.ki.dwFlags & _KEYUP]
        self.assertEqual(downs, [0x11, 0x41])
        self.assertEqual(ups, [0x41, 0x11])

    def test_unknown_key_is_refused_rather_than_guessed(self):
        driver = _driver(_FakeScreen())
        result = driver.press("hyperspace")
        self.assertEqual(result.status, "unavailable")

    def test_clear_field_selects_all_then_deletes(self):
        fake = _FakeScreen()
        driver = _driver(fake)
        self.assertTrue(driver.clear_field().succeeded)
        downs = [
            event.union.ki.wVk for event in fake.key_events()
            if not event.union.ki.dwFlags & _KEYUP
        ]
        self.assertEqual(downs, [0x11, 0x41, 0x2E])


if __name__ == "__main__":
    unittest.main()
