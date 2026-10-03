"""How far the judge can be trusted: its verdicts against a person's.

    # 1. pick replies for a person to label (the judge's verdicts are not shown)
    .venv/Scripts/python.exe -m evals.calibration export \
        --runs runtime/evals/baseline-1 runtime/evals/baseline-2 runtime/evals/baseline-3 \
        --out runtime/evals/calibration-v2.json

    # 2. set every label in that file to "yes" or "no" ("n/a" only where offered)

    # 3. compare
    .venv/Scripts/python.exe -m evals.calibration score --file runtime/evals/calibration-v2.json

**Sampling.** A property scored on two or three labels has no measurable
agreement, which is most of what version 1's calibration could not say.
Each property now gets a quota of labels, drawn so that the judge said yes
to some and no to others -- the verdicts are not in the file, but without
both kinds a person and a judge who always agree on "yes" look perfect.
One scenario turn is used at most once across runs (the runs' replies are
nearly identical), and labels are packed onto as few replies as possible,
so a person reads less to label more.

A property whose agreement stays low is not a measurement yet: either its
definition in evals/rubric.json is ambiguous or the judge cannot apply it,
and it should not decide anything until it is fixed and scored again.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import corpus  # noqa: E402
from evals.report import load_run  # noqa: E402
from evals.judge import DEFAULT_JUDGE  # noqa: E402

# Labels per property: the ones a Phase 2 claim about explanation quality
# would rest on get more.
CORE_QUOTA = 7
MINOR_QUOTA = 4
MINOR = {"adapts_to_confusion", "notation_earned", "no_unrelated_remark"}
_LABELS = {"y": "yes", "yes": "yes", "n": "no", "no": "no",
           "na": "n/a", "n/a": "n/a", "not applicable": "n/a"}


def _pool(run_dirs: list[Path]):
    """Every judged (turn, property) the calibration could ask about."""
    scenarios = {s.id: s for s in corpus.load(corpus.ALL_SUITES)}
    pool = []
    for run_dir in run_dirs:
        run = load_run(run_dir)
        for result in run["results"]:
            scenario = scenarios.get(result["scenario"])
            if scenario is None:
                continue
            history = []
            for turn in result["turns"]:
                index = turn["index"]
                wanted = scenario.turns[index] if index < len(scenario.turns) else None
                judged = run["judged"].get((result["scenario"], index), {}).get("verdicts", {})
                if wanted is not None and wanted.score and turn["display"] and judged:
                    for name in wanted.rubric:
                        verdict = judged.get(name, {}).get("verdict")
                        if verdict in ("yes", "no", "n/a"):
                            pool.append({
                                "run": str(run_dir), "scenario": result["scenario"],
                                "suite": result["suite"], "index": index,
                                "property": name, "judge": verdict,
                                "history": list(history), "person": turn["said"],
                                "elaina": turn["display"],
                            })
                history.append({"person": turn["said"], "elaina": turn["display"] or ""})
    return pool


def _examples(definition: str) -> list[str]:
    """The quoted example utterances inside a definition."""
    import re

    found = []
    for example in re.findall(r"'(.+?)'(?=[\s.,;:)]|$)", definition):
        if len(example.split()) >= 3 or example[-1:] in ".?!…":
            found.append(example)
    return found


def _resembles(reply: str, example: str) -> bool:
    """Whether a sentence of the reply is close to a definition's example."""
    import re

    wanted = set(re.findall(r"\w+", example.casefold()))
    if len(wanted) < 2:
        return False
    for sentence in re.split(r"(?<=[.!?])\s+", reply or ""):
        words = set(re.findall(r"\w+", sentence.casefold()))
        if words and len(wanted & words) / len(wanted | words) >= 0.6:
            return True
    return False


def _used_before(paths: list[Path]) -> set[tuple]:
    used = set()
    for path in paths:
        if path.exists():
            for item in json.loads(path.read_text(encoding="utf-8"))["items"]:
                used.add((item["scenario"], item["index"]))
    return used


