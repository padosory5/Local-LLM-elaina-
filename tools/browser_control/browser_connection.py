"""Chrome DevTools Protocol connection for Elaina-controlled browser pages.

Phase 4C deliberately opens pages in a dedicated Chromium profile rather
than attaching to an ordinary personal browser window.  That gives the
browser opener and DOM controller one stable session, while keeping the
user's unrelated tabs, cookies, and unsaved form state out of scope.

When a user asks about an already-open normal browser page, we still do not
silently start using a different page: :meth:`connect` reports that the page
is not control-ready unless navigation specifically opted into the isolated
session.
"""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tools.computer_control.windows_app_catalog import WindowsAppCatalog
from tools.computer_control.windows_process_control import WindowsProcessControl

_PORT_CHECK_TIMEOUT_SECONDS = 0.5
_LAUNCH_WAIT_ATTEMPTS = 10
_LAUNCH_WAIT_INTERVAL_SECONDS = 0.5


class BrowserConnectionError(OSError):
    """A browser could not be made available for a requested navigation."""


@dataclass(frozen=True)
class BrowserConnectionResult:
    status: str  # "connected", "not_debug_enabled", "not_found", "unavailable"
    message: str = ""
    browser: Any = None  # a connected playwright.sync_api.Browser
    playwright: Any = None  # the Playwright driver; caller must .stop() it
    # True only when this connection call created Elaina's isolated browser.
    # ``open_url`` uses it to reuse the command-line navigation tab instead
    # of visibly creating an about:blank tab and navigating it a moment later.
    launched: bool = False


