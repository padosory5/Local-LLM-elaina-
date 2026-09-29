# The evaluation rubric, and why it is shaped this way

**What this is.** The record of how `evals/rubric.json` became version 2: the
calibration that showed version 1 could not be trusted, what each
disagreement meant, and the design that replaced it. Read this before
changing a property. A definition that drifts without re-calibration
silently changes what every later number means.

## Version 1 against a person

20 replies and 59 labels, labelled blind against the judge's verdicts:
**68% agreement, Cohen's kappa 0.38.** Not enough to stand behind "model B
improved explanation quality".

| Property | Agreement | Kappa | What the disagreements showed |
|---|---|---|---|
| `answers_the_ask` | 82% of 17 | 0.60 | overloaded with correctness (Seattle/Seoul); asked of "Yeah." |
| `natural` | 33% of 9 | 0.07 | the judge read concise, correct replies as "textbook" |
| `plain_language` | 20% of 5 | −0.67 | no threshold for when a term counts as explained |
| `why_it_helps` | 50% of 6 | 0.18 | one name for two ideas, attached to questions it cannot apply to |
| `no_textbook_opening` | 50% of 4 | 0 | "Overfitting happens when…" and "The Taylor series is used to…" judged opposite ways |
| `concrete_first` | 88% of 8 | 0 | is an analogy ("like a bet on a stock") concrete? |
| others | 100% | — | 1 to 4 labels each: too few to mean anything |

The last row is its own finding. **A property with three labels has no
measurable agreement**, and version 1's calibration drew its sample without
regard to that.

## The decisions

**1. Properties apply by communicative goal, not by hand.** Every scored turn
declares a `mode`:

- fact, definition, formula, example;
- cause, purpose, how, intuition, confused, comparison;
- receipt, remark.

Every property declares the modes it applies to. A property that does not
apply is not scored, rather than forced to a yes or a no. That makes the
bad pairings structurally impossible: *why it helps* on "why do you lurch
forward", *answers the ask* on "Yeah." `tests/test_eval_corpus.py` holds
the rule.

**2. `n/a` only where the reply decides applicability.** That is
`notation_earned` alone ("n/a if the reply contains no formula"). Everywhere
else applicability is known from the question, so it is decided by the
mode. Allowing n/a there would give the judge an escape from every hard call.

**3. Splits and replacements**

| Version 1 | Version 2 | Why |
|---|---|---|
| `answers_the_ask` | `answers_the_ask` (attempted the requested task) + deterministic correctness | a reply of the right shape with a wrong value is two failures, not one |
| `answers_the_ask` on non-requests | `takes_the_turn_as_meant` | "Yeah." asks for nothing; the behaviour is receiving it without starting anything |
| `why_it_helps` | `says_what_it_solves`, for *example* and *purpose* only | for "why does it happen", giving the cause is already the ask |
| `natural` | `no_service_phrasing` + `leads_with_the_answer` | the observable parts; concreteness and metadata already have their own properties |
| `no_textbook_opening` | `leads_with_the_answer` | the failure is not "a definition first" but "anything before what was asked" |
| `plain_language` | `terms_explained` | with an explicit threshold |
| `stops_when_done` | merged into `only_what_helps` | the two flagged the same sentences |
| — | `accurate` (judged, explanations only) | a Korean Taylor-series answer described a pop musician, and no property could say so |

The why-mode itself was split into **cause** (bus, ice, inflation) and
**purpose** (CPU cache, "instead of the original function", compound
interest).

**4. Correctness is deterministic wherever the truth is known.**

- Clock times in each named zone, the gap between zones, any UTC offset the
  reply states, today's date, weekday and year: all computed from the turn's
  own recorded start time.
- Quantities in named units, within a tolerance, and arithmetic answers.

Checks are recomputed from stored replies whenever a report is made, so a
corrected corpus re-scores old runs.

The version 1 string checks were removed. "Must not contain UTC" said
nothing about behaviour, and failed a reply that was asked for the offset.
Whether metadata was exposed is now `only_what_helps`. Whether a stated
offset is true is `utc_offset`.

**5. Definitions carry a yes and a no example**, invented for the definition.
None is taken from the corpus, so the judge cannot learn the calibration
replies from its own instructions.

**6. Verdicts are tied to the exact wording.** Each verdict carries the
rubric's fingerprint. The report and calibration ignore verdicts given under
different words, so an edited definition cannot be averaged with the old one.

