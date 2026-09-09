"""Drive natural, unscripted-feeling conversations through the real Elaina.

Unlike ``live_conversation_check.py``, which replays specific failure
transcripts to prove a defect is gone, this one exists to *measure how she
sounds*. The turns are ordinary: a greeting, a complaint about work, a
follow-up with no subject in it, an acknowledgement, a topic change, a
couple of tool-shaped requests mixed in among them.

The point of mixing them is the measurement. Conversation quality is only
visible as a whole -- a reply that reads fine on its own is still a failure
if it sounds like a different person from the one who answered two turns
earlier -- so the arcs deliberately cross from chat into tool use and back.

Each run writes a JSON transcript that ``conversation_quality_report.py``
scores. Same turns before and after a change; the numbers are comparable
because the input is identical.

Usage::

    .venv/Scripts/python.exe scripts/live_dogfood_conversation.py \
        --arc everyday --out runtime/a1_baseline_everyday.json

Start the backend first, without the Electron window::

    ELAINA_OPEN_DESKTOP=0 .venv/Scripts/python.exe main.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The Windows console here is cp949, and a reply carrying an emoji or a
# stray variation selector kills the run on the print rather than on
# anything that matters. Measuring what she said must never depend on what
# the terminal can render.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - older stream
        pass

from brain.conversation_style import (  # noqa: E402
    ANSWER, CLOSE, GREET, REACT, RECEIPT, REPORT,
)

try:
    import websockets
except ImportError:  # pragma: no cover - operator-facing message
    raise SystemExit("pip install websockets to run this check")


DEFAULT_URL = "ws://127.0.0.1:8765"


# Each arc is one conversation, in order, in one process. Restart the
# backend between arcs: history lives in the running ChatEngine, and a
# repetition or greeting-variety fault is invisible inside a session that
# has already warmed up.
#
# Each turn also declares the conversational act its *reply* should
# perform. That is a statement about the conversation, not about the
# implementation -- a reply to "thanks" is a receipt whoever writes it -- so
# the same table scores a run from before a change and a run from after it.
#
# ``computer_control`` says whether the arc needs the desktop switch on.
# Only read-only requests are ever sent with it enabled -- this script runs
# against the operator's real machine.
ARCS: dict[str, dict] = {
    "everyday": {
        "computer_control": False,
        "turns": (
            ("hey", GREET),
            ("not much, just got back from work", REACT),
            ("kind of tired honestly", REACT),
            ("yeah it was a long one. what's 15% of 84?", ANSWER),
            ("thanks", RECEIPT),
            ("what time is it in london right now?", ANSWER),
            ("cool. do you know anything about making cold brew?", ANSWER),
            ("how long should it steep?", ANSWER),
            ("ok i'll try that", RECEIPT),
            ("actually forget the coffee, what's a good movie to watch tonight?",
             ANSWER),
            ("something lighter", ANSWER),
            ("nah", RECEIPT),
        ),
    },
    "tooling": {
        "computer_control": True,
        "turns": (
            ("hey can you check what the weather is like in seoul today", ANSWER),
            ("ok", RECEIPT),
            ("can you control my browser?", ANSWER),
            ("what can you do?", ANSWER),
            ("find me a good wireless mouse under 50 dollars", ANSWER),
            ("the second one", ANSWER),
            ("hmm what else is there", ANSWER),
            ("never mind, what windows do i have open right now?", REPORT),
            ("thanks", RECEIPT),
            ("actually back to the mouse thing", ANSWER),
            ("ok", RECEIPT),
            ("that's fine, thanks", RECEIPT),
        ),
    },
    "korean": {
        "computer_control": False,
        "turns": (
            ("안녕", GREET),
            ("방금 퇴근했어", REACT),
            ("좀 피곤하네", REACT),
            ("오늘 길었어. 84의 15%가 얼마야?", ANSWER),
            ("고마워", RECEIPT),
            ("지금 런던 몇 시야?", ANSWER),
            ("콜드브루 만드는 법 알아?", ANSWER),
            ("얼마나 우려야 돼?", ANSWER),
            ("알겠어 해볼게", RECEIPT),
            ("커피는 됐고, 오늘 볼 만한 영화 뭐 있어?", ANSWER),
            ("좀 더 가벼운 걸로", ANSWER),
            ("아니야", RECEIPT),
        ),
    },
    # Not a conversation so much as the switch rule, said out loud. Each
    # turn is here because it is a case the rule has to get right, and the
    # two "stays put" turns matter most: flipping on a one-word
    # interjection is the failure this whole design is built to avoid.
    "mixed": {
        "computer_control": False,
        "turns": (
            ("hey, can you check the weather in seoul today", ANSWER),
            ("고마워", RECEIPT),
            ("그 monitor 어때? 50달러 이하로 찾아줘", ANSWER),
            ("ok", RECEIPT),
            ("speak english please", RECEIPT),
            ("오늘 날씨 어때?", ANSWER),
            ("한국어로 말해줘", RECEIPT),
            ("고마워", RECEIPT),
        ),
    },
    "social": {
        "computer_control": False,
        "turns": (
            ("morning", GREET),
            ("i had a rough night", REACT),
            ("no", RECEIPT),
            ("just couldn't sleep", REACT),
            ("yeah", RECEIPT),
            ("what's 2+2", ANSWER),
            ("lol", RECEIPT),
            ("alright i'm heading out", CLOSE),
        ),
    },
    # ---------------------------------------------------------------- day 2
    #
    # Turns nothing has been tuned against. The five arcs above were the
    # optimisation target for six phases, so re-running them measures how
    # well the tuning fits them rather than how she actually is -- which is
    # the whole reason dogfooding session 1 found things nine benchmark
    # phases had not. These are written as someone would really talk, and
    # deliberately include the shapes known to be weak: subjectless
    # follow-ups, a correction, a topic abandoned mid-arc, and questions
    # that invite her to invent a number.
    "evening": {
        "computer_control": False,
        "turns": (
            ("hey, long day", GREET),
            ("just back to back meetings honestly", REACT),
            ("do you know why someone would keep waking up at 3am?", ANSWER),
            ("i don't drink coffee after 2 though", REACT),
            ("hm", RECEIPT),
            ("anyway. decent cheap headphone brand?", ANSWER),
            ("under 100", ANSWER),
            ("what's the battery life on that one", ANSWER),
            ("you sure about that?", ANSWER),
            ("ok forget the headphones, is it supposed to rain tomorrow?", ANSWER),
            ("right", RECEIPT),
            ("night", CLOSE),
        ),
    },
    "korean_day": {
        "computer_control": False,
        "turns": (
            ("안녕", GREET),
            ("오늘 좀 힘들었어", REACT),
            ("회사에서 하루 종일 회의만 했어", REACT),
            ("그러게 말이야", RECEIPT),
            ("요즘 볼만한 드라마 있어?", ANSWER),
            ("음 별로네", REACT),
            ("그럼 영화는?", ANSWER),
            ("그건 봤어", REACT),
            ("드라마는 됐고, 저녁 뭐 먹지?", ANSWER),
            ("간단한 걸로", ANSWER),
            ("오케이 그렇게 할게", RECEIPT),
            ("고마워 잘자", CLOSE),
        ),
    },
    # Code-switching in both directions, plus an explicit switch mid-arc.
    "switch": {
        "computer_control": False,
        "turns": (
            ("hey 오늘 날씨 어때?", ANSWER),
            ("아 그렇구나", RECEIPT),
            ("can you say that in english?", ANSWER),
            ("thanks. 근데 우산 필요할까?", ANSWER),
            ("ok got it", RECEIPT),
            ("let's speak korean from now on", RECEIPT),
            ("저녁에 산책 가도 될까?", ANSWER),
            ("알겠어", RECEIPT),
        ),
    },
    # A7, said the way a person says it rather than the way a matrix does.
    "personal": {
        "computer_control": False,
        "turns": (
            ("i'm vegetarian by the way", RECEIPT),
            ("since last year", REACT),
            ("what do you know about me?", ANSWER),
            ("can you find somewhere near me to eat?", ANSWER),
            ("forget that i'm vegetarian", RECEIPT),
            ("what do you know about me now?", ANSWER),
            ("ok thanks", RECEIPT),
        ),
    },
    # Pushback. She is wrong, is told so, and has to neither cave nor dig in.
    "pushback": {
        "computer_control": False,
        "turns": (
            ("how much does a nintendo switch 2 cost?", ANSWER),
            ("for real? that seems expensive", REACT),
            ("no i think you're wrong about that", ANSWER),
            ("ok whatever", RECEIPT),
            ("what's the screen refresh rate on it", ANSWER),
            ("you sure?", ANSWER),
            ("alright", RECEIPT),
        ),
    },
}


async def run(url: str, arc_name: str, timeout: float) -> dict:
    arc = ARCS[arc_name]
    records: list[dict] = []
    async with websockets.connect(url, max_size=None) as socket:
        await socket.send(json.dumps({"command": "set_input_mode", "mode": "text"}))
        await socket.send(json.dumps({
            "command": "set_computer_control_mode",
            "enabled": bool(arc["computer_control"]),
        }))
        await asyncio.sleep(1.0)

        for index, (message, act) in enumerate(arc["turns"], start=1):
            print("\n" + "=" * 72)
            print(f"[{arc_name} {index}/{len(arc['turns'])}] USER: {message}")
            print("-" * 72)
            started = time.perf_counter()
            await socket.send(
                json.dumps({"command": "send_text_message", "text": message})
            )
            reply, statuses = await _await_reply(socket, timeout)
            elapsed = time.perf_counter() - started
            if reply is None:
                print(f"ELAINA: (no reply within {timeout:.0f}s)")
            else:
                print(f"ELAINA ({elapsed:.1f}s): {reply}")
            records.append({
                "turn": index,
                "arc": arc_name,
                "user": message,
                "act": act,
                "reply": reply,
                "statuses": statuses,
                "seconds": round(elapsed, 2),
            })
    return {"arc": arc_name, "turns": records}


async def _await_reply(socket, timeout: float):
    deadline = time.monotonic() + timeout
    statuses: list[str] = []
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None, statuses
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        event = payload.get("event")
        if event == "assistant_status":
            text = str(payload.get("text", ""))
            statuses.append(text)
            print(f"   ... {text}")
        elif event == "assistant_finished":
            return str(payload.get("text", "")), statuses
    return None, statuses


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--arc", default="everyday", choices=sorted(ARCS))
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    transcript = asyncio.run(run(args.url, args.arc, args.timeout))
    missing = sum(1 for turn in transcript["turns"] if not turn["reply"])
    print("\n" + "=" * 72)
    print(f"Arc {args.arc}: {len(transcript['turns'])} turn(s), "
          f"{missing} missing reply/replies.")
    if args.out:
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(transcript, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Wrote {destination}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
