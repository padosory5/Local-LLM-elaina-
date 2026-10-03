# Stage C: the explanation-contract experiment, protocol

*Version C1.0, 2026-10-01. **Approved by the user and frozen** with
`python -m evals.stagec --freeze`. The freeze moved the corpus to
`evals/scenarios/explain_heldout.json` and wrote `runtime/evals/pc/FROZEN.json` with everything
in §8. No arm ran before it.

From then on, any change to this document, the corpus, an arm's text or code, the evaluator,
the product code, a model or the Ollama version makes `--verify` fail, and `--run` and the
report refuse to proceed. The held-out corpus is not modified after results are seen. A changed
arm or criterion is a new protocol version, with a new held-out set.*

## 1. The question, and what decides it

The question comes from docs/STAGE_C_DIAGNOSIS.md: both models answer first explanations
correctly but in the wrong shape. The cause is five conflicting sources of order and length
in the prompt and post-processing. Three questions follow:

- **A → B:** does one explanation contract, replacing the conflicting instructions, help?
- **B → C:** does a small question-specific plan from the 8B add to it?
- **C → D:** is the 8B the limit, judged against the same plan written by the 27B?

**Diagnosis and evaluation stay separate:**
- Only the new held-out set decides (30 scored turns).
- The 21 Stage C diagnosis turns run beside it, from the frozen corpus v2. They are reported
  apart, decide nothing, and did not shape the contract text beyond the diagnosis already
  written.

## 2. The held-out corpus

`explain_heldout`: 24 scenarios, 12 English and 12 Korean.
- **Turns:** 18 single first explanations, and 6 explanations followed by a deepening question.
  That makes 30 scored turns.
- **Modes:** cause 15, how 10, purpose 3, intuition 2. All are modes the rubric judges
  `concrete_first` on.
- **Checks:** every turn has a broad `must_contain_any` on its subject, so a wrong-subject
  answer is caught as a substance defect. Korean turns check their language. The defaults
  (`not_searched`, `no_cards`) are corpus v2's.
- **Separation:**
  - no scenario ID or utterance appears in v1, the contamination matrix, Stage B or corpus v2,
    which includes the 21 diagnosis turns;
  - no subject word appears anywhere in their files;
  - `tests/test_stagec.py` checks all three.

| | English | Korean |
|---|---|---|
| single | bread going stale; microwave; touchscreen; onions and tears; soda going flat; two-factor authentication; half-life; leap years; electric guitar | clouds; induction hob; muscle growth; QR codes; short selling; expected value; insurance; static in winter; the power grid |
| with a follow-up | dishwasher → special detergent; fever → why it stops rising; credit card income → cashback | elevator floors → order of stops; ears in a tunnel → swallowing; bicycle balance → falling when stopped |

## 3. The arms

Every arm uses `qwen3:8b` with production's soft stages and post-processing. Only what the
arm names changes. Production is untouched: with `ELAINA_EXPLAIN_ARM` unset, which is how she
always runs, no prompt changes and no plan is made (`tests/test_explain_contract.py`).

**Where an arm applies.** On an explanation turn: the response budget is `explain`, the shape
is `explain`, and the act is answer. That covers 20 of the 21 diagnosed turns. Every other turn
is built exactly as in production. The report states, per arm, how many held-out turn-runs the
contract applied to.

| arm | on an explanation turn | extra call |
|---|---|---|
| **A** | production prompt, unchanged | none |
| **B** | the conflicting rules come out and one contract goes in (below) | none |
| **C** | B, plus a plan from one 8B call before generation | 1 × 8B, explanation turns only |
| **D** | B, plus the same plan written beforehand by the 27B | none at run time |

### What B removes, and what it puts in their place

The five conflicts and how B resolves each:

| conflict | in production | under B |
|---|---|---|
| "lead with the answer" | persona: "Lead with the answer. State the fact or the final number first…"; style: "Lead with the answer in plain spoken words." | removed. The contract asks for the answer *through* the opening situation, not before it, and not after a warm-up. |
| result before background | voice requirements: "Give the requested result before reactions, background, or offers of more help." | removed. The contract gives the order. |
| short limits | persona: "Short by default, a sentence or two…"; style: "At most 4 sentences."; requirements: 130 words and 6 sentences | one length: **at most 4 sentences and 130 words**. That is the ceiling `her_voice` already enforces after generation, so the unchanged post-processing does not fight the contract. |
| the example's place | explain goal: "…and show one concrete case or worked example" (no position) | the contract's order: situation, then the general point, then terms in place, then mechanism only if needed |
| explanation guidance | persona: "If the user asks for an explanation, keep it simple and in order. No jargon, no over-explaining." | replaced by the contract's terms rule |