## Each disagreement, and where version 2 lands

Version 2 was designed from what the properties are *for* (the architecture
brief), not to reproduce either side's labels. Where it now disagrees with an
earlier human label, that is stated.

| Item | Version 1 | Version 2 |
|---|---|---|
| bus, `why_it_helps` (person yes, judge no) | wrong property for a cause question | not scored; the cause is `answers_the_ask` |
| bus, `plain_language` (person **no**, judge yes) | "inertia keeps them moving" | `terms_explained`: the same sentence says what inertia does, so **yes**. This follows the threshold proposed with the calibration, and goes against the earlier label |
| overfitting, `plain_language` (person yes, judge no) | "training data, noise, outliers" for "I'm not technical" | `terms_explained`: none explained, and the listener said they are not technical, so **no**. Also against the earlier label |
| inflation, `why_it_helps` | the judge itself said "not applicable" | not scored (cause) |
| Taylor example, `why_it_helps` (person yes, judge no) | "useful for calculating trigonometric values with high precision" | `says_what_it_solves`: names a task, not the difficulty removed, so **no** by the definition's own example. Against the earlier label; the brief's desired behaviour was "explains why approximation helps" |
| Seattle/Seoul, `answers_the_ask` (person no, judge yes) | the right form with wrong times | `answers_the_ask` yes (it attempted); `clock_times` and `hour_difference` fail deterministically |
| "Yeah." → "Understood.", `answers_the_ask` | no ask to answer | `takes_the_turn_as_meant` |
| six `natural` disagreements | concise, correct, "textbook" to the judge | replaced by observable properties; the textbook complaint now lands on `concrete_first` and `leads_with_the_answer` |
| overfitting / Taylor, `no_textbook_opening` | "Overfitting happens when…" vs "The Taylor series is used to…" | `leads_with_the_answer`: a definition first answers "what does it mean"; a general sentence before the example does not answer "give me an example" |
| derivative-as-bet, `concrete_first` | is an analogy concrete? | yes, if it is spelled out as something a listener can picture; "like a recipe" alone is not |

## What the calibration replies also showed

These are not rubric problems. Nothing measured them before.

- **"테일러 급수" was explained as a singer.** Two turns about Taylor series
  in the Korean trajectory described "a musician … in pop and rock".
  Measured now by `accurate`.
- **"What's a derivative?" is ambiguous.** The financial instrument is a
  correct answer. The scenario now asks about calculus, because it exists
  to measure adapting after confusion, not word sense.
- **The display rewrites notation.** A quadratic-formula reply showed
  `√(b^2 to 4ac)`: the dash rule read `2 - 4` as a range. That is the same
  defect `display_math_intact` measures; here it is in a real reply.
- **A reply ended mid-sentence**: "I've got your number, 206 555 0134. Is
  there."

## Version 2 against the labels (2026-09-24)

73 labels on 29 replies, drawn per property from replies the judge accepted
and rejected. **86% agreement, kappa 0.74** (version 1: 68%, 0.38).

The labels were proposed by Claude from the definitions alone, without the
judge's verdicts. The user reviewed them and changed none. This is
agreement between two models, with a person's sign-off. Two models can share
blind spots, so it is weaker evidence than a person labelling independently.

| Property | Agreement | Kappa | Bar (≥80%, κ≥0.4) |
|---|---|---|---|
| `answers_the_ask` | 86% of 7 | 0.72 | met |
| `takes_the_turn_as_meant` | 100% of 6 | 1.00 | met |
| `only_what_helps` | 100% of 7 | 1.00 | met |
| `no_service_phrasing` | 86% of 7 | 0.72 | met |
| `accurate` | 86% of 7 | 0.72 | met |
| `says_what_it_solves` | 100% of 7 | 1.00 | met, on one positive |
| `notation_earned` | 100% of 4 | 1.00 | met, small sample |
| `no_unrelated_remark` | 100% of 4 | — | no negatives exist to test |
| `leads_with_the_answer` | 71% of 7 | 0.46 | **not met** |
| `concrete_first` | 71% of 7 | 0.42 | **not met** |
| `terms_explained` | 71% of 7 | 0.36 | **not met** |
| `adapts_to_confusion` | 67% of 3 | 0.40 | **not met** |

