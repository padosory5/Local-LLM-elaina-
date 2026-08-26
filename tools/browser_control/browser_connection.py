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

from core.paths import DATA_DIRECTORY
from tools.computer_control.windows_app_catalog import WindowsAppCatalog
from tools.computer_control.windows_process_control import WindowsProcessControl

_PORT_CHECK_TIMEOUT_SECONDS = 0.5
# A cold browser launch on a busy machine routinely needs more than the 5
# seconds the old 10x0.5s wait allowed -- and giving up while the window is
# already visibly open is exactly the "blank browser sitting there forever"
# failure seen live. Expressed as a wall-clock deadline rather than an
# attempt count so the interval can change without silently changing how
# long a user waits, and injectable so tests exercising the give-up path
# do not actually sit here.
_LAUNCH_TIMEOUT_SECONDS = 15.0
_LAUNCH_WAIT_INTERVAL_SECONDS = 0.5
_CDP_CONNECT_ATTEMPTS = 3
_CDP_CONNECT_RETRY_SECONDS = 0.6
_CDP_VERSION_TIMEOUT_SECONDS = 2.0
_CDP_CONNECT_TIMEOUT_MS = 10_000
# If the configured CDP port/profile launches but never starts serving,
# recover once on a neighbouring port with a separate Elaina-only profile.
# This is intentionally a single bounded fallback: it fixes a stale/default
# port without turning one navigation into a series of visible browser windows.
_ISOLATED_RECOVERY_PORT_OFFSETS = (1,)


class BrowserConnectionError(OSError):
    """A browser could not be made available for a requested navigation."""


