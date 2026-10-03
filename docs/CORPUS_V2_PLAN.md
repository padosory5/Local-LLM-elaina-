# Corpus v2: the held-out check of the shadow FAST/DEEP router

*Plan, 2026-09-30. Nothing has run; nothing is frozen. The scenarios are
staged in `evals/scenarios_next/followups_v2.json` and are not loaded by
`evals/corpus.py`.*

> **Superseded by `docs/FAST_DEEP_V2_PROTOCOL.md`** (same day, after the
> review). What changed from this plan:
>
> - **Arms.** The ground truth for a follow-up is no longer SPLIT. It is
>   LATE: the 27B writing the turn after FAST's own first answer. A ROUTED
>   arm runs the real switch inside the conversation (`brain/fastdeep_apply.py`,
>   evaluation only).
> - **Corpus.** 30 scenarios and 60 scored turns, up from 24 and 44:
>   - two implicit-confusion cases added to group A;
>   - a group E added: confusion, then a turn that returns to FAST;
>   - two group B follow-ups replaced, because the `purpose` mode graded
>     benefits on a question about drawbacks;
>   - stricter value checks.
> - **Registration.** v2 is not added to `EXTRA_SUITES`: v1's fingerprint
>   stays valid in the working tree.
> - **Latency criteria.** Measured switching cost, not one fixed end-to-end
>   budget.

## Why

The shadow router (`brain/fastdeep_router.py`) was designed on the v1
corpus. Everything it scores there is development data
(`docs/FAST_DEEP_SHADOW.md`).

| | DEEP+ recall | DEEP precision | DEEP use |
|---|---|---|---|
| Baseline FAST/DEEP labels | 50% | 92% | 18% |
| Stage B, 27B answering under the 8B's reading | 83% | 53% | 47% of follow-up-heavy turns |

Whether the rule works on follow-ups it has never seen is a question only
new turns can answer.

## What v2 is

A new suite, `followups_v2`: 24 scenarios and 44 scored turns. No subject
appears in v1. Modes are the rubric's existing ones, so the rubric and its
fingerprint do not change.

| group | what it tests | scenarios | what the rule should do |
|---|---|---|---|
| A | confusion and re-explanation follow-ups, several implicit ("Hm?", "Simpler, please.", "음… 무슨 말인지 모르겠어.") | 8 (5 en, 3 ko) | DEEP where it helps |
| B | deepening follow-ups that are not confusion ("What's the catch?", "Why is red always on the outside?") | 6 (4 en, 2 ko) | open: the rule sends them; does DEEP help? |
| C | follow-ups that should stay FAST: a value again, thanks, a remark, a correction, a topic shift | 6 (4 en, 2 ko) | FAST |
| D | first-time explanations | 4 (2 en, 2 ko) | FAST by design; does that cost anything? |

Group C and the implicit cases in group A are there to break the rule, not
to flatter it. Group B is where the rule makes a claim the v1 data barely
tested.

**Review before freezing.** The scenarios were written by someone who had
seen the rule. Before anything is frozen, the user reviews them, and edits,
adds or drops what reads as written for the rule.

## How it is kept apart from v1

1. **Staged until reviewed.** `evals/scenarios_next/` is not read by
   `corpus.load`, so v1's fingerprint (`cbd8c3b57913990a`) still holds and
   every v1 report stays reproducible.
2. **Registered at the freeze, in one change:**
   - move the file to `evals/scenarios/`;
   - add `followups_v2` to `EXTRA_SUITES`, so it loads only when named and
     never enters a v1 comparison.
3. **A new freeze.** v2 gets its own root, `runtime/evals/pf2/`, and its own
   `FROZEN.json`. The fingerprint is recomputed and now covers the v2
   corpus.
   - The analysis code is the frozen v1 code, unchanged.
   - The v2 record adds a hash of `brain/fastdeep_router.py`, so the rule
     under test cannot move either.
   - v1 stays reproducible from the commit that holds it. Once v2 is in the
     corpus, v1's own fingerprint check no longer matches the working tree,
     which is by design.
4. **No mixing.** Every v2 report reads only `pf2` runs and `followups_v2`
   turns. Nothing from v1 enters a v2 number.

## How it is evaluated

**Arms.** One session, 3 runs each, alternating, TTS and speech recognition
off.

| arm | reads the turn | answers | soft stages | what it gives |
|---|---|---|---|---|
| FAST | 8B | 8B | on | the router's shadow decision, recorded live in each turn's trace (`shadow_route`) |
| SPLIT | 8B | 27B | off | DEEP as a routed system would answer it, under the 8B's reading |
| DEEP | 27B | 27B | off | for reference |

**Labels.** The frozen v1 rules, unchanged:

- the tiers;
- FAST run *i* against SPLIT run *i* (and DEEP), stable at 2 of 3;
- "mixed" counts toward neither;
- form-only never makes DEEP+.

Blind labels are asked only for a comparison that rests solely on
`concrete_first` or `adapts_to_confusion` **and** decides a turn's label. No
general labelling round.

**Measures.** The same as `docs/FAST_DEEP_SHADOW.md`:

- **Routing quality:** DEEP+ recall, DEEP precision, DEEP use, DEEP-worse
  turns sent, missed DEEP+ turns, needless escalations.
- **Quality:** combined, against all FAST, all DEEP and perfect knowledge.
- **Attribution:** routing miss, DEEP not improving the turn, neither model
  good enough, and unstable or judge-only differences.
- **Latency:** switching included. The 27B load and 8B reload are
  re-measured in SPLIT.

The answers are reported per group.

**Proposed criteria for "it generalises".** These are for the user to set
before the run.

- On groups A and B, against FAST → SPLIT stable labels: DEEP precision of
  at least 70%, and DEEP+ recall of at least 50%.
- At most one stable DEEP-worse turn sent to DEEP.
- No group C turn sent to DEEP.
- End-to-end p95 within the budget the user sets. The development data
  gives 17.2 s p95, with a 4.8 s p50.

**Cost.**

- Generation: FAST about 8 minutes, SPLIT about 20, DEEP about 13 per run.
  With 3 runs each, about 2 hours.
- Judging: about 30 minutes.
- Labels: whatever the rule above calls for, expected to be a handful.

## What it does not cover

- The history limit stays. SPLIT's earlier answers are the 27B's own; a
  system that routes only the follow-up would have FAST's earlier answers.
  Measuring that needs a model switch inside a conversation, which is the
  first piece of the production router and comes after v2.
- Reasoning, planning, coding and synthesis turns remain Stage B's separate
  supplementary suite.