The kept voice requirements are production's own words; a test holds them to
`brain/response_policy.py`. The prompt doesn't grow: for "How does GPS know where I am?", B's
prompt is 1,939 words against A's 1,937. C adds the plan block, about 50–80 words.

**The contract does not ask for "Imagine…"** It asks that the opening situation *carry the
answer*:
- "The situation is where the answer happens, not a warm-up before it."
- A comparison of the form "it is like X" is not a situation, which follows the rubric's
  definition of `concrete_first`.

The exact texts are in the appendix.

### The plan (C and D)

**Five fields only:**

| field | content | size limit |
|---|---|---|
| `understand` | what the person needs to understand | ≤ 15 words |
| `scene` | one particular everyday situation where the answer can be seen happening | ≤ 20 words |
| `answer` | the answer, in plain words | ≤ 20 words |
| `terms` | at most 2 terms, each with an everyday meaning | ≤ 12 words each |
| `depth` | `brief` or `mechanism` | – |

**What C does not do.** It does not write a draft to rewrite.

**What the planner sees.** The person's questions so far, and no assistant text. That gives C
and D the same input, so C against D compares only the planner model.

**When a plan is unusable.** It is not JSON, has no scene or answer, has a scene or answer over
40 words (twice the limit, which would make it a draft), or has terms that aren't a list. The
turn then falls back to B, and the reason is recorded.

**How D's plans are written.** The 27B uses the same prompt and call, once, for every chain of
questions in the 43 scenarios, before any arm runs. They go to
`runtime/evals/pc/plans_27b.json`, and the file's hash is logged. They are written after the
freeze, as arm D's input.

**Format check before the freeze.** Three v1 development questions were sent to the 8B
planner, with no backend and nothing from either set:
- all three plans were usable JSON;
- 1.0–1.4 s when warm, 112–168 tokens;
- the content was not examined for tuning.

## 4. Conditions

**Models and runtime** (all pinned by the freeze):

| item | value |
|---|---|
| writer and reader | `qwen3:8b` (Q4_K_M, digest `500a1f067a9f…`) |
| D's plans only | `hf.co/jrell/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller:latest` (IQ4_XS, `9a56ed646e71…`) |
| judge | `qwen3.6:35b-a3b` (`07d35212591f…`) |
| Ollama | 0.35.0 |
| hardware | RTX 5080 16 GB, driver 616.92 |

The 27B writes D's plans in a separate pass. Nothing else uses it, and no arm swaps models.

**Conditions:** speech recognition off; `ELAINA_TTS=off`; `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1`; the Elaina app closed; the GPU free; every model unloaded before
each run; a fresh backend per scenario.

**Order:**
1. D's plans.
2. For run 1, 2, 3: A, B, C, D.
3. The judge, after all generation.

The freeze is re-verified before every run. An Ollama self-update or any code change stops the
session.

## 5. Labels and the blind sample

**Labels.** Comparisons use the frozen v1 analysis (`evals/fastdeep_analysis.py`, fingerprint
`cbd8c3b57913990a`) unchanged:
- run *i* against run *i*;
- the tiers;
- stable means 2 of 3;
- "mixed" counts toward neither;
- form-only never counts as better.

**The blind sample (concrete_first).**

| item | value |
|---|---|
| judge-credited replies | up to 20 held-out replies credited with `concrete_first`, drawn evenly from B, C and D |
| distractors | 8 replies the judge did not credit, from any arm |
| order | shuffled, fixed seed |
| what the person sees | the conversation and the reply, with no arm, run or verdict; the key is in a separate file |
| definition | the rubric's existing one, unchanged: a particular scene or worked example before the general statement; "X is like Y" does not count |
| measure | **Confirmation:** the share of the judge's credits the person also labels yes |

**How the sample is used.**
- At **70% or more**, a gain that rests only on `concrete_first` counts as stable.
- **Below 70%**, the apparent gain is rejected, and those comparisons stay pending, as the
  frozen rule says.
- The sample validates the judge; nothing is changed to make the judge agree.

## 6. Acceptance criteria (frozen)

These are the user's criteria of 2026-10-01, unchanged, computed on the 30 held-out turns.
"Net gain X → Y" means (turns stably better in Y − turns stably worse) / 30.