def export(run_dirs: list[Path], out: Path, seed: int, properties=None,
           quotas: dict[str, int] | None = None, avoid: set | None = None) -> Path:
    """Write a blind calibration file.

    ``properties`` limits it to the properties being re-calibrated;
    ``quotas`` overrides how many labels each gets; ``avoid`` is the scenario
    turns an earlier calibration already showed a person, used only when a
    property has nothing else left -- a reply seen with someone's labels
    beside it cannot be labelled blind again.
    """
    rng = random.Random(seed)
    pool = _pool(run_dirs)
    rng.shuffle(pool)
    specs = corpus.rubric()
    avoid = avoid or set()
    if properties:
        pool = [entry for entry in pool if entry["property"] in properties]

    # One reply per scenario turn: the first run that has it, after shuffling.
    chosen_run: dict[tuple, str] = {}
    for entry in pool:
        chosen_run.setdefault((entry["scenario"], entry["index"]), entry["run"])
    pool = [e for e in pool if chosen_run[(e["scenario"], e["index"])] == e["run"]]

    selected: dict[tuple, dict] = {}      # (scenario, index) -> item
    picks: list[tuple[tuple, str]] = []
    # Scarce properties first, so their few candidates are not spent elsewhere.
    by_property = defaultdict(list)
    for entry in pool:
        by_property[entry["property"]].append(entry)
    order = sorted(by_property, key=lambda name: len(by_property[name]))
    for name in order:
        quota = (quotas or {}).get(name, MINOR_QUOTA if name in MINOR else CORE_QUOTA)
        candidates = by_property[name]
        classes = defaultdict(list)
        for entry in candidates:
            classes[entry["judge"]].append(entry)
        for bucket in classes.values():
            # Replies no earlier calibration showed first; among those, the
            # ones already being read for another property.
            bucket.sort(key=lambda e: (
                (e["scenario"], e["index"]) in avoid,
                (e["scenario"], e["index"]) not in selected,
            ))
        taken: list[dict] = []
        # Alternate between the judge's verdict classes until the quota is met.
        while len(taken) < quota and any(classes.values()):
            for verdict in ("no", "yes", "n/a"):
                if classes.get(verdict) and len(taken) < quota:
                    taken.append(classes[verdict].pop(0))
        for entry in taken:
            key = (entry["scenario"], entry["index"])
            if key not in selected:
                selected[key] = {
                    "run": entry["run"], "scenario": entry["scenario"],
                    "suite": entry["suite"], "index": entry["index"],
                    "conversation": entry["history"], "person": entry["person"],
                    "elaina": entry["elaina"], "labels": {}, "definitions": {},
                    "allowed": {},
                }
            item = selected[key]
            item["labels"][name] = ""
            item["definitions"][name] = specs[name]["definition"] + (
                f" (Answer n/a if {specs[name]['allows_na']}.)" if specs[name].get("allows_na") else ""
            )
            item["allowed"][name] = ["yes", "no", "n/a"] if specs[name].get("allows_na") else ["yes", "no"]
            picks.append((key, name))

    items = list(selected.values())
    rng.shuffle(items)
    for number, item in enumerate(items, 1):
        item["item"] = number
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "rubric_version": corpus.rubric_version(),
        "rubric_fingerprint": corpus.rubric_fingerprint(),
        "instructions": (
            "For each item, read the conversation, what the person said and "
            "Elaina's reply. Set every label to \"yes\" or \"no\" -- or \"n/a\" "
            "where 'allowed' offers it -- by the definition next to it, "
            "including its examples. Judge the reply as the person would. "
            "Correctness of times, dates, arithmetic and units is checked "
            "separately and is not one of these labels. The judge's own "
            "verdicts are deliberately not in this file."
        ),
        "items": items,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    counts = Counter(name for _, name in picks)
    reused = sum((item["scenario"], item["index"]) in avoid for item in items)
    print(f"Wrote {out}: {len(items)} replies, {len(picks)} labels to fill in; "
          f"{reused} of the replies were in an earlier calibration.")
    for name in specs:
        if counts.get(name):
            print(f"  {name:<24} {counts[name]}")
    for item in items:
        for name in item["labels"]:
            for example in _examples(specs[name]["definition"]):
                if _resembles(item["elaina"], example):
                    print(f"  WARNING: item {item['item']}'s reply resembles the "
                          f"'{name}' example {example!r}")

    # The same items, to read. No labels and no verdicts, so it can be
    # labelled blind; the labels go into the JSON file.
    lines = [
        "# Calibration -- items to label",
        "",
        f"Rubric version {corpus.rubric_version()}. Label each property in "
        f"`{out.name}` (the `labels` field) as yes or no -- n/a only where "
        "offered -- by its definition below.",
        "",
    ]
    for item in items:
        lines.append(f"## #{item['item']}")
        lines.append("")
        for earlier in item["conversation"]:
            lines.append(f"> *earlier* — **Person:** {earlier['person']}  ")
            lines.append(f"> **Elaina:** {earlier['elaina']}")
            lines.append(">")
        lines.append(f"> **Person:** {item['person']}  ")
        lines.append(f"> **Elaina:** {item['elaina']}")
        lines.append("")
        for name in item["labels"]:
            lines.append(f"- **{name}** ({' / '.join(item['allowed'][name])}): "
                         f"{item['definitions'][name]}")
        lines.append("")
    sheet = out.with_name(out.stem + "-sheet.md")
    sheet.write_text("\n".join(lines), encoding="utf-8")
    print(f"Reading sheet: {sheet}")
    return out


