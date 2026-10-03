# Stage C: why both models' explanations miss the shape

*2026-10-01. Diagnosis only: nothing is changed, production stays FAST-only, the routing result
stands. Data: the frozen corpus v2 runs (`runtime/evals/pf2`). The numbers come from
`python -m evals.stagec_diagnosis`, which is read-only. The replies were also read by hand.*

## 1. The 21 turns, grouped by failure

A failure counts when it holds in 2 of 3 runs. "27B" is the 27B writing under the 8B's reading
(SPLIT on a first turn, LATE on a second). "DEEP" is the 27B reading as well.

| group | turns | FAST | 27B | DEEP |
|---|---|---|---|---|
| **not concrete first and terms unexplained**, both models | 19: GPS, Wi-Fi, lift, photosynthesis, TCP, garbage collection (t1), battery (t0, t1), jet lag (t0, t1), rainbow, heat pump, black hole, sky, entropy, placebo, fridge, earthquake, magnet | both | both | both on 16 (jet lag t1 also fails leads-with-answer); lift passes both; rainbow and heat pump pass concrete-first |
| not concrete first, terms explained by the 27B | ETF vs mutual fund (t1) | both | concrete-first only | none |
| not concrete first, plus purpose ("why does it matter") | supply chain | concrete-first, leads-with-answer, says-what-it-solves | concrete-first, terms | concrete-first, says-what-it-solves |

Across all 21 turns:

| failure category | FAST | 27B | DEEP | shared by FAST and 27B |
|---|---|---|---|---|
| concrete-first | 21 | 21 | 17 | 21 |
| terms-explained | 20 | 20 | 18 | 19 |
| leads-with-answer | 1 | 0 | 1 | 0 |
| answers-the-ask | 0 | 0 | 0 | 0 |
| unnecessary detail | 0 | 0 | 0 | 0 |
| other (says-what-it-solves) | 1 | 0 | 1 | 0 |

The replies answer the question, on target and correctly: no answers-the-ask failure and no
excess detail. **What fails is the shape:** the order, and the vocabulary.

**How far to trust the judge here.**
- *concrete_first.* Calibration found the judge over-credits it, never under-credits
  (docs/EVAL_RUBRIC.md: all five disagreements were the judge accepting an analogy). Reading the
  replies confirms every "no": none of the 189 replies opens with a scene.
- *terms_explained.* It meets the reliability bar (83%, κ 0.67). Reading run 1, though:
  - **the 27B's** "no" verdicts are almost all real jargon: triangulating, Rayleigh scattering,
    solid-electrolyte interphase, suprachiasmatic nucleus, electron spin;
  - **about a quarter of FAST's** look wrong. The cited words were the user's own (placebo,
    garbage collection), everyday (gravity), or defined in the reply ("lift, the force that
    counteracts gravity").

## 2. The repeated structure

The same template, in both models and both languages:

> general answer → mechanism → "For example, …" → a closing line on why it matters

| measure (all 189 replies) | FAST | 27B | DEEP |
|---|---|---|---|
| illustration in the first sentence | 0 | 0 | 0 |
| illustration in the last sentence | 21 | 27 | 32 |
| no illustration | 18 | 21 | 15 |
| median sentences | 4 | 3 | 4 |
| median words (budget 130) | 50 | 47 | – |

Three patterns stand out:

- **The example is decorative.** It is an instance of use, not a scene that carries the
  mechanism: "a Boeing 747 uses this principle", "in Java…", "Japan is on a plate boundary",
  "look at the sky on a sunny day".
- **Jargon grows with knowledge.** The 27B names the mechanism precisely, with its technical
  term. That is why it does not fix terms-explained.
- **The starting point is the subject, not the person.** "GPS determines location…",
  "Entropy is a measure of…". An intuition request ("What does entropy *actually* mean?") gets
  the textbook definition.

## 3. Generation, or the pipeline?

**After generation: not the cause.**
- The 27B's replies were never changed after it wrote them (0 of 63), and they fail like FAST's.
- 16 of FAST's 63 replies were changed (her_voice, closing_offer, final_form,
  append_recommendation, length_rewrite). Every one of their drafts already opened with a general
  statement. The changes shortened or re-registered the reply, or appended an offer.
- One side defect: `append_recommendation` attached "I can pull up a refrigerator if you want."
  to an explanation of how fridges work.

**Before generation: a large part of the cause.** For "How does GPS know where I am?" the 8B
receives about 1,940 words. About 30 of them concern how to explain. The rest comes from four
layers, each written for something else:

| layer | what it tells the model |
|---|---|
| persona, knowledge section (written for values and calculations) | "Lead with the answer. State the fact… first, then context only if it helps." "No jargon, no over-explaining." |
| persona, voice section | "Short by default, a sentence or two" |
| voice response requirements | "Give the requested result before… background"; at most 6 sentences; then the only explanation-specific sentence: "say why it happens, what it is for…, and show one concrete case" |
| style contract for the answer act ("how to sound") | "Lead with the answer in plain spoken words." "At most 4 sentences." |

Three layers say "answer first", and the explain goal asks for the example last. Only the `state`
shape mentions terms at all; the `explain` shape, which 20 of the 21 turns received, does not.
Nothing anywhere says which words this listener needs explained, or where the explanation
should start. About 1,000 words describe agents and capabilities irrelevant to a knowledge
answer.

The models resolve these instructions the same way, faithfully. A larger model follows them
with more knowledge, which here means more jargon.

