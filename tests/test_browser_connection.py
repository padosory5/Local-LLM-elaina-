import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from tools.browser_control.browser_connection import BrowserConnection, BrowserConnectionResult
from tools.computer_control.windows_app_catalog import AppEntry, AppResolution
from tools.computer_control.windows_process_control import ProcessInfo, ProcessResolution


class FakeCatalog:
    def __init__(self, resolution: AppResolution):
        self.resolution = resolution
        self.queries = []

    def resolve(self, query):
        self.queries.append(query)
        return self.resolution


class FakeProcessControl:
    def __init__(self, resolution: ProcessResolution):
        self.resolution = resolution
        self.resolve_calls = []

    def resolve(self, entry):
        self.resolve_calls.append(entry)
        return self.resolution


_WHALE_ENTRY = AppEntry.create(
    "Whale", "executable", "C:/Program Files/Naver/Naver Whale/Application/whale.exe",
)


class _FakePage:
    def __init__(self, url: str = "about:blank"):
        self.url = url
        self.goto_calls = []
        self.load_state_calls = []
        self.brought_to_front = False

    def goto(self, url, *, timeout, wait_until):
        self.goto_calls.append((url, timeout, wait_until))
        self.url = url

    def wait_for_load_state(self, state, *, timeout):
        self.load_state_calls.append((state, timeout))

    def bring_to_front(self):
        self.brought_to_front = True


class _FakeContext:
    def __init__(self, pages=()):
        self.pages = list(pages)
        self.new_page_calls = 0
        self.next_page = _FakePage()

    def new_page(self):
        self.new_page_calls += 1
        self.pages.append(self.next_page)
        return self.next_page


class _FakeBrowser:
    def __init__(self, contexts):
        self.contexts = contexts


class BrowserConnectionAlreadyReachableTests(unittest.TestCase):
    def test_connects_directly_when_the_debug_port_is_already_open(self):
        fake_browser = object()
        fake_playwright_instance = MagicMock()
        fake_playwright_instance.chromium.connect_over_cdp.return_value = fake_browser
        fake_playwright_factory = MagicMock()
        fake_playwright_factory.start.return_value = fake_playwright_instance

        connection = BrowserConnection(
            browser_name="Whale",
            catalog=FakeCatalog(AppResolution("resolved", "Whale", entry=_WHALE_ENTRY)),
            process_control=FakeProcessControl(ProcessResolution("not_running")),
            port_checker=lambda port: True,
        )

        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=fake_playwright_factory,
        ):
            result = connection.connect()

        self.assertEqual(result.status, "connected")
        self.assertIs(result.browser, fake_browser)
        fake_playwright_instance.chromium.connect_over_cdp.assert_called_once_with(
            "http://localhost:9222"
        )

    def test_connection_failure_still_stops_the_driver(self):
        fake_playwright_instance = MagicMock()
        fake_playwright_instance.chromium.connect_over_cdp.side_effect = RuntimeError(
            "refused"
        )
        fake_playwright_factory = MagicMock()
        fake_playwright_factory.start.return_value = fake_playwright_instance

        connection = BrowserConnection(
            browser_name="Whale",
            catalog=FakeCatalog(AppResolution("resolved", "Whale", entry=_WHALE_ENTRY)),
            process_control=FakeProcessControl(ProcessResolution("not_running")),
            port_checker=lambda port: True,
        )

        with patch(
            "playwright.sync_api.sync_playwright",
            return_value=fake_playwright_factory,
        ):
            result = connection.connect()

        self.assertEqual(result.status, "unavailable")
        fake_playwright_instance.stop.assert_called_once()


class BrowserConnectionRunningWithoutDebugTests(unittest.TestCase):
    def test_explains_the_relaunch_shortcut_without_closing_anything(self):
        # A browser already running normally must never be force-closed --
        # that risks losing open tabs and unsaved form state. Elaina only
        # ever explains what to do.
        launcher = MagicMock()
        connection = BrowserConnection(
            browser_name="Whale",
            catalog=FakeCatalog(AppResolution("resolved", "Whale", entry=_WHALE_ENTRY)),
            process_control=FakeProcessControl(
                ProcessResolution(
                    "resolved", (ProcessInfo(pid=123, name="whale", title="Naver Whale"),)
                )
            ),
            port_checker=lambda port: False,
            launcher=launcher,
        )

        result = connection.connect()

        self.assertEqual(result.status, "not_debug_enabled")
        self.assertIn("Elaina-controlled", result.message)
        launcher.assert_not_called()

    def test_navigation_mode_can_start_an_isolated_browser_without_closing_normal_one(self):
        launcher = MagicMock()
        connection = BrowserConnection(
            browser_name="Whale",
            catalog=FakeCatalog(AppResolution("resolved", "Whale", entry=_WHALE_ENTRY)),
            process_control=FakeProcessControl(
                ProcessResolution(
                    "resolved", (ProcessInfo(pid=123, name="whale", title="Naver Whale"),)
                )
            ),
            port_checker=lambda port: False,
            launcher=launcher,
        )

        with patch("tools.browser_control.browser_connection.time.sleep"):
            result = connection.connect(allow_isolated_launch=True)

        self.assertEqual(result.status, "unavailable")
        launcher.assert_called_once()