def _kappa(pairs) -> float | None:
    """Cohen's kappa over any set of categories; None when undefined."""
    total = len(pairs)
    if not total:
        return None
    categories = {a for a, _ in pairs} | {b for _, b in pairs}
    agree = sum(a == b for a, b in pairs) / total
    chance = sum(
        (sum(a == c for a, _ in pairs) / total) * (sum(b == c for _, b in pairs) / total)
        for c in categories
    )
    if chance >= 1:
        return None
    return (agree - chance) / (1 - chance)


def score(path: Path, judge: str | None = None) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("rubric_fingerprint") != corpus.rubric_fingerprint():
        print("The rubric's wording has changed since this file was made. The "
              "judge's verdicts under the current wording are compared with "
              "labels given under the old one; export a new file instead.")
        return 1
    runs: dict[str, dict] = {}
    by_property = defaultdict(list)
    disagreements = []
    missing = 0
    for item in data["items"]:
        run_dir = item["run"]
        if run_dir not in runs:
            runs[run_dir] = load_run(Path(run_dir), judge)
        verdicts = runs[run_dir]["judged"].get((item["scenario"], item["index"]), {}).get("verdicts", {})
        for name, label in item["labels"].items():
            label = _LABELS.get(str(label).strip().lower(), "")
            if not label:
                missing += 1
                continue
            # Not "judge": that is the model whose verdicts are read, and
            # reusing the name made every run after the first load under a
            # judge called "yes" or "no" -- with no verdicts at all.
            given = verdicts.get(name, {}).get("verdict", "")
            by_property[name].append((label, given))
            if given != label:
                disagreements.append((item["item"], item["scenario"], name, label, given,
                                      verdicts.get(name, {}).get("why", "")))
    if missing:
        print(f"{missing} label(s) not filled in yet; scoring the rest.")
    everything = [pair for pairs in by_property.values() for pair in pairs]
    if not everything:
        print("No labels yet.")
        return 1
    agree = sum(a == b for a, b in everything) / len(everything)
    kappa = _kappa(everything)
    print(f"Overall: {agree:.0%} agreement on {len(everything)} labels"
          + (f", kappa {kappa:.2f}" if kappa is not None else ""))
    for name in corpus.rubric():
        pairs = by_property.get(name)
        if not pairs:
            continue
        rate = sum(a == b for a, b in pairs) / len(pairs)
        k = _kappa(pairs)
        person = Counter(a for a, _ in pairs)
        print(f"  {name:<24} {rate:>4.0%} of {len(pairs):>2}"
              + (f"  kappa {k:5.2f}" if k is not None else "              ")
              + f"   person: {dict(person)}")
    if disagreements:
        print("\nDisagreements (item, scenario, property, person, judge, judge's reason):")
        for row in disagreements:
            print(f"  #{row[0]} {row[1]} {row[2]}: person {row[3]}, judge {row[4]} -- {row[5]}")
    path.with_name(path.stem + "_result.json").write_text(json.dumps({
        "agreement": agree, "kappa": kappa, "labels": len(everything),
        "by_property": {name: {"agreement": sum(a == b for a, b in pairs) / len(pairs),
                               "kappa": _kappa(pairs), "labels": len(pairs)}
                        for name, pairs in by_property.items()},
    }, indent=1), encoding="utf-8")
    return 0


def _current(definition: str) -> str:
    """A stored definition without the n/a note export appends to it."""
    return definition.split(" (Answer n/a if ")[0].strip()


