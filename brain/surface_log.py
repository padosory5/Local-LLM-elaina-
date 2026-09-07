"""Why there were, or were not, cards -- written where it can be read later.

Twice now the answer to "I don't see anything" has been a decision the
backend made and printed to a console nobody was attached to. Elaina is
normally started by a launcher that hides its output, so by the time the
question is asked the reasoning is gone and the only way to answer is to run
it again and hope it reproduces.

The renderer's half of this already lands in ``runtime/renderer.log``. This
is the other half. Between the two, "I asked for hotels and saw nothing" is
answerable by reading two files rather than by re-running anything:

* nothing in this file about the turn -> the surface decision was never
  reached at all
* ``considering`` then ``none: ...`` -> the backend decided, and says why
* ``emitting`` here with nothing in ``renderer.log`` -> it was sent and the
  window did not get it

Truncated once per backend process, so what it holds is the current session.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.paths import RUNTIME_ROOT


PATH = Path(RUNTIME_ROOT) / "surface.log"

_opened = False


def note(line: str) -> None:
    """Print it as before, and keep a copy."""
    global _opened
    print(line)
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M:%S")
        with PATH.open("a" if _opened else "w", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {line}\n")
        _opened = True
    except OSError:
        # Logging is never allowed to be the reason a turn fails.
        pass