| # | rule | effect |
|---|---|---|
| 1 | C's net gain over B < 20% | the plan adds insufficient value |
| 2 | more than 1 substance regression, or more than 2 leads-with-the-answer regressions, against A (a regression holds in ≥ 2 of 3 run pairs) | that arm is rejected |
| 3 | fewer than 70% of the judge's `concrete_first` credits confirmed blind | the apparent gain is rejected |
| 4 | C's median turn time on planned held-out turns more than 1.5 s above B's on the same turns, or more than 5% of C's planned held-out turn-runs unusable | C is rejected on cost or reliability |
| 5 | D's net gain over B ≥ 20% while C's is not | the plan works, but the 8B can't write it |

**B is judged on its own.** B "produces the required improvement" when its net gain over A is
at least 20%, it has no rejecting regression, and the blind labels confirm. **If B does and C
does not add value, the result is B: C is not added because it exists.**

**Valid only if:**
- every arm has three complete runs;
- every run has at least 95% of its held-out turns comparable;
- the blind labels are in;
- the freeze verifies.

Otherwise the report says INVALID or NOT FINAL.

## 7. The report

`python -m evals.stagec --report docs/STAGE_C_RESULTS.md`, in the user's order:
1. A vs B;
2. B vs C;
3. C vs D;
4. blind-label agreement;
5. regressions;
6. latency;
7. unusable-plan rate;
8. failure categories, held-out and diagnosis turns separately.

The outcome is printed mechanically. **No arm goes to production on the result:** the user
decides whether it justifies a controlled implementation.

## 8. What the freeze records

`runtime/evals/pc/FROZEN.json`; `--review` prints it without writing.

- **This document:** sha256.
- **The corpus:** sha256 and shape.
- **The reference:** the corpus v2 sha256 and the 21 turns.
- **Analysis:** v1's fingerprint and its files.
- **Rubric:** fingerprint (`c4fac3a80008`).
- **Evaluator:** the judge, and the sha256 of `evals/stagec.py`, `fastdeep_v2.py`, `judge.py`,
  `run.py`, `phase2.py`, `fastdeep.py` and `integrity.py`.
- **The arms' exact texts:** the contract, the plan template, the planner prompt, and the
  persona and style lines removed.
- **The arms' files:** `explain_contract.py`, `chat_engine.py`, `response_policy.py`,
  `conversation_style.py`, `response_budget.py`, and both personality files.
- **Product code:** the tree fingerprint.
- **Models:** digests.
- **Runtime:** Ollama, GPU, driver, CPU, RAM, OS, Python and packages.
- **Arms:** environments and order.
- **Criteria:** the thresholds and their wording, and the blind-sample design.

**Corpus v2's freeze no longer verifies.** Stage C's switch is product code, which changes the
tree corpus v2 fingerprinted. The v2 report stands as generated. The exact v2-frozen tree was
archived before any change: `runtime/evals/pf2/frozen_tree.tar.gz`, sha256 `702ae03f…`.

## 9. Cost

| part | estimate |
|---|---|
| D's plans | 68 question chains on the 27B, about 6 min |
| one run of one arm | 43 scenarios, 68 turns: about 12 min |
| C's extra | +1.0–1.4 s on each explanation turn |
| generation | 12 runs, about 2.5 h |
| judging | 816 verdict sets, about 1.2 h |
| **total** | **about 3.8 h** unattended |

Without the 21 reference turns it would be about 2.2 h. They are included as approved, for
reporting only.

**VRAM:** the 8B alone in every arm, about 6 GB. The 27B (13.5 GB) runs only in the separate
pass for D's plans.

## 10. What this does not cover

- **Confusion follow-ups**, whose `elaborate` goal is unchanged.
- **Value, state and example turns.**
- **Long conversations.**
- **The speech path.**
- **Whether `concrete_first` is the right target** for every explanation: the rubric and the
  person's labels say it is.

## Appendix: the exact texts

Generated from `brain/explain_contract.py`. The freeze records that file's hash, so this text
and the code cannot differ.

<!-- APPENDIX:BEGIN -->
### B: the voice requirements of an explanation turn (English)

```text
Answer the current request in this response. Do not say that you will calculate, explain, check, or break something down later when you can do it now. Do not ask whether the user wants the answer after they already requested it. Compose a naturally shorter complete answer; never stop mid-sentence or omit the requested result to satisfy a length target.

HOW TO EXPLAIN THIS
Build the explanation around one concrete situation, in this order:
1. Open with a particular, everyday situation in which the answer can be seen happening (a specific person, object or number), and let that opening already carry the answer to their question. The situation is where the answer happens, not a warm-up before it; a comparison of the form "it is like X" is not a situation.
2. If it helps, say the general point in plain words as what that situation shows.
3. Use a technical word only when the answer needs it, and say what it means in everyday words in the same sentence.
4. Add more about how it works only when the answer is unclear without it.
At most 4 sentences and at most 130 words.
```