def judge_labels(paths: list[Path], model: str) -> int:
    """Run a judge on labelled items directly, and compare it with the labels.

    This is how a judge that did not judge the runs is calibrated: the
    person's labels are ground truth whoever the judge is, so a new judge
    costs no new labelling. Only properties whose definition in the file is
    still the rubric's current wording are used -- a label given under
    other words is a label for a different question.
    """
    import ollama

    from config.loader import Config
    from evals.judge import judge_turn

    specs = corpus.rubric()
    client = ollama.Client(host=Config().get("llm", "ollama", "base_url"))
    by_property = defaultdict(list)
    disagreements = []
    skipped = Counter()
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data["items"]:
            wanted = {}
            for name, label in item["labels"].items():
                label = _LABELS.get(str(label).strip().lower(), "")
                same_words = (
                    name in specs
                    and _current(item["definitions"][name]) == specs[name]["definition"]
                )
                if not label or not same_words:
                    skipped[name] += 1
                    continue
                wanted[name] = label
            if not wanted:
                continue
            history = [(turn["person"], turn["elaina"]) for turn in item["conversation"]]
            verdicts = judge_turn(
                client, model, history, item["person"], item["elaina"],
                {name: specs[name] for name in wanted},
            )
            for name, label in wanted.items():
                verdict = verdicts[name]["verdict"]
                by_property[name].append((label, verdict))
                if verdict != label:
                    disagreements.append((path.name, item["item"], item["scenario"], name,
                                          label, verdict, verdicts[name]["why"]))
    everything = [pair for pairs in by_property.values() for pair in pairs]
    if not everything:
        print("Nothing to compare.")
        return 1
    agree = sum(a == b for a, b in everything) / len(everything)
    kappa = _kappa(everything)
    print(f"Judge {model}: {agree:.0%} agreement with the labels on "
          f"{len(everything)} labels" + (f", kappa {kappa:.2f}" if kappa is not None else ""))
    if skipped:
        print("Left out (label given under older wording): "
              + ", ".join(f"{name} {count}" for name, count in skipped.items()))
    for name in specs:
        pairs = by_property.get(name)
        if not pairs:
            continue
        rate = sum(a == b for a, b in pairs) / len(pairs)
        k = _kappa(pairs)
        print(f"  {name:<24} {rate:>4.0%} of {len(pairs):>2}"
              + (f"  kappa {k:5.2f}" if k is not None else ""))
    if disagreements:
        print()
        print("Disagreements (file, item, scenario, property, label, judge, judge's reason):")
        for row in disagreements:
            print(f"  {row[0]} #{row[1]} {row[2]} {row[3]}: label {row[4]}, "
                  f"judge {row[5]} -- {row[6]}")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("export", "score", "judge"))
    parser.add_argument("--runs", nargs="+", default=[])
    parser.add_argument("--file", "--out", dest="file",
                        default="runtime/evals/calibration-v2.json")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--properties", nargs="*", default=None,
                        help="only these properties (default: all)")
    parser.add_argument("--quota", nargs="*", default=[],
                        help="per-property label counts, as name=number")
    parser.add_argument("--avoid", nargs="*", default=[],
                        help="earlier calibration files whose replies to use last")
    parser.add_argument("--judge", default=None,
                        help="score: whose verdicts; judge: which model to run")
    parser.add_argument("--labels", nargs="*", default=[],
                        help="judge: labelled calibration files to run it on")
    args = parser.parse_args()
    if args.command == "export":
        if not args.runs:
            parser.error("export needs --runs")
        unknown = set(args.properties or ()) - set(corpus.rubric())
        if unknown:
            parser.error(f"unknown properties: {sorted(unknown)}")
        quotas = {}
        for spec in args.quota:
            name, _, number = spec.partition("=")
            quotas[name] = int(number)
        export(
            [Path(run) for run in args.runs], Path(args.file), args.seed,
            properties=args.properties, quotas=quotas,
            avoid=_used_before([Path(path) for path in args.avoid]),
        )
        return 0
    if args.command == "judge":
        return judge_labels([Path(path) for path in args.labels or [args.file]],
                            args.judge or DEFAULT_JUDGE)
    return score(Path(args.file), args.judge)


if __name__ == "__main__":
    raise SystemExit(main())
