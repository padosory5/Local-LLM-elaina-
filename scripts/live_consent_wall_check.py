"""Drive the real consent-dismissal path against real, controlled DOMs.

A live cookie wall is a poor test subject: whether one appears depends on
the site's mood, the IP, and whether Elaina's persistent profile already
stored a consent cookie from a previous visit. So these fixtures are
injected into the real controlled browser instead -- a real Chromium, real
computed styles, the real scan, and the real
BrowserControl.dismiss_privacy_overlay -- with the banner shapes that
actually show up in the wild.

What must happen:
* an explicit reject/essential-only control is clicked, and the page behind
  it becomes reachable;
* a wall offering only "Accept" is never clicked -- Elaina declines
  tracking on the user's behalf, she never agrees to it;
* a bare "Close" in ordinary page content is never touched;
* Korean consent wording works the same as English.

Usage::

    .venv/Scripts/python.exe scripts/live_consent_wall_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.browser_action_planner import BrowserActionPlanner  # noqa: E402
from config.loader import Config  # noqa: E402
from tools.browser_control.browser_connection import BrowserConnection  # noqa: E402
from tools.browser_control.browser_control import BrowserControl  # noqa: E402
from tools.browser_control.browser_observer import BrowserObserver  # noqa: E402


_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family: sans-serif; margin: 0; }}
  main {{ padding: 24px; }}
  .cookie-consent {{
    position: fixed; inset: 0; background: rgba(0,0,0,.6);
    display: flex; align-items: center; justify-content: center;
  }}
  .cookie-consent .box {{ background: #fff; padding: 24px; max-width: 420px; }}
  button {{ padding: 8px 14px; margin-right: 8px; font-size: 15px; }}
</style></head><body>
<main>
  <h1>{heading}</h1>
  <p>The real content of the page, which a consent wall hides.</p>
  <a href="https://example.com/one">First result</a>
  <a href="https://example.com/two">Second result</a>
  <button>Close</button>
</main>
{overlay}
</body></html>
"""

_OVERLAY = """
<div class="cookie-consent" id="cookie-consent-banner" {dialog_attrs}>
  <div class="box">
    <h2>We value your privacy</h2>
    <p>We and our partners store cookies to personalise content and ads.</p>
    {buttons}
  </div>
</div>
<script>
  // A real consent manager removes itself when a choice is made, and
  // BrowserControl refuses to report a dismissal until the wall is
  // actually gone -- so the fixture has to behave the same way.
  document.querySelectorAll('.cookie-consent button').forEach((button) => {{
    button.addEventListener('click', () => {{
      window.__elainaChoice = button.innerText.trim();
      document.getElementById('cookie-consent-banner').remove();
    }});
  }});
</script>
"""


def _fixture(title, heading, buttons, *, dialog=False, overlay=True):
    return _PAGE.format(
        title=title,
        heading=heading,
        overlay=_OVERLAY.format(
            buttons=buttons,
            dialog_attrs='role="dialog" aria-modal="true"' if dialog else "",
        ) if overlay else "",
    )


CASES: tuple[tuple[str, str, bool, str], ...] = (
    (
        "reject-all banner",
        _fixture(
            "Reject", "News",
            '<button>Accept all</button><button>Reject all</button>',
        ),
        True,
        "clicks 'Reject all' and frees the page",
    ),
    (
        "essential-only dialog",
        _fixture(
            "Essential", "Shop",
            '<button>Accept all cookies</button><button>Only essential</button>',
            dialog=True,
        ),
        True,
        "clicks 'Only essential' inside a role=dialog wall",
    ),
    (
        "Korean consent wall",
        _fixture(
            "동의", "쇼핑",
            '<button>모두 동의</button><button>모두 거부</button>',
        ),
        True,
        "handles Korean wording identically",
    ),
    (
        "accept-only wall",
        _fixture(
            "AcceptOnly", "Paper",
            '<button>Accept all</button><button>Manage preferences</button>',
        ),
        False,
        "NEVER agrees to tracking on the user's behalf",
    ),
    (
        "no wall at all",
        _fixture("Plain", "Plain page", "", overlay=False),
        False,
        "leaves an ordinary page (and its plain 'Close' button) alone",
    ),
)


def main() -> int:
    config = Config()
    connection = BrowserConnection(
        browser_name=str(config.get(
            "browser_control", "browser_name", default="Whale", required=False,
        )),
        debugging_port=int(config.get(
            "browser_control", "remote_debugging_port", default=9222, required=False,
        )),
    )
    observer = BrowserObserver(connection=connection)
    control = BrowserControl(observer=observer)
    # The planner owns the auto-dismissal loop; build one without running
    # its __init__ so no model client is required for a DOM-only check.
    planner = BrowserActionPlanner.__new__(BrowserActionPlanner)
    planner.observer = observer
    planner.control = control

    control.navigate(None, "https://example.com", allow_isolated_launch=True)
    page = observer.resolve_navigable_page(None)
    if page is None:
        print("Could not obtain a controlled page.")
        return 1

    failures = 0
    for name, html, should_dismiss, expectation in CASES:
        page.set_content(html)
        page.evaluate("() => { window.__elainaChoice = ''; }")
        observation = planner._describe_page(None)
        dismissed = bool(observation.dismissed_overlays)
        choice = str(page.evaluate("() => window.__elainaChoice || ''") or "")
        wall_gone = not bool(page.evaluate(
            "() => !!document.getElementById('cookie-consent-banner')"
        ))

        ok = dismissed == should_dismiss
        if should_dismiss:
            # The whole point: it must have used the reject control, and
            # the wall must actually be gone.
            ok = ok and wall_gone and bool(choice)
            ok = ok and not any(
                word in choice.casefold()
                for word in ("accept", "agree", "allow", "동의")
            )
        else:
            # Nothing may have been clicked at all.
            ok = ok and not choice
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {expectation}")
        print(
            f"         dismissed={observation.dismissed_overlays} "
            f"clicked={choice!r} wall_gone={wall_gone} "
            f"elements={len(observation.elements)}"
        )

    observer.close()
    print("\n" + "=" * 58)
    print(f"{len(CASES) - failures}/{len(CASES)} consent-wall cases passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
