# FAST/DEEP corpus v2: protocol

*Version v2.0, 2026-09-30. **Approved by the user and frozen** with
`python -m evals.fastdeep_v2 --freeze`. Their choices at approval: keep "Hm?" in group A, and
keep the reference DEEP arm. The freeze moved the corpus to
`evals/scenarios/followups_v2.json` and wrote `runtime/evals/pf2/FROZEN.json` with everything
in §8. From then on, any change to this document, the corpus, the router, the evaluator, the
product code, a model or the Ollama version makes `--verify` fail, and `--run` and the report
refuse to proceed. A changed rule or criterion is a new protocol version, with a new held-out
corpus.*

## 1. The question

Does the shadow router (`brain/fastdeep_router.py`) generalise to follow-ups it was not
designed on, when it actually routes? "Actually" means the architecture production would run:

- FAST writes the earlier turns.
- The router decides DEEP on a later turn.
- The 27B writes that turn with FAST's answers in its history.
- The next turn goes back to the 8B.
- Every model swap is paid for and measured.

Stage B's SPLIT arm did not test this, because its earlier answers were the 27B's own. Its
results are development evidence, not proof (§9).

## 2. The corpus

`followups_v2`: 30 scenarios and 60 scored turns, 18 English and 12 Korean. Every subject,
scenario ID and utterance is new: none appears in the v1 corpus, the contamination matrix or
the Stage B runs. A test checks the IDs and utterances (`tests/test_fastdeep_v2.py`); the subjects were checked by searching the v1 corpus and the matrix.

Each scenario names its group. Each scored turn's role follows from its group and position, so
nothing about a turn is decided after it runs.

| group | scenarios | roles of the scored turns |
|---|---|---|
| A: confusion or re-explanation (7 explicit, 3 implicit) | 10 | first; then **confusion** |
| B: deepening follow-ups that are not confusion | 6 | first; then **deepening** |
| C: follow-ups that should stay FAST (a value again ×2, thanks, a remark, a correction, a topic shift to the time) | 6 | first; then **stay_fast** |
| D: first-time explanations | 4 | first |
| E: confusion, then a turn that returns to FAST (thanks ×2, a value, the date) | 4 | first; **confusion**; **stay_fast** |

**In scope** for the router are the confusion and deepening turns: 20 turns, 60 turn-runs.

**Should stay FAST** are the C second turns and the E third turns: 10 turns.

All of group C's turns escape the rule through its FAST gates (not an explanation, a domain
claim, a topic shift); none is an explanation-shaped follow-up. Whether DEEP is wasted on an
explanation-shaped follow-up is not knowable in advance. It is measured by precision on
groups A and B instead.

## 3. The system under test

**The rule.** `brain/fastdeep_router.py`, sha256 `20edb80e2cd8f123…` (`decide` sha256
`f3418967e647c079…`), unchanged since Stage B. It sends a turn DEEP when four things hold:

- its response budget is an explanation;
- the topic has not shifted;
- no deterministic domain claimed the turn;
- at least one continuation signal holds: the router marks it a follow-up, the previous turn
  was an explanation, or the router reads it as a clarification.

There is no model call, no phrase list and no scenario knowledge.

**The switch: evaluation arms only.** `brain/fastdeep_apply.py`. With
`ELAINA_FASTDEEP_ROUTE` unset, which is how she always runs, the policy records only and
every turn is answered FAST. Nothing in `config.yaml` sets it, and `tests/test_fastdeep_apply.py`
pins this. Production stays FAST-only.

In an arm, the switch acts right after the decision is recorded:

- the 27B writes every word of the turn;
- the soft stages are off for that turn only;
- the next turn starts back on the 8B with soft stages on.

**Product code.** All of `agents`, `brain`, `config`, `core`, `memory`, `security`, `tools`,
`vision`, `voice` and `main.py` is fingerprinted at the freeze. The smoke run confirmed that a
backend run does not change it.

## 4. Arms and conditions

The 8B reads every turn in every arm except DEEP. The judge never sees which arm wrote a reply.

| arm | dirs | writes the turn | soft stages | what it gives |
|---|---|---|---|---|
| FAST | `pf2-fast-1..3` | the 8B, always | on | today's behaviour; the shadow decision recorded |
| ROUTED | `pf2-routed-1..3` | the 27B on the turns the router sends DEEP, the 8B otherwise | off on switched turns | **the routed system**: real decisions, real history, real swaps |
| LATE | `pf2-late-1..3` | the 8B on the first turn, the 27B on every later turn | off after the first | ground truth for second turns: the 27B after FAST's first answer |
| SPLIT | `pf2-split-1..3` | the 27B, always | off | ground truth for first turns; Stage B's method on later turns |
| DEEP | `pf2-deep-1..3` | the 27B reads and writes | off | reference only; no criterion uses it |

