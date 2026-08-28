import unittest

from tools.screen_browser.browser_window import (
    BrowserWindow,
    BrowserWindowFinder,
    executable_is_browser,
)


def _finder(windows, *, names, foreground=0):
    """A finder over fake (handle, title, pid, rect) rows."""
    return BrowserWindowFinder(
        enumerator=lambda: list(windows),
        process_name_reader=lambda pid: names.get(pid, ""),
        foreground_reader=lambda: foreground,
    )


class ExecutableClassificationTests(unittest.TestCase):
    def test_recognises_browser_executables(self):
        for image in (
            r"C:\Program Files\Naver\Naver Whale\Application\whale.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "MSEDGE.EXE",
        ):
            with self.subTest(image=image):
                self.assertTrue(executable_is_browser(image))

    def test_rejects_webview_hosts(self):
        # Measured live: the ChatGPT and Claude desktop apps present
        # Chrome_WidgetWin_1 windows and expose no page at all. Class name
        # cannot separate them from a real browser; the executable can.
        self.assertFalse(executable_is_browser(
            r"C:\Program Files\Microsoft\EdgeWebView\msedgewebview2.exe",
        ))
        self.assertFalse(executable_is_browser("electron.exe"))

    def test_rejects_unknown_and_empty(self):
        self.assertFalse(executable_is_browser(r"C:\Apps\Code.exe"))
        self.assertFalse(executable_is_browser(""))


class WindowDiscoveryTests(unittest.TestCase):
    def test_only_browser_windows_are_returned(self):
        finder = _finder(
            [
                (1, "YouTube - Whale", 10, (0, 0, 100, 100)),
                (2, "ChatGPT", 20, (0, 0, 100, 100)),
                (3, "project - Visual Studio Code", 30, (0, 0, 100, 100)),
            ],
            names={
                10: r"C:\x\whale.exe",
                20: r"C:\x\msedgewebview2.exe",
                30: r"C:\x\Code.exe",
            },
        )
        self.assertEqual([w.handle for w in finder.list_windows()], [1])

    def test_foreground_window_sorts_first(self):
        finder = _finder(
            [
                (7, "a - Chrome", 10, (0, 0, 1, 1)),
                (9, "b - Chrome", 10, (0, 0, 1, 1)),
            ],
            names={10: "chrome.exe"},
            foreground=9,
        )
        windows = finder.list_windows()
        self.assertEqual([w.handle for w in windows], [9, 7])
        self.assertTrue(windows[0].is_active)

    def test_active_window_refuses_to_guess_between_unfocused_windows(self):
        finder = _finder(
            [
                (7, "a - Chrome", 10, (0, 0, 1, 1)),
                (9, "b - Chrome", 10, (0, 0, 1, 1)),
            ],
            names={10: "chrome.exe"},
            foreground=0,
        )
        self.assertIsNone(finder.active_window())

    def test_single_unfocused_window_is_unambiguous(self):
        finder = _finder(
            [(7, "a - Chrome", 10, (0, 0, 1, 1))],
            names={10: "chrome.exe"},
            foreground=0,
        )
        self.assertEqual(finder.active_window().handle, 7)

    def test_no_browser_windows_gives_none(self):
        finder = _finder([], names={})
        self.assertEqual(finder.list_windows(), ())
        self.assertIsNone(finder.active_window())

    def test_window_for_handle(self):
        finder = _finder(
            [(7, "a - Chrome", 10, (0, 0, 1, 1))], names={10: "chrome.exe"},
        )
        self.assertIsNotNone(finder.window_for_handle(7))
        self.assertIsNone(finder.window_for_handle(8))

    def test_enumeration_failure_reports_no_windows_instead_of_raising(self):
        def _explode():
            raise OSError("enumeration failed")

        finder = BrowserWindowFinder(
            enumerator=_explode,
            process_name_reader=lambda pid: "chrome.exe",
            foreground_reader=lambda: 0,
        )
        self.assertEqual(finder.list_windows(), ())
        self.assertIsNone(finder.active_window())


class PageTitleTests(unittest.TestCase):
    def test_browser_suffix_is_removed(self):
        window = BrowserWindow(1, "Example Domain - Whale", 1, "whale.exe")
        self.assertEqual(window.page_title, "Example Domain")

    def test_long_tail_is_kept_because_it_is_page_text(self):
        title = "Recipe - how to make the best sourdough bread at home"
        window = BrowserWindow(1, title, 1, "whale.exe")
        self.assertEqual(window.page_title, title)

    def test_title_without_separator_is_unchanged(self):
        window = BrowserWindow(1, "Example", 1, "whale.exe")
        self.assertEqual(window.page_title, "Example")


if __name__ == "__main__":
    unittest.main()
