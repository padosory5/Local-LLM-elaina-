"""Hammer the real browser stack against real sites, with no model in the loop.

``scripts/live_conversation_check.py`` drives the whole assistant; this one
isolates the layer underneath it, so a browser problem can be told apart
from a model problem. It answers the questions that actually decide whether
browser control is trustworthy:

* Does a cold launch reach the site every time, or does it sometimes sit on
  about:blank?
* Once there, does the scan see real, clickable controls -- on a search
  engine, a shop, a video site, a wiki, a Korean site?
* Do consent banners and modals get cleared before the scan, or do they
  hide the page?
* Is a click on a scanned id actually verified?

Usage::

    .venv/Scripts/python.exe scripts/live_browser_stress_check.py
    .venv/Scripts/python.exe scripts/live_browser_stress_check.py --repeat 5
    .venv/Scripts/python.exe scripts/live_browser_stress_check.py --site youtube

Nothing here types into a field, submits a form, or clicks anything
committing -- it navigates, scans, and clicks one ordinary link.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import Config  # noqa: E402
from tools.browser_control.browser_connection import BrowserConnection  # noqa: E402
from tools.browser_control.browser_control import BrowserControl  # noqa: E402
from tools.browser_control.browser_observer import BrowserObserver  # noqa: E402


# Deliberately varied: a search engine, a video site, a wiki, a shop, and
# Korean sites -- the point is that this works everywhere, not just on the
# booking sites the feature was first built against.
SITES: dict[str, str] = {
    "google": "https://www.google.com/search?q=best+laptops+2026",
    "youtube": "https://www.youtube.com/results?search_query=python+tutorial",
    "wikipedia": "https://en.wikipedia.org/wiki/Seoul",
    "naver": "https://search.naver.com/search.naver?query=%EB%85%B8%ED%8A%B8%EB%B6%81",
    "danawa": "https://search.danawa.com/dsearch.php?query=%EB%85%B8%ED%8A%B8%EB%B6%81",
    "bbc": "https://www.bbc.com/news",
    "amazon": "https://www.amazon.com/s?k=usb+c+cable",
}


def _report(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    return ok


def check_site(control: BrowserControl, observer: BrowserObserver, name: str, url: str) -> bool:
    print(f"\n=== {name} :: {url}")
    started = time.perf_counter()
    navigation = control.navigate(None, url, allow_isolated_launch=True)
    navigated_in = time.perf_counter() - started
    if not _report(
        "navigate",
        navigation.status == "navigated",
        f"{navigation.status} in {navigated_in:.1f}s -- {navigation.message[:90]}",
    ):
        return False

    landed = str(navigation.url or "")
    if not _report(
        "left about:blank",
        bool(landed) and not landed.startswith("about:"),
        f"landed on {landed[:80]}",
    ):
        return False

    scan_started = time.perf_counter()
    observation = observer.describe_page()
    scanned_in = time.perf_counter() - scan_started
    if not _report(
        "scan",
        observation.status == "observed",
        f"{observation.status} in {scanned_in:.1f}s",
    ):
        return False

    _report(
        "elements found",
        len(observation.elements) >= 5,
        f"{len(observation.elements)} interactive elements",
    )
    if observation.dismissed_overlays:
        print(f"  [INFO] dismissed overlays: {observation.dismissed_overlays}")
    if observation.blocking_dialog:
        print("  [INFO] a dialog is still open; its controls are listed first")

    text = observer.read_text()
    _report(
        "readable text",
        getattr(text, "status", "") == "observed" and len(getattr(text, "text", "")) > 200,
        f"{len(getattr(text, 'text', ''))} characters",
    )

    # One ordinary, non-committing link click, to prove a scanned id is
    # actually actionable rather than merely listed.
    link = next(
        (
            element
            for element in observation.elements
            if element.tag == "a" and not element.is_ad and element.label
        ),
        None,
    )
    if link is None:
        print("  [INFO] no ordinary link to click on this page; skipping click")
        return True
    click = control.click(
        observation.tab_index, link.id,
        expected_label=link.label,
        expected_url=observation.url,
        expected_scan_id=observation.scan_id,
        expected_href=link.href,
    )
    ok = click.status in {"clicked", "confirmation_required"}
    _report(
        "click a scanned link",
        ok,
        f"{click.status} on {link.label[:40]!r}",
    )
    print(f"  [TIME] total {time.perf_counter() - started:.1f}s")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site", action="append", default=[], choices=sorted(SITES),
        help="Limit to one or more sites; may be repeated.",
    )
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="Run the whole sweep N times, to catch intermittent failures.",
    )
    args = parser.parse_args()

    config = Config()
    connection = BrowserConnection(
        browser_name=str(config.get(
            "browser_control", "browser_name", default="Whale", required=False,
        )),
        debugging_port=int(config.get(
            "browser_control", "remote_debugging_port", default=9222, required=False,
        )),
        user_data_dir=str(config.get(
            "browser_control", "user_data_dir", default="", required=False,
        )) or None,
    )
    observer = BrowserObserver(connection=connection)
    control = BrowserControl(observer=observer)

    selected = args.site or list(SITES)
    failures: list[str] = []
    for round_index in range(1, max(1, args.repeat) + 1):
        if args.repeat > 1:
            print(f"\n############ round {round_index}/{args.repeat}")
        for name in selected:
            try:
                if not check_site(control, observer, name, SITES[name]):
                    failures.append(f"{name} (round {round_index})")
            except Exception as error:
                print(f"  [FAIL] raised {type(error).__name__}: {error}")
                failures.append(f"{name} (round {round_index})")

    print("\n" + "=" * 60)
    total = len(selected) * max(1, args.repeat)
    print(f"{total - len(failures)}/{total} site checks passed.")
    if failures:
        print("Failed: " + ", ".join(failures))
    observer.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