### B: the voice requirements of an explanation turn (Korean)

```text
지금 들어온 요청에 이 응답에서 답합니다. 지금 할 수 있는 계산이나 설명을 나중에 하겠다고 미루지 않습니다. 이미 요청하신 것을 다시 원하시는지 묻지 않습니다. 길이를 맞추려고 문장을 중간에 끊거나 요청하신 결과를 빼지 않습니다. 자연스럽게 짧게 씁니다. 반드시 습니다체로 씁니다. '~요'로 끝나는 해요체는 쓰지 않습니다. 확실하지 않은 것은 지어내지 않습니다. 영화 제목, 상품명, 가격은 확인된 것만 말합니다.

설명하는 방법
하나의 구체적인 상황을 중심으로, 이 순서로 설명합니다.
1. 답이 실제로 일어나는 모습이 보이는 특정한 일상 상황(구체적인 사람, 물건, 숫자)으로 시작하고, 그 시작 부분에서 이미 그 상황을 통해 질문에 대한 답을 말합니다. 상황은 답이 일어나는 곳이지 답 앞의 도입부가 아닙니다. "X와 같다"는 식의 비유는 상황이 아닙니다.
2. 도움이 되면, 그 상황이 보여 주는 일반적인 원리를 쉬운 말로 말합니다.
3. 전문 용어는 답에 꼭 필요할 때만 쓰고, 같은 문장에서 일상적인 말로 뜻을 풀어 줍니다.
4. 작동 원리는 그것 없이는 답이 분명하지 않을 때만 더 설명합니다.
4문장, 130단어 이내로 말합니다.
```

### B: persona rules removed on an explanation turn (English; the Korean file's rules at the same positions)

- Short by default, a sentence or two, unless the subject genuinely needs more room or the user asks for detail.
- Lead with the answer. State the fact or the final number first, then context only if it helps.
- If the user asks for an explanation, keep it simple and in order. No jargon, no over-explaining.

Korean:

- 기본은 한두 문장입니다. 주제가 정말 길이를 요구하거나 사용자가 자세히 요청할 때만 늘립니다.
- 답부터 말합니다. 사실이나 최종 숫자를 먼저 말하고, 도움이 될 때만 설명을 덧붙입니다.
- 설명을 요청하시면 순서대로 쉽게 말씀드립니다. 전문 용어도, 과한 설명도 쓰지 않습니다.

### B: style lines removed

- `Lead with the answer in plain spoken words.`
- `At most 4 sentences.`

### C and D: the plan block (English), appended after the contract

```text
PLAN FOR THIS ANSWER (follow it; do not read it out)
What they need to understand: {understand}
Situation to open with: {scene}
The answer, carried by that situation: {answer}
Words to explain in everyday terms if you use them: {terms}
How it works: {depth}
```

### C and D: the plan block (Korean), appended after the contract

```text
이 답의 계획 (따르되, 그대로 읽지 않습니다)
이해해야 할 것: {understand}
시작할 상황: {scene}
그 상황이 전하는 답: {answer}
쓰게 되면 일상적인 말로 풀어 줄 용어: {terms}
작동 원리: {depth}
```

### C: the planner prompt (system message, as sent for an English question; a Korean question ends "Write every value in Korean.")

```text
You plan how an answer will be explained. You do not write the answer.
Read the person's question (their earlier questions, if any, come first) and return one JSON object with exactly these keys:
"understand": what this person needs to understand for the question to be answered, in at most 15 words.
"scene": one particular, everyday situation in which the answer can be seen happening, with a specific person, object or number, in at most 20 words. Not a comparison of the form "it is like X".
"answer": the answer to the question itself, in plain words, in at most 20 words.
"terms": a list of at most 2 objects {"term": ..., "plain": ...} for words the answer cannot do without that a non-specialist may not know, each meaning in everyday words in at most 12 words; an empty list if there are none.
"depth": "brief" if the scene and the answer are enough, "mechanism" if one more sentence on how it works is needed.
Write every value in English.
```

The user message is `Earlier questions:` with one line per earlier question (when there are any), then `Question:` and the current question.

Call: qwen3:8b, format json, temperature 0, num_predict 256, think off; input: the person's questions so far, no assistant text.

Unusable: not JSON, not an object, no scene or answer, scene or answer longer than 40 words, or terms not a list: the turn falls back to B.

<!-- APPENDIX:END -->