**The disagreements were predictable.** Nine of ten fell on the 18 labels
flagged as judgment calls before scoring. The other 55 agreed 54 times. The
tenth concerns the same reply, and the same question, as a flagged one. Each
remaining gap is a specific ambiguity:

- **A reply about the wrong subject** (#1: 미분 answered as 파생상품). The
  labels scored it only under `accurate`; the judge also failed it on
  `answers_the_ask` and `leads_with_the_answer`. `accurate` names "the
  subject the person meant", and so does the idea of attempting the ask.
- **What counts as concrete.** Two analogies, split in opposite directions:
  "a bet on a stock's future price" (labels yes, judge no) and "like a
  magnifying glass on a function" (labels no, judge yes). The definition's
  "a familiar thing that is spelled out" does not separate them.
- **Whose vocabulary sets the threshold.** Is "polynomial" technical for
  someone asking about Taylor series (#3)? And does a reply that explains
  nothing meet `terms_explained` by default (#27)?
- **A restatement against a simplification** (#7). The reply moved "a bet
  on price movements", already said once, to the front. The labels called
  it a restatement; the judge called it a simpler approach. The earlier
  human label on the same exchange agreed with the judge.
- **Fields named before the example** (#8), whether that leads with the
  answer.
- **A contradiction as an error** (#19: two steeping times). And whether
  "준비해보시죠?" is a closing nudge.

## Version 3

These are the fixes calibration v2 pointed to. The user chose the reading
for `adapts_to_confusion`.

| Property | Change | The disagreement it settles |
|---|---|---|
| `answers_the_ask` | a reply about a different subject from the one the conversation makes clear has not attempted the task | #1: 미분 answered as 파생상품 |
| `accurate` | covers only whether claims are true; two statements that contradict each other count as an error; the subject is no longer part of it | #1, and #19's two steeping times |
| `concrete_first` | counts only a particular scene (someone doing something with particular things or numbers) or a worked example; a bare "X is like Y" does not, however familiar Y is | #7 and #13, which split in opposite directions |
| `terms_explained` | a term is technical unless the person used it or it is everyday vocabulary; a reply that uses the asked-about term without saying what it means is "no" | #3 ("polynomial"), #27 (a non-answer) |
| `leads_with_the_answer` | for a request for an example, naming the fields where it is used is not yet the example | #8 |
| `no_service_phrasing` | a closing nudge or question that asks for nothing needed counts | #19 ("준비해보시죠?") |
| `adapts_to_confusion` | must bring something the previous explanation lacked (a new example, analogy, picture or starting point); a simpler restatement is "no" | #7: the user chose this reading (b) |

**A leak in version 2, found while writing version 3.** Version 2 said its
examples were invented. Two were not:

- The `only_what_helps` example ("asked the year… Today is Thursday, the
  24th…") mirrored the `year_now` reply.
- The `takes_the_turn_as_meant` example ("'Yeah.' after rental listings")
  was a contamination scenario.

Both properties then scored 100% on calibration items that were those very
replies, so their perfect agreement may be inflated. Version 3 replaces both
examples, and replaces the Korean closing phrase in `no_service_phrasing`,
which was close to corpus text. `tests/test_eval_corpus.py` now fails if a
quoted example utterance appears in anything a scenario says.
`evals.calibration export` warns when a sampled reply resembles an example.
Both properties are re-calibrated with the rest.

**Calibration v3** covers the changed properties only, with 3 to 6 labels
each. It prefers replies no earlier calibration showed, since a reply read
with labels beside it cannot be labelled blind again, and ships with no
labels filled in.

## Version 3 against a person, labelled blind (2026-09-24)

45 labels on 24 replies, labelled by the user with no labels and no
verdicts in the file: **91% agreement, kappa 0.82.**

| Round | Labeller | Labels | Agreement | Kappa |
|---|---|---|---|---|
| v1 | the user, blind | 59 | 68% | 0.38 |
| v2 | Claude; the user reviewed and changed none | 73 | 86% | 0.74 |
| v3 (changed properties) | the user, blind | 45 | 91% | 0.82 |

| Property | Agreement | Kappa | Bar (≥80%, κ≥0.4) |
|---|---|---|---|
| `answers_the_ask` | 100% of 5 | 1.00 | met |
| `takes_the_turn_as_meant` | 100% of 5 | 1.00 | met (replies reused from v2) |
| `concrete_first` | 100% of 6 | 1.00 | met, on one positive |
| `adapts_to_confusion` | 100% of 3 | 1.00 | met (replies reused, 3 labels) |
| `only_what_helps` | 100% of 5 | 1.00 | met |
| `terms_explained` | 83% of 6 | 0.67 | met |
| `leads_with_the_answer` | 80% of 5 | 0.62 | met, at the bar |
| `no_service_phrasing` | 80% of 5 | 0.62 | met, at the bar |
| `accurate` | 80% of 5 | 0.62 | met, at the bar |

**The four disagreements.**

- **One is the judge departing from a definition.** #23 was a reply about
  the wrong subject that stated nothing false. The person followed version
  3's split: `answers_the_ask` no, `accurate` yes. The judge still counted
  the wrong subject as inaccuracy.
- **Three are threshold calls:**
  - is "density" explained by "takes up more space"? (`terms_explained`)
  - is "like calculating factorials or traversing trees" an example or an
    area? (`leads_with_the_answer`)
  - is a vague "추가 정보가 필요합니다" a stock nudge? (`no_service_phrasing`)

**What the numbers do not show.**

- **Sample sizes.** 5 or 6 labels a property means one disagreement moves
  agreement by about 17–20 points. Three properties sit exactly at the bar.
- **Rare positives.** `concrete_first` was confirmed on one positive. The
  baseline model almost never opens concretely, so agreement on concrete
  openings is barely tested.
- **Properties not re-calibrated blind.** `says_what_it_solves`,
  `notation_earned` and `no_unrelated_remark` were unchanged in version 3.
  Their only calibration is v2 (Claude's labels, the user's sign-off):
  one positive for `says_what_it_solves`, no negatives for
  `no_unrelated_remark`.

**For Phase 2.** The judge can score these properties. Where a model arm
improves a property whose positives were rare here -- `concrete_first`,
`says_what_it_solves`, `adapts_to_confusion` -- the claim should come with a
short confirmatory calibration: a few of that arm's new "yes" verdicts,
labelled blind. The positives are exactly what this calibration could not
test.

## The Phase 2 judge (2026-09-24)

The 27B that judged Phase 1 is also a Phase 2 contestant, so Phase 2 is
judged by `qwen3.6:35b-a3b`. It was calibrated against the same labels,
which are ground truth whoever the judge is (`evals.calibration judge`).

Plain JSON mode was not enough for this model: it returned its own
structure, with properties missing. Answers are now constrained by a JSON
schema (`evals/judge.py`): every property present, a reason before its
verdict, and n/a only where allowed. Verdicts are stored and read per judge
model, so two judges are never averaged.

**Against the v3 labels** (the user, blind; 45 labels): **87% agreement,
kappa 0.73.** The 27B scored 91% / 0.82 on the same labels.

| Property | 35B | Bar |
|---|---|---|
| `answers_the_ask`, `leads_with_the_answer`, `concrete_first` | 100% | met |
| `terms_explained` | 83%, κ 0.67 | met |
| `only_what_helps`, `no_service_phrasing` | 80%, κ 0.62 | met |
| `takes_the_turn_as_meant`, `accurate` | 80%, κ 0.55 | met |
| `adapts_to_confusion` | 67% of 3, κ 0.40 | **not met**: called "like a bet" new, though the previous reply said it |

**Against the v2 labels**, for the properties still worded as they were
then (Claude's labels, the user's sign-off; weaker evidence):

- `says_what_it_solves`: 86% of 7
- `notation_earned`: 75% of 4
- `no_unrelated_remark`: 100% of 4

**Known weaknesses of this judge.**

- It accepted a Korean reply's false claim ("테일러 급수는 일반적으로
  학년을 의미") as accurate.
- Once, it called a time-zone name "necessary context" for a plain time
  answer, which the rubric says it is not.

Phase 2 claims about `adapts_to_confusion`, and about `accurate` on Korean
replies, carry these caveats.

## When a property is reliable enough

Calibration version 2 samples per property (7 labels for each property a
Phase 2 claim would rest on, 4 for the rest). It draws both replies the
judge accepted and replies it rejected, so agreement cannot be inflated by
two raters who both say yes.

The bar for using the judge in Phase 2:

- **Overall**: kappa of at least 0.6.
- **Each property a claim rests on**: agreement of at least 80% and kappa
  of at least 0.4.
- **A property below its bar** is reported but excluded from any headline
  claim until its definition is fixed and re-calibrated.
