"""A4's instrument: does every capability declare a contract, and does
every sentence it produces stay out of its own implementation.

Two halves, because A4 has two halves.

**Declaration.** Every capability in :mod:`brain.capabilities` must have a
contract in :mod:`brain.capability_contract` -- typed needs, named result
keys, and a failure set -- and every declared failure must have a sentence
in both languages. A capability with no contract is the thing the phase
exists to make impossible, so it is counted rather than assumed.

**Leakage.** The static scan. It reads ``brain/`` and ``agents/`` with the
AST, finds string expressions that are assigned to a name the reply path
reads -- ``forced_response``, ``locked_response``, ``message``, ``summary``
and friends -- and asks :func:`capability_contract.leaks_internals` whether
what it would say names a component the person never chose.

It is a static scan on purpose. The live dogfood runs go through real
network calls that mostly *succeed*, so the failure vocabulary is exactly
the part a conversation-quality run does not exercise: you would have to
unplug the machine mid-arc to see it. The strings are in the source whether
or not a run happens to reach them, and 43 of them named a Python exception
class at the start of this phase.

    .venv/Scripts/python.exe scripts/capability_contract_report.py
    .venv/Scripts/python.exe scripts/capability_contract_report.py --json out.json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from brain import capability_contract as contracts  # noqa: E402
from brain.capabilities import CAPABILITIES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Names that carry a sentence to the person. Assigning a string to one of
# these is the moment a developer chooses words the user will hear.
SPOKEN_NAMES = frozenset({
    "forced_response",
    "locked_response",
    "effective_forced_response",
    "tool_result",
    "tool_result_fallback",
    "message",
    "spoken",
    "summary",
    "reply",
    "capability_note",
    "blocked_identification_reply",
})

# Keyword arguments that mean the same thing at a call site.
SPOKEN_KEYWORDS = frozenset({"tool_result", "message", "spoken", "reply"})

SEARCHED = ("brain", "agents", "voice")


def _text_of(node: ast.AST) -> str:
    """The literal words in a string expression, joined.

    F-string placeholders are dropped rather than guessed at: what a
    ``{}`` evaluates to is unknowable statically, which is precisely why
    an exception interpolated into one is a leak worth catching -- the
    surrounding literal text is what gives it away.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append(_placeholder(value))
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _text_of(node.left) + _text_of(node.right)
    if isinstance(node, ast.BoolOp):
        return " ".join(_text_of(item) for item in node.values)
    return ""


def _placeholder(value: ast.FormattedValue) -> str:
    """What an f-string slot will put there, when we can tell statically.

    ``{type(error).__name__}`` is the whole finding: the slot itself is
    the leak, and rendering it as its own name is what lets the same
    detector the reply path uses see it in the source.
    """
    try:
        expression = ast.unparse(value.value)
    except Exception:
        return " "
    if "__name__" in expression and "type(" in expression:
        return "SomeError"
    if expression.strip() in {"error", "exc", "e", "err"}:
        return " "
    return " "


def _spoken_strings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = {
                target.id for target in node.targets
                if isinstance(target, ast.Name)
            }
            if names & SPOKEN_NAMES:
                text = _text_of(node.value)
                if text.strip():
                    found.append((node.lineno, text))
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg in SPOKEN_KEYWORDS:
                    text = _text_of(keyword.value)
                    if text.strip():
                        found.append((node.lineno, text))
    return found


def scan_leaks() -> list[dict]:
    findings: list[dict] = []
    for folder in SEARCHED:
        base = ROOT / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if path.name == "capability_contract.py":
                # The detector's own patterns are written out in it.
                continue
            try:
                spoken = _spoken_strings(path)
            except SyntaxError:
                continue
            for line, text in spoken:
                fragment, kind = contracts.leaks_internals(text, authored=True)
                if fragment:
                    findings.append({
                        "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                        "line": line,
                        "kind": kind,
                        "fragment": fragment,
                        "text": " ".join(text.split())[:110],
                    })
    return findings


def check_declarations() -> dict:
    declared, undeclared, incomplete = [], [], []
    for capability in CAPABILITIES:
        contract = contracts.contract_for(capability.id)
        if contract is None:
            undeclared.append(capability.id)
            continue
        declared.append(capability.id)
        problems = []
        if not contract.failures:
            problems.append("no declared failure set")
        if not contract.returns:
            problems.append("no declared result keys")
        for failure in contract.failures:
            for language in contracts.LANGUAGES:
                if not failure.says.get(language):
                    problems.append(f"{failure.code} has no {language} line")
        for need in contract.needs:
            if need.kind not in contracts.KINDS:
                problems.append(f"{need.key} has unknown kind {need.kind!r}")
            for language in contracts.LANGUAGES:
                if not need.asks.get(language):
                    problems.append(f"{need.key} has no {language} question")
        if problems:
            incomplete.append({"capability": capability.id, "problems": problems})
    return {
        "capabilities": len(CAPABILITIES),
        "declared": declared,
        "undeclared": undeclared,
        "incomplete": incomplete,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="out", default="")
    args = parser.parse_args()

    declarations = check_declarations()
    leaks = scan_leaks()

    total = declarations["capabilities"]
    complete = total - len(declarations["undeclared"]) - len(declarations["incomplete"])

    print("=" * 66)
    print("A4 -- CAPABILITY CONTRACTS")
    print("=" * 66)
    print(f"\nContracts complete: {complete}/{total}")
    for name in declarations["undeclared"]:
        print(f"  [ ] {name} -- no contract at all")
    for item in declarations["incomplete"]:
        print(f"  [~] {item['capability']} -- {'; '.join(item['problems'])}")

    by_kind: dict[str, int] = {}
    for finding in leaks:
        by_kind[finding["kind"]] = by_kind.get(finding["kind"], 0) + 1

    print(f"\nSentences that name internals: {len(leaks)}")
    for kind, count in sorted(by_kind.items(), key=lambda pair: -pair[1]):
        print(f"  {count:>4}  {kind}")

    if leaks:
        print("\nWhere:")
        for finding in leaks[:40]:
            print(
                f"  {finding['file']}:{finding['line']}  "
                f"[{finding['fragment']}]  {finding['text']}"
            )
        if len(leaks) > 40:
            print(f"  ... and {len(leaks) - 40} more")

    passed = complete == total and not leaks
    print("\n" + "=" * 66)
    print("PASS" if passed else "NOT YET")
    print("=" * 66)

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {"declarations": declarations, "leaks": leaks},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nWritten to {args.out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