class BrowserConnectionNotRunningTests(unittest.TestCase):
    def test_launches_with_the_debug_flag_when_nothing_is_running(self):
        launcher = MagicMock()
        fake_browser = object()
        fake_playwright_instance = MagicMock()
        fake_playwright_instance.chromium.connect_over_cdp.return_value = fake_browser
        fake_playwright_factory = MagicMock()
        fake_playwright_factory.start.return_value = fake_playwright_instance

        with TemporaryDirectory() as directory:
            profile = Path(directory) / "elaina-browser-profile"
            connection = BrowserConnection(
                browser_name="Whale",
                user_data_dir=profile,
                catalog=FakeCatalog(AppResolution("resolved", "Whale", entry=_WHALE_ENTRY)),
                process_control=FakeProcessControl(ProcessResolution("not_running")),
                port_checker=lambda port: True,
                launcher=launcher,
            )
            # Nothing running yet -- simulate the port only appearing after launch.
            connection._port_checker = lambda port: False

            def fake_launch(*args, **kwargs):
                connection._port_checker = lambda port: True
                return MagicMock()

            launcher.side_effect = fake_launch

            with patch(
                "playwright.sync_api.sync_playwright",
                return_value=fake_playwright_factory,
            ):
                result = connection.connect()

            self.assertEqual(result.status, "connected")
            command = launcher.call_args.args[0]
            self.assertEqual(command[0], "C:/Program Files/Naver/Naver Whale/Application/whale.exe")
            self.assertIn("--remote-debugging-address=127.0.0.1", command)
            self.assertIn("--remote-debugging-port=9222", command)
            self.assertIn(f"--user-data-dir={profile.resolve()}", command)
            self.assertEqual(launcher.call_args.kwargs, {"shell": False})

    def test_initial_navigation_url_is_passed_to_the_browser_launch_command(self):
        launcher = MagicMock()
        initial_url = "https://www.youtube.com/results?search_query=Laufey+From+The+Start"
        with TemporaryDirectory() as directory:
            connection = BrowserConnection(
                browser_name="Whale",
                user_data_dir=Path(directory) / "profile",
                catalog=FakeCatalog(AppResolution("resolved", "Whale", entry=_WHALE_ENTRY)),
                process_control=FakeProcessControl(ProcessResolution("not_running")),
                port_checker=lambda port: False,
                launcher=launcher,
            )

            self.assertTrue(connection._launch_with_debugging(initial_url=initial_url))

        command = launcher.call_args.args[0]
        self.assertEqual(command[-2:], ["--new-window", initial_url])

    def test_open_url_reuses_the_command_line_navigation_tab_after_cold_launch(self):
        requested_url = "https://www.youtube.com/results?search_query=Laufey+From+The+Start"
        launched_page = _FakePage(requested_url)
        context = _FakeContext([launched_page])
        playwright = MagicMock()
        connection = BrowserConnection(
            catalog=FakeCatalog(AppResolution("not_found", "Whale")),
            process_control=FakeProcessControl(ProcessResolution("not_running")),
            port_checker=lambda port: True,
        )
        connection.connect = MagicMock(return_value=BrowserConnectionResult(
            "connected",
            browser=_FakeBrowser([context]),
            playwright=playwright,
            launched=True,
        ))

        self.assertTrue(connection.open_url(requested_url))

        connection.connect.assert_called_once_with(
            allow_isolated_launch=True,
            initial_url=requested_url,
        )
        self.assertEqual(context.new_page_calls, 0)
        self.assertEqual(launched_page.goto_calls, [])
        self.assertEqual(launched_page.load_state_calls, [("domcontentloaded", 15000)])
        self.assertTrue(launched_page.brought_to_front)
        self.assertEqual(connection.last_opened_url, requested_url)
        playwright.stop.assert_called_once()

    def test_open_url_adds_a_page_when_the_controlled_browser_is_already_running(self):
        requested_url = "https://www.youtube.com/results?search_query=Laufey+From+The+Start"
        context = _FakeContext([_FakePage("https://www.google.com/")])
        playwright = MagicMock()
        connection = BrowserConnection(
            catalog=FakeCatalog(AppResolution("not_found", "Whale")),
            process_control=FakeProcessControl(ProcessResolution("not_running")),
            port_checker=lambda port: True,
        )
        connection.connect = MagicMock(return_value=BrowserConnectionResult(
            "connected",
            browser=_FakeBrowser([context]),
            playwright=playwright,
            launched=False,
        ))

        self.assertTrue(connection.open_url(requested_url))

        self.assertEqual(context.new_page_calls, 1)
        self.assertEqual(
            context.next_page.goto_calls,
            [(requested_url, 15000, "domcontentloaded")],
        )
        self.assertTrue(context.next_page.brought_to_front)

    def test_reports_not_found_when_the_browser_is_not_installed(self):
        connection = BrowserConnection(
            browser_name="Whale",
            catalog=FakeCatalog(AppResolution("not_found", "Whale")),
            process_control=FakeProcessControl(ProcessResolution("not_running")),
            port_checker=lambda port: False,
        )

        result = connection.connect()

        self.assertEqual(result.status, "not_found")

    def test_reports_unavailable_when_the_port_never_opens_after_launch(self):
        with TemporaryDirectory() as directory:
            connection = BrowserConnection(
                browser_name="Whale",
                user_data_dir=Path(directory) / "profile",
                catalog=FakeCatalog(AppResolution("resolved", "Whale", entry=_WHALE_ENTRY)),
                process_control=FakeProcessControl(ProcessResolution("not_running")),
                port_checker=lambda port: False,
                launcher=MagicMock(),
            )

            with patch("tools.browser_control.browser_connection.time.sleep"):
                result = connection.connect()

            self.assertEqual(result.status, "unavailable")
            self.assertIn("never opened", result.message)


if __name__ == "__main__":
    unittest.main()