@dataclass(frozen=True)
class BrowserConnectionResult:
    status: str  # "connected", "not_control_ready", "not_debug_enabled", "not_found", "unavailable"
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
        launch_timeout_seconds: float = _LAUNCH_TIMEOUT_SECONDS,
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
        self.launch_timeout_seconds = max(0.0, float(launch_timeout_seconds))
        # The URL after redirects is the identity that BrowserObserver should
        # prefer on the next turn.  A search engine often adds harmless query
        # parameters, so the requested URL alone is not reliable enough to
        # find the tab that was just opened.
        self.last_opened_url = ""

    def cdp_endpoint_ready(self) -> str:
        """The live CDP WebSocket address, or "" while it isn't serving.

        A TCP port accepting connections is not the same as DevTools being
        ready to speak: Chromium opens the socket before the HTTP endpoint
        answers, and Playwright's own HTTP-URL connect path is documented
        as flaky in exactly that window. Asking /json/version for the real
        webSocketDebuggerUrl is both the readiness probe and the reliable
        thing to hand to connect_over_cdp afterwards.
        """
        import json as _json
        import urllib.request

        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.debugging_port}/json/version",
                timeout=_CDP_VERSION_TIMEOUT_SECONDS,
            ) as response:
                payload = _json.loads(response.read().decode("utf-8"))
            return str(payload.get("webSocketDebuggerUrl", "") or "")
        except Exception:
            return ""

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

        if not allow_isolated_launch:
            # Observation must remain observation. In particular,
            # BrowserObserver.describe_page() is allowed to discover that no
            # controlled page exists, but it must never visibly create an
            # isolated about:blank browser window as a side effect.
            if self._browser_is_running():
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
            return BrowserConnectionResult(
                "not_control_ready",
                (
                    "No Elaina-controlled browser page is open right now. "
                    "Ask me to search or open a public page and I can start "
                    "one in a separate controlled browser window."
                ),
            )

        launched = self._launch_with_debugging(initial_url=initial_url)
        if not launched:
            return BrowserConnectionResult(
                "not_found",
                f"I couldn't find {self.browser_name} installed on this device.",
            )
        if not self._wait_for_port():
            if self._recover_isolated_browser(initial_url=initial_url):
                return self._connect_over_cdp(sync_playwright, launched=True)
            return BrowserConnectionResult(
                "unavailable",
                (
                    f"{self.browser_name} started, but its browser-control "
                    "port never opened."
                ),
            )
        return self._connect_over_cdp(sync_playwright, launched=True)

    def _recover_isolated_browser(self, *, initial_url: str) -> bool:
        """Try one fresh isolated port/profile after a failed safe launch.

        This is reachable only from ``connect(...allow_isolated_launch=True)``
        after the primary Elaina profile failed to expose CDP. It never
        closes, attaches to, or reuses the user's normal browser profile.
        If the candidate port is already listening, leave it alone rather
        than risking attachment to somebody else's DevTools session.
        """
        original_port = self.debugging_port
        original_profile = self.user_data_dir
        for offset in _ISOLATED_RECOVERY_PORT_OFFSETS:
            candidate_port = original_port + offset
            if candidate_port > 65535 or self._port_checker(candidate_port):
                continue
            self.debugging_port = candidate_port
            self.user_data_dir = self._recovery_profile_path(
                original_profile, candidate_port,
            )
            if not self._launch_with_debugging(initial_url=initial_url):
                continue
            if self._wait_for_port():
                return True
        # A failed recovery must not leave later observations pointing at a
        # never-ready port/profile. The next user-requested navigation gets a
        # clean attempt at the configured controlled session instead.
        self.debugging_port = original_port
        self.user_data_dir = original_profile
        return False

    @staticmethod
    def _recovery_profile_path(profile: Path, port: int) -> Path:
        """A persistent profile that cannot contend with the failed launch."""
        return profile.with_name(f"{profile.name}-recovery-{port}")

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
            already_navigated = False
            if page is None and result.launched:
                # The command-line handoff did not land. The startup tab is
                # still sitting on about:blank and is the window the user is
                # actually looking at -- navigate that, rather than opening a
                # second tab beside it and leaving the blank one visible.
                # That stray blank tab is exactly what "she just opens an
                # about:blank page and loads forever" looked like.
                page = self._startup_tab(contexts[0])
                if page is not None:
                    self._navigate_or_confirm_committed(page, requested_url)
                    already_navigated = True
            if page is None:
                page = contexts[0].new_page()
                self._navigate_or_confirm_committed(page, requested_url)
            elif not already_navigated:
                # The browser already received the URL.  Waiting is useful
                # for immediate DOM follow-up, but a slow third-party page
                # must not cause us to open the same URL a second time or
                # falsely report that browser launch failed.
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                # A command-line URL is normally already committed by the
                # time CDP is ready.  Do not report success merely because a
                # window exists, though: a failed cold launch used to leave
                # us with a visible about:blank tab and a false "opened"
                # result.  One ordinary Playwright navigation is a safe
                # recovery when the command-line handoff did not land.
                if not self._navigation_committed(page, requested_url):
                    self._navigate_or_confirm_committed(page, requested_url)
            if not self._navigation_committed(page, requested_url):
                raise BrowserConnectionError(
                    "The controlled browser never reached the requested page; "
                    "it is still on its startup tab."
                )
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

    @staticmethod
    def _navigation_committed(page: Any, requested_url: str) -> bool:
        """Whether a visible page has left startup and reached the site.

        Redirects within a site's normal host family are allowed.  A blank
        tab, an old unrelated page, or a failed navigation is never an
        acceptable result for a generic ``open_url`` request.
        """
        try:
            actual = urlsplit(str(getattr(page, "url", "") or ""))
            requested = urlsplit(str(requested_url))
        except ValueError:
            return False
        actual_host = (actual.hostname or "").casefold().removeprefix("www.")
        requested_host = (
            (requested.hostname or "").casefold().removeprefix("www.")
        )
        if not actual_host or not requested_host:
            return False
        return (
            actual_host == requested_host
            or actual_host.endswith("." + requested_host)
            or requested_host.endswith("." + actual_host)
        )

    def _navigate_or_confirm_committed(self, page: Any, requested_url: str) -> None:
        """Navigate once and accept a timeout only after a real commit."""
        try:
            page.goto(requested_url, timeout=15000, wait_until="domcontentloaded")
        except Exception as error:
            if self._navigation_committed(page, requested_url):
                return
            raise BrowserConnectionError(
                f"I couldn't reach the requested page in the controlled browser: {error}"
            ) from error

    def _connect_over_cdp(
        self,
        sync_playwright,
        *,
        launched: bool = False,
    ) -> BrowserConnectionResult:
        playwright = sync_playwright().start()
        last_error: Exception | None = None
        for attempt in range(_CDP_CONNECT_ATTEMPTS):
            if attempt:
                time.sleep(_CDP_CONNECT_RETRY_SECONDS)
            # Prefer the WebSocket address /json/version reports -- it only
            # exists once DevTools is genuinely serving, and skips the
            # flaky HTTP-URL negotiation path entirely. 127.0.0.1, never
            # "localhost": Chromium binds the debug port to IPv4 only, and
            # on this machine "localhost" resolves to the IPv6 loopback
            # first (the same tax config.yaml documents for Ollama).
            endpoint = (
                self.cdp_endpoint_ready() if self._uses_real_probes else ""
            ) or f"http://127.0.0.1:{self.debugging_port}"
            try:
                # Bounded: connect_over_cdp is documented as capable of
                # hanging against a browser whose DevTools port accepts
                # connections but has stopped answering. Unbounded, that
                # becomes a turn that never finishes.
                browser = playwright.chromium.connect_over_cdp(
                    endpoint, timeout=_CDP_CONNECT_TIMEOUT_MS,
                )
                return BrowserConnectionResult(
                    "connected",
                    browser=browser,
                    playwright=playwright,
                    launched=launched,
                )
            except Exception as error:
                last_error = error
        playwright.stop()
        return BrowserConnectionResult(
            "unavailable", f"I couldn't connect to the browser: {last_error}",
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
    def _startup_tab(context: Any) -> Any | None:
        """The blank tab a cold Chromium launch leaves behind, if present."""
        blank_prefixes = ("about:blank", "chrome://newtab", "whale://newtab")
        for page in list(getattr(context, "pages", ()) or ()):
            url = str(getattr(page, "url", "") or "").casefold()
            if not url or url.startswith(blank_prefixes):
                return page
        return None

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
        # private runtime state (core.paths.DATA_DIRECTORY), not a path
        # computed relative to this file -- that broke silently once this
        # module moved a directory deeper during the tools/ reorg, spilling
        # a second untracked browser profile under tools/runtime/.
        return DATA_DIRECTORY / "browser-profile"

    def _wait_for_port(self) -> bool:
        deadline = time.monotonic() + self.launch_timeout_seconds
        while True:
            # The socket check keeps injected test checkers working; the
            # /json/version probe is what actually proves DevTools is up
            # (Chromium accepts TCP connections before it will answer).
            if self._port_checker(self.debugging_port) and (
                not self._uses_real_probes or self.cdp_endpoint_ready()
            ):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_LAUNCH_WAIT_INTERVAL_SECONDS)

    @property
    def _uses_real_probes(self) -> bool:
        """Whether this connection talks to a real browser, not a double."""
        return self._port_checker is BrowserConnection._default_port_checker

    @staticmethod
    def _default_port_checker(port: int) -> bool:
        try:
            with socket.create_connection(
                # 127.0.0.1, not "localhost" -- the debug port is bound to
                # IPv4 only, and this machine resolves "localhost" to ::1
                # first, spending the whole timeout on an address nothing
                # listens on.
                ("127.0.0.1", port), timeout=_PORT_CHECK_TIMEOUT_SECONDS,
            ):
                return True
        except OSError:
            return False
