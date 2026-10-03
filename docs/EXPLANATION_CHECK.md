# The revised explanation contract: check

*2026-10-01. Written before any check turn ran; the criteria are fixed here and in
`evals/explain_check.py`. The production flag stays `off` until this passes, and enabling it is
the user's decision.*

## Why the contract was revised

**Stage C supports the architecture, not the wording.** It showed that one explanation contract,
in place of five conflicting order and length rules, helps (docs/STAGE_C_RESULTS.md). But its
contract made a concrete opening the rule, because `concrete_first` was the frozen target.

**The goal is different:** an understandable, natural explanation. A concrete example or scene is
a tool, not a required opening.

**What does not change:**
- The rubric is untouched. The strict `concrete_first` and `leads_with_the_answer` definitions
  stay as they are, and Stage C's 22 of 30 failures under the strict definition stay failures.
- `concrete_first` is now *read*, not *targeted*.

**The revision** (`brain/explain_contract.CONTRACT_TEXT`, version `d398489d1bd7`) keeps the
architecture:
- it removes the same conflicting persona, requirement and style lines;
- it keeps one length, the 4 sentences her post-processing already enforces;
- it adds no planner and no extra model call.

It changes the order of priorities:

1. Answer the question directly in the first sentence, in plain words.
2. Add one concrete example only when it makes the answer easier to understand: an abstract
   idea, or a mechanism that is hard to picture. Leave it out when the direct answer is already
   clear. Never open with an example or a story that delays the answer.
3. Use a technical word only when the answer needs it, explained in everyday words in the same
   sentence.
4. Say more about how it works only when the answer is unclear without it.

The limit is at most 4 sentences and 130 words; shorter is better when the answer is simple.

Stage C's text survives word for word (version `35c01efc844a`) as the comparison arm
`contract_stage_c`.

## What runs

| arm | contract | runs |
|---|---|---|
| A | current production (no contract) | 3 |
| B | Stage C's contract, word for word | 3 |
| R | the revision | 3 |

**The check set: `explain_check`.** 18 new first explanations, 10 English and 8 Korean. None
comes from v1, Stage B, corpus v2 (including the 21 diagnosis turns) or Stage C's held-out set.
Each was marked in advance:

- **`direct` (9):** a direct answer is the natural explanation, and an example would be padding.
  Hiccups, salty oceans, goosebumps, pink flamingos, popcorn, breath in winter, sweating after
  spicy food, browning apples, the smell after rain.
- **`example` (9):** the idea is abstract or the mechanism hard to picture, so an example helps.
  Correlation and causation, public-key encryption, diminishing returns, binary search, speed and
  velocity, diversification, independent events, economies of scale, leverage.

**The regression set.** Arm R also runs 3 times on Stage C's held-out set (30 turns). It is
compared with Stage C's own A and B runs there: same code apart from the contract, an earlier
session.

**Conditions:** `qwen3:8b`; production's soft stages and post-processing; the experiment flag
forced off in every arm, so only the arm's own contract runs; the frozen v1 analysis machinery
and judge.

## What is measured

All rates are shares of judged turn-runs.

| measure | definition |
|---|---|
| **Answer first** | `leads_with_the_answer` yes, under the strict definition |
| **Forced example** | `concrete_first` yes and `leads_with_the_answer` no: a scene before the answer. Read on the `direct` turns. |
| **Quality** | no substance or explanation defect apart from `concrete_first`: answers the ask, accurate, terms explained, says what it solves, the subject checks |
| **Comparisons with A** | run *i* against run *i*, stable at 2 of 3 |
| **Reading aids** (not criteria) | `concrete_first` rate; an example anywhere in the reply (a wording search); length |

## Criteria (fixed before running)

R passes when all of these hold:

| # | criterion |
|---|---|
| 1 | **Answer first, regressions:** at most 1 stable answer-first regression against A, on each set |
| 2 | **Answer first, rate:** R's rate at least A's minus 5 points, on each set |
| 3 | **Forced examples:** on the `direct` turns, R's forced-example rate at most 10%, and at most A's plus 5 points |
| 4 | **Quality:** R's stable quality gain against A at least 0 (better − worse), on each set |
| 5 | **Substance:** at most 1 stable substance regression against A, on each set |

**If it passes:** the flag can be set to `split` (half of real explanation turns on the revision,
half as now, each logged) for real-turn evaluation. That is still the user's decision.

**If it does not:** the report says which criterion failed, and nothing is enabled.

**Cost:** about 80 minutes unattended. That is 9 runs of 18 turns, 3 runs of 30 turns, and the
judge.
