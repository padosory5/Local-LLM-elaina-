"""How long before Elaina makes a sound?

Piper ran on this machine. ElevenLabs is a network call, so the question
changed from "how fast is synthesis" to "how long until the first audio
byte is playable" -- and the current path answers that badly for reasons
that have nothing to do with ElevenLabs being slow:

* ``ChatEngine`` fills ``tts_buffer`` once, **after** the whole reply is
  generated, and speaks it in one call. Nothing overlaps.
* ``ElevenLabsTTS.speak`` receives a chunk *iterator* and drains all of it
  into a temp file before playing a single byte. The streaming is already
  happening and is being thrown away.
* ``mp3_44100_128`` is four times the bytes of the format ElevenLabs
  recommends for streaming.
* ``eleven_multilingual_v2`` is their quality model, not their fast one.

This measures each of those separately, so the fix is chosen from numbers
rather than from a list of plausible causes.

It makes real API calls and spends credits -- the sentences are short and
the run is a handful of requests.

    .venv/Scripts/python.exe scripts/tts_latency_check.py
    .venv/Scripts/python.exe scripts/tts_latency_check.py --models eleven_flash_v2_5
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

sys.stdout.reconfigure(encoding="utf-8")

from config.loader import Config  # noqa: E402

# Short, and the shape of a real reply rather than a benchmark sentence.
ENGLISH = "It runs at 165Hz, and it is on offer this week."
KOREAN = "지금은 165Hz로 동작하고, 이번 주에 할인 중입니다."


def measure(client, *, voice_id, model, output_format, text, settings=None):
    """Time to the first audio chunk, and to the last."""
    request = {
        "voice_id": voice_id,
        "model_id": model,
        "text": text,
        "output_format": output_format,
    }
    if settings is not None:
        request["voice_settings"] = settings

    started = time.perf_counter()
    first = None
    total = 0
    try:
        for chunk in client.text_to_speech.convert(**request):
            if first is None:
                first = time.perf_counter() - started
            total += len(chunk)
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}
    return {
        "first_chunk": first or 0.0,
        "all_audio": time.perf_counter() - started,
        "bytes": total,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models", default="",
        help="comma-separated model ids to compare (default: config + flash)",
    )
    parser.add_argument("--korean", action="store_true")
    args = parser.parse_args()

    config = Config()
    voice_id = config.get("tts", "elevenlabs", "voice_id")
    configured_model = config.get("tts", "elevenlabs", "model")
    configured_format = config.get(
        "tts", "elevenlabs", "output_format",
        default="mp3_44100_128", required=False,
    )

    from elevenlabs.client import ElevenLabs
    client = ElevenLabs(
        api_key=config.get_env("tts", "elevenlabs", "api_key_env"),
    )

    models = (
        [name.strip() for name in args.models.split(",") if name.strip()]
        or [configured_model, "eleven_turbo_v2_5", "eleven_flash_v2_5"]
    )
    text = KOREAN if args.korean else ENGLISH

    print("=" * 70)
    print("TIME TO FIRST AUDIO")
    print(f"voice {voice_id} · {'Korean' if args.korean else 'English'} · "
          f"{len(text)} chars")
    print("=" * 70)

    print(f"\n### models (at the configured format {configured_format})\n")
    print(f"  {'model':<26} {'first chunk':>12} {'all audio':>11} {'KB':>7}")
    for model in models:
        result = measure(
            client, voice_id=voice_id, model=model,
            output_format=configured_format, text=text,
        )
        if "error" in result:
            print(f"  {model:<26} {result['error']}")
            continue
        mark = "  <- configured" if model == configured_model else ""
        print(f"  {model:<26} {result['first_chunk']:>11.2f}s "
              f"{result['all_audio']:>10.2f}s {result['bytes']/1024:>6.0f}"
              f"{mark}")

    print(f"\n### formats (on {configured_model})\n")
    print(f"  {'format':<26} {'first chunk':>12} {'all audio':>11} {'KB':>7}")
    for fmt in ("mp3_44100_128", "mp3_44100_64", "mp3_22050_32"):
        result = measure(
            client, voice_id=voice_id, model=configured_model,
            output_format=fmt, text=text,
        )
        if "error" in result:
            print(f"  {fmt:<26} {result['error']}")
            continue
        mark = "  <- configured" if fmt == configured_format else ""
        print(f"  {fmt:<26} {result['first_chunk']:>11.2f}s "
              f"{result['all_audio']:>10.2f}s {result['bytes']/1024:>6.0f}"
              f"{mark}")

    print("\n" + "=" * 70)
    print("first chunk is what she could start playing on;")
    print("all audio is what the current code waits for before playing.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