**The model itself.** Both default to definition-first, and the 27B to precise terminology.
Neither is incapacity:
- in the person-labelled Phase 2 and 3 replies, the 27B opened concretely on 7 of 13;
- on v2's confusion turns, where the `elaborate` goal names a new example or an analogy, the 27B
  does change its starting point. **The models do what the instructions converge on, and the instructions converge on
"general answer first, about four sentences, example optional".**

## 4. Would an explicit explanation goal help?

The useful split is between two hypotheses:
- **H1, one owner of the shape:** a single contract replaces the four conflicting layers on
  explanation turns. No new call.
- **H2, per-question content:** a plan for *this* question (starting scene, core answer, terms to
  gloss with their plain meaning, whether an example helps, depth). It needs a planning step.

| failure | H1, one owner | H2, a plan per question | why |
|---|---|---|---|
| concrete-first (21/21) | plausibly, in part | plausibly | H1 removes the "answer first" conflict. Only H2 supplies the scene, and an empty "open with a scene" instruction tends to produce a generic one or a bare analogy, which the person doesn't count. |
| terms-explained (20/21) | unlikely | plausibly | The failure is not knowing *which* words this listener lacks. The 27B, with more knowledge, fails as often. That is content a generic contract can't carry. |
| starting point (seen in reading) | no | plausibly | Only a per-question field can say "start from their phone, not from GPS". |
| leads-with-answer, answers-the-ask, detail (0–1 failures) | risk | risk | Not failing today. A scene opening can delay the answer, so these are the regressions to guard. |

**Two risks that could falsify H2:**
- The 8B may plan as badly as it writes: it would choose "satellites" as the scene and miss
  "triangulating" as a term.
- A plan in the prompt is one more source to reconcile. If it conflicts with the persona's
  "answer first", the model may reconcile it the same way.

## 5. Proposed offline experiment (not built)

**Plan-conditioned explanations: one experiment, four arms, the 8B only.**

| arm | what changes on explanation turns | extra calls |
|---|---|---|
| A, base | nothing: production FAST | none |
| B, one contract | the four layers' order and length lines are replaced by a single explanation contract written once from the rubric definitions | none |
| C, plan (8B) | B, plus a plan from one structured 8B call: kind, opening scene, core answer, up to 2 terms with plain meanings, example useful yes/no, sentences | 1 × 8B, on explanation turns only |
| D, plan (27B, ceiling) | B, plus the same plan written by the 27B in a separate offline pass | none at generation time |

What each comparison answers:
- **B against A:** is the conflict itself the cause?
- **C against B:** does per-question content add anything?
- **D against C:** is the 8B a good enough planner?

Protections against overfitting the prompt to the evaluation:
- A **new held-out set**, written and frozen before any arm runs:
  - 24 scenarios, 12 English and 12 Korean, covering cause, how, purpose and intuition;
  - 18 single first explanations, and 6 explanations followed by a deepening question;
  - 30 scored turns in all.
- The 21 diagnosis turns are reported apart, as in-sample only.
- The contract text and plan schema are frozen at the start and **not iterated on results**.
- The frozen analysis decides labels (2 of 3, tiers).
- `concrete_first` is below the agreement bar, so a blind person-label sample of about 20 pairs
  decides it.
- Three runs per arm, alternating, in one session.

## 6. Cost

| | A, base | B, one contract | C, plan (8B) | D, plan (27B) |
|---|---|---|---|---|
| extra latency per explanation turn | – | ≈ 0, or +0.1–0.2 s if term glosses add 10–25 words (8B: 129 tokens/s) | **+1.0–1.6 s**: about 100–150 plan tokens plus prompt reading (the router's call is 2.4 s for about 265 tokens) | same as B at generation time |
| other turns | – | unchanged | unchanged | unchanged |
| VRAM | 8B resident, about 6 GB | same | same, no second model | offline only: one 27B pass (13.5 GB alone, about 5 min) |
| production candidate | – | yes | yes | no: a 27B planner costs the 8 s swap |

**Wall time:** 4 arms × 3 runs × about 43 scenarios is about 2 h of generation, plus about 1 h of
judging and the person's blind sample, about 20 pairs. Without the 21 in-sample turns it is
about 1.5 h in all.

## 7. What rejects the hypothesis

All of these are on the held-out turns, and the thresholds are fixed before the run.

1. **The plan adds nothing beyond one contract.** C's stable gains over B on concrete-first or
   terms-explained, less its stable losses, cover fewer than 20% of the held-out explanation
   turns (fewer than 6 of 30).
   - If B alone clears that bar against A, the answer is "one owner, no plan, no new call".
2. **The plan breaks substance.** More than 1 stable loss against A on answers-the-ask, accurate
   or must-contain-any, or more than 2 on leads-with-the-answer.
3. **The gain is the judge's.** In the blind sample, the person confirms concrete-first on fewer
   than 70% of the replies the judge credited.
4. **Too costly for production.** C adds more than 1.5 s p50 on explanation turns, or the 8B's
   plan is unusable (malformed, or missing the opening) on more than 5% of turns.
5. **The idea works but the 8B can't plan it.** D clears criterion 1 and C does not. That rejects
   C for this hardware; it does not justify a 27B planning step.

## What this does not settle

- Whether concrete-first is the right target for *every* explanation is a rubric question, not
  a model one. The rubric and the person's labels say it is.
- The `append_recommendation` misfire is a separate pipeline defect, noted and not fixed.