class BrowserConnection:
    """Attach to, or safely start, the user's configured browser over CDP."""

    def __init__(
        self,
        *,
        browser_name: str = "Whale",
        debugging_port: int = 9222,
        user_data_dir: str | Path | None = None,
        catalog: WindowsAppCatalog | None = None,
        process_control: WindowsProcessControl | None = None,
        port_checker=None,
        launcher=None,
    ) -> None:
        self.browser_name = str(browser_name).strip() or "Whale"
        self.debugging_port = int(debugging_port)
        # Chromium 136+ ignores a remote-debugging port attached to its
        # default profile.  More importantly, attaching automation to a
        # person's normal profile would expose every open tab and cookie to
        # this local process.  Elaina therefore owns one isolated profile for
        # pages she opens herself.  It is deliberately persistent so a user
        # can sign in to a site there if they choose, but it is never the
        # browser's default profile.
        self.user_data_dir = self._resolve_user_data_dir(user_data_dir)
        self.catalog = catalog or WindowsAppCatalog()
        self.process_control = process_control or WindowsProcessControl()
        self._port_checker = port_checker or self._default_port_checker
        self._launcher = launcher or subprocess.Popen
        # The URL after redirects is the identity that BrowserObserver should
        # prefer on the next turn.  A search engine often adds harmless query
        # parameters, so the requested URL alone is not reliable enough to
        # find the tab that was just opened.
        self.last_opened_url = ""

    def connect(
        self,
        *,
        allow_isolated_launch: bool = False,
        initial_url: str = "",
    ) -> BrowserConnectionResult:
        """Attach to a CDP browser, optionally starting Elaina's safe one.

        ``allow_isolated_launch`` is intentionally opt-in.  A request about
        an already-open normal browser page must never silently replace that
        page with an unrelated profile.  Navigation that Elaina starts,
        however, opts in so its follow-up DOM actions stay in the same
        controllable browser session.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return BrowserConnectionResult(
                "unavailable",
                "Browser control isn't installed on this system.",
            )

        if self._port_checker(self.debugging_port):
            return self._connect_over_cdp(sync_playwright)

        if self._browser_is_running() and not allow_isolated_launch:
            return BrowserConnectionResult(
                "not_debug_enabled",
                (
                    f"{self.browser_name} is open, but not ready for browser "
                    "control. Pages opened by Elaina use a separate, "
                    "Elaina-controlled browser window; ask me to reopen this "
                    "page there, or start your browser with remote debugging "
                    "and a separate user-data directory."
                ),
            )

        launched = self._launch_with_debugging(initial_url=initial_url)
        if not launched:
            return BrowserConnectionResult(
                "not_found",
                f"I couldn't find {self.browser_name} installed on this device.",
            )
        if not self._wait_for_port():
            return BrowserConnectionResult(
                "unavailable",
                (
                    f"{self.browser_name} started, but its browser-control "
                    "port never opened."
                ),
            )
        return self._connect_over_cdp(sync_playwright, launched=True)

    def open_url(self, url: str) -> bool:
        """Open ``url`` in the same CDP session later used for DOM control.

        This is the bridge that was missing from Phase 4C: using
        ``webbrowser.open_new_tab`` launches whichever Windows browser happens
        to be default, which is often neither the configured browser nor a
        CDP-enabled process.  A fresh, isolated Elaina browser is safe to
        start even when a normal browser is already open because it never
        closes, reuses, or reads the normal profile.
        """
        requested_url = str(url).strip()
        result = self.connect(
            allow_isolated_launch=True,
            initial_url=requested_url,
        )
        if result.status != "connected" or result.browser is None:
            raise BrowserConnectionError(
                result.message or "I couldn't start the controlled browser."
            )

        playwright = result.playwright
        try:
            contexts = list(getattr(result.browser, "contexts", ()) or ())
            if not contexts:
                raise BrowserConnectionError(
                    "The controlled browser did not expose a usable context."
                )
            # On a cold launch Chromium first shows an about:blank tab.  Pass
            # the requested address on its command line (see
            # ``_launch_with_debugging``), then reuse that page once CDP is
            # ready.  This avoids a second blank tab and lets the visible
            # navigation start while the debugging port is coming up.
            page = (
                self._launched_navigation_page(contexts[0], requested_url)
                if result.launched
                else None
            )
            if page is None:
                page = contexts[0].new_page()
                page.goto(requested_url, timeout=15000, wait_until="domcontentloaded")
            else:
                # The browser already received the URL.  Waiting is useful
                # for immediate DOM follow-up, but a slow third-party page
                # must not cause us to open the same URL a second time or
                # falsely report that browser launch failed.
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
            self.last_opened_url = str(getattr(page, "url", "") or requested_url)
            try:
                page.bring_to_front()
            except Exception:
                # Bringing a tab forward is cosmetic.  Navigation itself is
                # already complete and must not be reported as failed because
                # a browser chose not to expose that optional CDP operation.
                pass
            return True
        except BrowserConnectionError:
            raise
        except Exception as error:
            raise BrowserConnectionError(
                f"I couldn't open that page in the controlled browser: {error}"
            ) from error
        finally:
            # Disconnect Playwright without closing the user's controlled
            # browser window.  BrowserObserver reconnects over CDP on the
            # action turn and sees the page that was just opened.
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    pass

    def _connect_over_cdp(
        self,
        sync_playwright,
        *,
        launched: bool = False,
    ) -> BrowserConnectionResult:
        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.connect_over_cdp(
                f"http://localhost:{self.debugging_port}"
            )
        except Exception as error:
            playwright.stop()
            return BrowserConnectionResult(
                "unavailable", f"I couldn't connect to the browser: {error}",
            )
        return BrowserConnectionResult(
            "connected", browser=browser, playwright=playwright, launched=launched,
        )

    def _browser_is_running(self) -> bool:
        resolution = self.catalog.resolve(self.browser_name)
        if resolution.status != "resolved" or resolution.entry is None:
            return False
        process_resolution = self.process_control.resolve(resolution.entry)
        return process_resolution.status == "resolved"

    def _launch_with_debugging(self, *, initial_url: str = "") -> bool:
        resolution = self.catalog.resolve(self.browser_name)
        if (
            resolution.status != "resolved"
            or resolution.entry is None
            or resolution.entry.launch_kind != "executable"
        ):
            return False
        executable = resolution.entry.launch_value
        try:
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            command = [
                executable,
                f"--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={self.debugging_port}",
                f"--user-data-dir={self.user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            # ``open_url`` receives an already validated HTTP(S) address via
            # SafeBrowserControl.  Keep this defensive check nevertheless,
            # because this low-level launcher is also useful in tests and
            # must never turn arbitrary text into a browser command argument.
            launch_url = str(initial_url or "").strip()
            if urlsplit(launch_url).scheme.casefold() in {"http", "https"}:
                command.extend(("--new-window", launch_url))
            self._launcher(command, shell=False)
        except OSError:
            return False
        return True

    @staticmethod
    def _launched_navigation_page(context: Any, requested_url: str) -> Any | None:
        """Return the exact page Chromium began from ``requested_url``.

        We intentionally require the complete scheme, host, path, and query
        to match.  A persistent Elaina profile can contain older YouTube tabs;
        reusing one of those would be worse than opening a fresh tab.
        """
        expected = urlsplit(str(requested_url))
        if expected.scheme.casefold() not in {"http", "https"}:
            return None
        for page in list(getattr(context, "pages", ()) or ()):
            actual = urlsplit(str(getattr(page, "url", "") or ""))
            if (
                actual.scheme.casefold() == expected.scheme.casefold()
                and actual.netloc.casefold() == expected.netloc.casefold()
                and actual.path == expected.path
                and actual.query == expected.query
            ):
                return page
        return None

    @staticmethod
    def _resolve_user_data_dir(value: str | Path | None) -> Path:
        if value is not None and str(value).strip():
            return Path(value).expanduser().resolve()
        # Keep generated automation state alongside the application's other
        # private runtime state.  This also makes the location predictable for
        # a portable install without touching Windows' default browser data.
        return Path(__file__).resolve().parents[1] / "runtime" / "data" / "browser-profile"

    def _wait_for_port(self) -> bool:
        for _ in range(_LAUNCH_WAIT_ATTEMPTS):
            if self._port_checker(self.debugging_port):
                return True
            time.sleep(_LAUNCH_WAIT_INTERVAL_SECONDS)
        return False

    @staticmethod
    def _default_port_checker(port: int) -> bool:
        try:
            with socket.create_connection(
                ("localhost", port), timeout=_PORT_CHECK_TIMEOUT_SECONDS,
            ):
                return True
        except OSError:
            return False