**Models** (Ollama digests):

| role | model | quantization | digest |
|---|---|---|---|
| FAST and reading | `qwen3:8b` | Q4_K_M | `500a1f067a9f…` |
| DEEP | `hf.co/jrell/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller:latest` | IQ4_XS | `9a56ed646e71…` |
| judge | `qwen3.6:35b-a3b` | Q4_K_M | `07d35212591f…` |

**Runtime:**

| item | value |
|---|---|
| Ollama | 0.35.0 (Stage B ran on 0.34.4; it updated itself in between) |
| GPU | RTX 5080, 16 GB, driver 616.92 |
| CPU | AMD Family 26 |
| RAM | 31 GB |
| OS | Windows 11 26200 |
| Python | 3.11.9 |

The 8B and the 27B do not fit on the card together, so every switch is a real unload and load.

**Conditions:**

- speech recognition off, and `ELAINA_TTS=off` (forced by the runner);
- `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`;
- the Elaina app closed, so port 8765 is free;
- no other GPU work; the GPU's tenants are logged before and after every run;
- every model unloaded before each run;
- each scenario in a fresh backend.

**Order:** one session. For run 1, then 2, then 3: FAST, ROUTED, LATE, SPLIT, DEEP. Judging
starts after all generation. The freeze is re-verified before every run, so an Ollama
self-update, a new model digest or a code change stops the session instead of contaminating
it.

## 5. Labels

The frozen v1 analysis (`evals/fastdeep_analysis.py`, fingerprint `cbd8c3b57913990a`) is used
unchanged. It compares FAST run *i* with the counterfactual run *i*:

