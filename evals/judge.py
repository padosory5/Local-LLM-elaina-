"""Grade the rubric properties of a run's replies with a separate model.

    .venv/Scripts/python.exe -m evals.judge --run runtime/evals/baseline-1

The judge is never the model being judged. The default is the local 27B,
which reads both languages and is not the qwen3:8b that wrote the replies;
it is loaded after the run is over, so the two never compete for the card.
Each scored turn is one call: the conversation so far, what the person
said, the reply, and the properties that apply, each with its definition
from evals/rubric.json. The judge sees the displayed reply -- what the
person reads -- and nothing about how it was produced.

How far to trust it is a measurement, not an assumption: evals.calibration
compares its verdicts with a person's.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ollama  # noqa: E402

from config.loader import Config  # noqa: E402
from evals import corpus  # noqa: E402


# Not any model under test. The 27B that judged Phase 1 is a Phase 2
# contestant, so the judge is this one, calibrated against the same human
# labels (docs/EVAL_RUBRIC.md).
DEFAULT_JUDGE = "qwen3.6:35b-a3b"
PREVIOUS_JUDGE = "hf.co/jrell/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller:latest"

SYSTEM = (
    "You grade single replies from Elaina, a voice assistant, against named "
    "properties. Apply each property's definition exactly as written, "
    "including its examples of yes and no: answer \"yes\" when the reply "
    "meets it and \"no\" when it does not. A property marked 'n/a allowed' "
    "may be answered \"n/a\", but only in the situation its note states; "
    "every other property must be yes or no. Judge the reply as the person "
    "would read it, in the context of the conversation. Longer is not "
    "better. The reply may be in English or Korean; the properties apply the "
    "same way to both.\n\n"
    "Return one JSON object mapping every property id you are given to "
    "{\"why\": \"one short sentence\", \"verdict\": \"yes\", \"no\" or "
    "\"n/a\"} -- the reason first, then the verdict it leads to. Nothing "
    "else."
)


def prompt(history: list[tuple[str, str]], said: str, reply: str,
           properties: dict[str, dict]) -> str:
    parts = []
    if history:
        lines = []
        for person, elaina in history:
            lines.append(f"Person: {person}")
            lines.append(f"Elaina: {elaina}")
        parts.append("CONVERSATION SO FAR\n" + "\n".join(lines))
    parts.append(f"THE PERSON SAID\n{said}")
    parts.append(f"ELAINA'S REPLY\n{reply}")
    parts.append("PROPERTIES\n" + "\n".join(
        f"- {name}: {spec['definition']}"
        + (f" (n/a allowed: {spec['allows_na']})" if spec.get("allows_na") else "")
        for name, spec in properties.items()
    ))
    return "\n\n".join(parts)


def _parse(content: str, wanted: dict[str, dict]) -> dict | None:
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return None
    verdicts = {}
    for name, spec in wanted.items():
        item = data.get(name) if isinstance(data, dict) else None
        if not isinstance(item, dict):
            return None
        verdict = str(item.get("verdict", "")).strip().lower()
        allowed = {"yes", "no", "n/a"} if spec.get("allows_na") else {"yes", "no"}
        if verdict not in allowed:
            return None
        verdicts[name] = {"verdict": verdict, "why": str(item.get("why", ""))[:300]}
    return verdicts


def schema(properties: dict[str, dict]) -> dict:
    """The exact shape of an answer, enforced by Ollama rather than asked for.

    Plain JSON mode was enough for the 27B; the 35B answered it with its own
    structure -- a nested "verdict" object, three of five properties
    missing. A schema makes the shape a constraint: every property present,
    a reason before its verdict, and n/a only where the rubric allows it.
    """
    return {
        "type": "object",
        "properties": {
            name: {
                "type": "object",
                "properties": {
                    "why": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["yes", "no", "n/a"] if spec.get("allows_na") else ["yes", "no"],
                    },
                },
                "required": ["why", "verdict"],
            }
            for name, spec in properties.items()
        },
        "required": list(properties),
    }


def judge_turn(client, model: str, history, said: str, reply: str,
               properties: dict[str, dict]) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt(history, said, reply, properties)},
    ]
    problem = "the judge did not return valid JSON"
    for attempt in range(4):
        try:
            response = client.chat(
                model=model, messages=messages, format=schema(properties),
                think=False,
                # Room for nine properties with reasons in Korean: at 600
                # tokens one such turn was cut off mid-JSON on every attempt.
                options={"temperature": 0, "num_predict": 1500}, keep_alive="15m",
            )
        except Exception as error:
            # Ollama restarts under us -- it updates itself, and a large
            # model can be evicted mid-call. Wait and ask again rather than
            # losing a whole run's judging to one dropped connection.
            problem = f"{type(error).__name__}: {error}"[:200]
            print(f"  judge call failed ({problem}); retrying", flush=True)
            time.sleep(10 * (attempt + 1))
            continue
        verdicts = _parse(response["message"]["content"], properties)
        if verdicts is not None:
            return verdicts
    return {name: {"verdict": "error", "why": problem} for name in properties}


CACHE = PROJECT_ROOT / "runtime" / "evals" / "judge_cache.jsonl"


def _cache_key(model: str, fingerprint: str, history, said: str, reply: str,
               names) -> str:
    """Everything a verdict depends on. At temperature 0 the same input gets
    the same verdict, and two runs of one arm repeat most of their replies."""
    import hashlib

    payload = json.dumps([model, fingerprint, history, said, reply, sorted(names)],
                         ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    cache = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            cache[item["key"]] = item["verdicts"]
    return cache


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, nargs="+", help="run directories")
    parser.add_argument("--model", default=DEFAULT_JUDGE)
    args = parser.parse_args()

    specs = corpus.rubric()
    version = corpus.rubric_version()
    fingerprint = corpus.rubric_fingerprint()
    scenarios = {s.id: s for s in corpus.load(corpus.ALL_SUITES)}
    host = Config().get("llm", "ollama", "base_url")
    client = ollama.Client(host=host)
    cache = _load_cache()

    for run in args.run:
        run_dir = Path(run)
        results = [
            json.loads(line)
            for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        judged_path = run_dir / "judged.jsonl"
        done = set()
        if judged_path.exists():
            for line in judged_path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                # Done means done by this judge under this wording.
                if (item.get("rubric_fingerprint") == fingerprint
                        and item.get("judge_model") == args.model):
                    done.add((item["scenario"], item["index"]))
        count = 0
        reused = 0
        started = time.monotonic()
        for result in results:
            scenario = scenarios.get(result["scenario"])
            history: list[tuple[str, str]] = []
            for turn in result["turns"]:
                key = (result["scenario"], turn["index"])
                # The properties come from the corpus as it is now -- the
                # turn's mode and the rubric's applicability -- not from the
                # list stored when the run was made.
                wanted = (
                    scenario.turns[turn["index"]]
                    if scenario and turn["index"] < len(scenario.turns) else None
                )
                if wanted is not None and wanted.score and wanted.rubric and key not in done:
                    properties = {name: specs[name] for name in wanted.rubric}
                    if turn["display"] is None:
                        verdicts = {name: {"verdict": "no", "why": "no reply"}
                                    for name in properties}
                        seconds = 0.0
                    else:
                        began = time.monotonic()
                        cache_key = _cache_key(args.model, fingerprint, history,
                                               turn["said"], turn["display"], properties)
                        verdicts = cache.get(cache_key)
                        if verdicts is None:
                            verdicts = judge_turn(
                                client, args.model, history, turn["said"],
                                turn["display"], properties,
                            )
                            if not any(v["verdict"] == "error" for v in verdicts.values()):
                                cache[cache_key] = verdicts
                                CACHE.parent.mkdir(parents=True, exist_ok=True)
                                with CACHE.open("a", encoding="utf-8") as handle:
                                    handle.write(json.dumps(
                                        {"key": cache_key, "verdicts": verdicts},
                                        ensure_ascii=False) + "\n")
                        else:
                            reused += 1
                        seconds = round(time.monotonic() - began, 2)
                    with judged_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({
                            "scenario": result["scenario"], "index": turn["index"],
                            "rubric_version": version,
                            "rubric_fingerprint": fingerprint,
                            "judge_model": args.model, "seconds": seconds,
                            "verdicts": verdicts,
                        }, ensure_ascii=False) + "\n")
                    count += 1
                history.append((turn["said"], turn["display"] or ""))
        print(f"{run_dir}: judged {count} turn(s) in {time.monotonic() - started:.0f}s "
              f"({reused} verdict sets reused from identical earlier input)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
