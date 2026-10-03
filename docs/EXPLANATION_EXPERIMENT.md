# The explanation contract in her own use: a controlled experiment

*2026-10-01. **The flag is `off`.** It is enabled only after the revised contract passes its
check (docs/EXPLANATION_CHECK.md), and only on the user's decision.*

## What it is

Stage C (docs/STAGE_C_RESULTS.md) found that one explanation contract, replacing five conflicting
order and length rules, improves explanations without an extra model call. That result is about
the *architecture*:

- **one owner of an explanation's shape;**
- **no planner;**
- **no second generation.**

**The contract text was revised after Stage C,** because the goal is an understandable, natural
explanation, not a concrete opening on every answer:
- a direct answer first;
- an example only when it makes the answer easier to understand;
- needed terms explained in place;
- at most 4 sentences.

Stage C's original text is kept, word for word, as an evaluation comparison arm.

**This is a partial step, not a solved problem.** Under the rubric's strict `concrete_first`
definition, Stage C's contract still failed on 22 of 30 held-out turns. The definitions stay
frozen, and those failures stay failures.

## The flag

`config.yaml` → `responses.explanation_contract`. `ELAINA_EXPLANATION_CONTRACT` overrides it.

| value | effect |
|---|---|
| `"off"` | every turn as before (current) |
| `"on"` | the contract on every explanation turn |
| `"split"` | the contract on a random half of explanation turns, decided by each turn's random trace ID; the other half as before |

**What counts as an explanation turn:** the response budget is `explain`, the shape is `explain`,
and the act is answer. Every other turn is untouched in every mode.

**What no mode can do:** no value enables a plan or another model call. The plan arms exist only
for evaluations, behind `ELAINA_EXPLAIN_ARM`, which nothing in `config.yaml` sets.

**Tests:** the test harness pins the flag `off`, so no test meets a random arm.
`tests/test_explanation_experiment.py` covers the modes, the logging, the contract versions and
the frozen definitions.

## What is logged

Every explanation turn, in every mode, carries `context.explanation_experiment` in its turn trace
(`runtime/turn_trace/<date>.jsonl`):

```json
{"mode": "split", "variant": "contract" | "current", "assigned": "split by turn id",
 "contract_version": "d398489d1bd7"}
```

A turn that ran the contract also carries `context.explanation_contract`. The model's draft, the
displayed reply, the pipeline steps and the timings are in the same record, as for every turn.

## Evaluating real turns

```bash
python -m evals.explanation_experiment
```

The summary shows, per arm:
- the number of turns;
- length;
- where the illustration sits (a reading aid);
- replies changed after generation;
- turn time.

```bash
python -m evals.explanation_experiment --export-blind 20
```

This writes 20 turns per arm, shuffled, with the arm hidden, to label on the strict
`concrete_first` and `leads_with_the_answer` definitions. The key is in a separate file.

```bash
python -m evals.explanation_experiment --labels
```

This gives each arm's yes rates from those labels.

Real turns are not a controlled sample, so a difference between arms is evidence to read with its
counts. Another architecture change waits for that evidence.

## For evaluations

The experiment reads `config.yaml`, so an evaluation arm should set
`ELAINA_EXPLANATION_CONTRACT` explicitly. Otherwise a future value of the flag leaks into it.
`evals/explain_check.py` sets it `off` in every arm.

The frozen corpus v2 and Stage C trees are archived:
- `runtime/evals/pf2/frozen_tree.tar.gz`;
- `runtime/evals/pc/frozen_tree.tar.gz`.

Their `--verify` no longer passes, because the product code moved on.