- **First turns:** the counterfactual is SPLIT, with the same, empty history.
- **Second turns:** the counterfactual is LATE, with the same history (FAST's first answer).
- **Third turns:** group E's return turns get **no label**. The two arms' histories differ, so
  no clean comparison exists. They enter only through "should stay FAST" (§6) and §7.

The label rules are the frozen ones:

- the tiers;
- stable means 2 of 3 run-paired comparisons;
- "mixed" counts toward neither;
- form-only never makes DEEP+;
- a DEEP+ that rests only on `concrete_first` or `adapts_to_confusion` is *pending*, and counts
  as not DEEP+ until blind labels confirm it.

**Blind labels** are requested only for pending second turns that could decide a criterion:
those in scope, or those ROUTED sent in any run (`--export-blind`). The frozen reader's rule
applies, with LATE in DEEP's place. There is no general labelling round.

The v2 suite is visible to the frozen code only inside `evals/fastdeep_v2.py`'s processes. That
is why v1's fingerprint and reports are unchanged in the working tree (tested).

## 6. Acceptance criteria (frozen)

Decisions are ROUTED's: a turn is *sent* when the engine switched it. Turn-run pairs are pooled
over the three ROUTED runs.

| # | criterion | threshold |
|---|---|---|
| 1 | **DEEP precision**: of every sent turn-run (any group, any turn), the share whose turn is stable DEEP+ | ≥ 70% |
| 2 | **DEEP+ recall**: of the stable DEEP+ turns in scope (A, B and E second turns), the share of turn-runs sent | ≥ 50% |
| 3 | **Stable DEEP-worse turns sent**, distinct turns, in any ROUTED run | ≤ 1 |
| 4 | **Should-stay-FAST turn-runs sent** (C second turns, E third turns), in any ROUTED run | 0 |
| 5 | **p95 of DEEP turns end to end** (8B reading, 27B load, 27B answer) | ≤ 18.0 s |
| 6 | **p95 of the 8B reload** measured in each return turn (the first turn after a DEEP turn) | ≤ 5.0 s |
| 7 | **p95 of ROUTED's other FAST turns** | ≤ the FAST arm's p95 over the same turns + 1.0 s |

**How the verdict is decided:**

- A criterion that cannot be computed counts as not met, for example when nothing is sent or
  no in-scope turn is stable DEEP+.
- The verdict is mechanical: ACCEPTED only if all seven are met.
- The run counts as valid only if every arm has three complete runs, at least 95% of each run's
  scored turns are comparable, and the freeze verifies. Otherwise the verdict is INVALID and
  the affected runs are repeated. That is not a reason to change anything else.

**Why these numbers.**

- **Criteria 1–4** are the user's thresholds of 2026-09-30, made strict:
  - pooled over runs, not majority;
  - "any run" for criteria 3 and 4;
  - pending labels count against precision.
- **Recall scope.** Recall covers the router's stated scope, the follow-ups. First turns are
  FAST by design. What that design costs is reported (first turns stably better with the 27B),
  but it does not decide the verdict.
- **Latency was set from Stage B's measurements and the v1-scenario smoke run of 2026-09-30,
  never from v2:**
  - entering DEEP loads the 27B: 7.5 s on Ollama 0.34.4, 7.9–8.2 s on 0.35.0;
  - returning reloads the 8B: 4.0 s and 4.2 s;
  - a DEEP turn is SPLIT's turn less the 8B reload SPLIT pays at its start. That is about
    13–14 s at the median and 14–17 s at p95 (SPLIT confusion turns p95 17.9 s, explanation
    turns 21.0 s, each less 4.0 s).
- **Why the budgets are relative and per turn.** A fixed budget for return turns or FAST turns
  would measure this corpus's layout instead of the switch. In v2 the turn after a DEEP turn is
  often the next scenario's first explanation, and FAST's own first explanations reach a p95 of
  12 s. So:
  - the return-turn budget is the measured reload;
  - FAST turns are compared with the FAST arm on the same turns.
- **What 18 s means for the product.** Even a DEEP turn that meets its budget makes the person
  wait 14–18 s. Whether that is acceptable for a voice reply is a product decision the
  measurement cannot make.

## 7. Attribution

Each turn gets one outcome, using ROUTED's majority decision. "Acceptable" means no substance
or explanation defect, in at least 2 of 3 runs.

| outcome | when |
|---|---|
| correct escalation | stable DEEP+, sent |
| correct FAST | not sent, and FAST acceptable or DEEP-worse |
| **routing miss**: missed escalation | stable DEEP+, in scope, not sent |
| **routing miss**: escalated a should-stay-FAST turn | a C or E return turn, sent |
| **DEEP model failure**: made it worse | stable DEEP-worse, sent |
| **FAST model failure**, outside the router's scope | stable DEEP+ on a first or should-stay-FAST turn, so the fix is FAST's, not routing's |
| **FAST model failure**, return turn | a return turn FAST did not answer acceptably after a DEEP turn |
| **neither model good enough** | no stable label; both FAST and the 27B not acceptable |
| **unstable / non-repeatable** | no stable label, and the runs disagree |
| **unstable / non-repeatable (judge-only)** | pending DEEP+ without blind confirmation |
| unneeded escalation (cost only) | sent; both acceptable; no material difference |

## 8. What the freeze records

`runtime/evals/pf2/FROZEN.json`; `--review` prints it without writing anything.

- **This document:** sha256.
- **The corpus:** sha256, scenario and turn counts, groups and roles.
- **Analysis:** v1's fingerprint and its six files' sha256.
- **Rubric:** version and fingerprint (`c4fac3a80008`).
- **Evaluator:** judge model, and the sha256 of `evals/fastdeep_v2.py`, `judge.py`, `run.py`,
  `phase2.py`, `fastdeep.py` and `integrity.py`.
- **Router:** file sha256, `decide` sha256 and git state.
- **Switch:** the sha256 of `fastdeep_apply.py`, `response_stages.py` and `chat_engine.py`.
- **Product code:** the tree fingerprint, file count and git HEAD.
- **Models:** digests, quantization and size.
- **Runtime:** Ollama version and environment, GPU, driver, CPU, RAM, OS, Python and packages.
- **Arms:** environments and order.
- **Criteria:** thresholds and their exact wording.

## 9. Cost

| part | estimate |
|---|---|
| Generation, per round | FAST about 10 min, ROUTED about 14, LATE about 17, SPLIT about 22, DEEP about 15: about 78 min |
| Generation, three rounds | about 3.9 h |
| Judging | 900 turn verdicts, about 1.5 h |
| Blind labels | a handful at most |
| **Total** | about 5.5 h unattended |

Dropping the reference DEEP arm saves about 1 h 10 min, and no criterion would change.

## 10. What v2 does not cover

- **Consecutive DEEP turns.** No v2 conversation has two DEEP-eligible turns in a row. In the
  smoke run, a second consecutive DEEP turn paid both swaps, because the 8B reads and the 27B
  writes: 18.2 s. That cost belongs to the production design.
- **Long conversations,** and DEEP turns late in them.
- **Reasoning, planning, coding and synthesis** turns.
- **The speech path.** Recognition and the voice are off.
- **Production routing.** None of it is built, and none of it is switched on.
