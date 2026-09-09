# A6 — Attribute grounding

**What the phase was for:** when she says a thing has a price, a refresh rate,
a rating or a battery life, that number came from evidence.

**Instrument:** [`scripts/attribute_grounding_report.py`](../scripts/attribute_grounding_report.py)
against [`tests/attribute_matrix.json`](../tests/attribute_matrix.json), written
before the fix. Enforcement is [`brain/attribute_values.py`](../brain/attribute_values.py),
wired into the guard that already existed — the same module the report reads,
per Rule 1.

```bash
.venv/Scripts/python.exe scripts/attribute_grounding_report.py
```

| | Before | After |
|---|---|---|
| Invented attribute classes caught | **1 / 6** | **8 / 8** |
| True sentences left alone | — | **11 / 11** |
| Attribute matrix | — | **20 / 20** |
| Source disagreement reported | never | wired, both languages |
| Tests | 3118 | **3143** |

---

## Names were grounded. Numbers with units were not.

`brain/grounded_values.py` has stopped her quoting an invented **price** since
Phase 4, and it is good work — narrow on purpose, because most numbers in
conversation are perfectly fine to state from general knowledge. But its
definition of a checkable value was money, phone numbers and email addresses,
and nothing else.

Measured against a real monitor listing, with six invented replies:

```
invents a price            caught
invents a refresh rate     not caught
invents a response time    not caught
invents a rating           not caught
invents battery life       not caught
invents a weight           not caught
```

One in six. A fabricated 240Hz reads exactly like a retrieved one, and for
someone shopping for a monitor it is the number that decides the purchase.

**Nothing else had to change.** `attribute_values` emits tokens in the shape
`_digits` already produced, so the existing decision (`needs_correction`), the
existing repair (`correct_values`), the bilingual honesty line and the parked
browser-control offer all applied to the wider vocabulary unaltered. The phase
is one function's worth of vocabulary and a great deal of care about what *not*
to include.

---

## Two tiers, because the risk is not symmetric

This guard **removes text from replies.** Its dangerous failure is not "missed a
spec" — it is "deleted a true sentence". So the units are split by how ordinary
they are in speech:

**Spec units** — Hz, ms, mAh, GB, dpi, Mbps, nits, stars. These essentially
never occur in casual conversation. The unit alone makes it a claim.

**Ambient units** — hours, kg, inches, percent, km. These are ordinary words, so
they count *only* when the same clause also names what is being measured
("battery life", "weighs", "screen"). Without that rule this turn, which is real
and comes from the everyday dogfood arc, would have had its answer deleted:

> **you:** how long should it steep?
> **her:** Let it steep for 14 hours in the fridge.

A bare number is never a claim in either tier — inherited deliberately from
`grounded_values`, which already refuses to second-guess "three hotels" or
"2026". That restraint is what keeps this from becoming the disclaimer generator
A1 spent effort removing.

Half of `tests/attribute_matrix.json` is negatives, and that proportion is the
design.

---

## The asymmetry, found by the guard flagging a correct answer

The evidence read `27-inch QHD gaming monitor`. The reply said `a 27 inch
screen`. The reply's clause named a measured thing ("screen") and the evidence's
did not ("monitor"), so the evidence produced **no length claim at all**, the
reply's matched nothing, and a correct answer was flagged as invented.

The ambient-unit rule exists to protect *replies* from over-flagging. Applying it
to the evidence as well is how a guard ends up stripping the one value it should
have confirmed. So:

> **Evidence is read permissively; replies are read strictly.** A measurement in
> a retrieved document is a fact whatever prose surrounds it. A measurement in
> ordinary speech usually is not a claim at all.

That is the safe direction for both mistakes this can make, and it is now a
parameter (`spoken=`) with a test on each side.

A second false positive of the same family: `2,560x1440` parsed as `560x1440`,
so a resolution written with a thousands separator in one place and without in
another read as two different values. Fixed, and tested in all three spellings.

---

## Sources disagreeing is a state, not a race

The plan's words, and the thing that made it shippable was finding a seam that
needed no plumbing. Search evidence arrives as numbered blocks —
`[1] Title / Source: url / Snippet: …` — and that numbering is the only
per-source structure surviving into the guard. Splitting on it recovers the
sources.

So when two of them give different numbers for the same attribute, she says so:

> It runs at 165Hz. Sources disagree on that one — another says 180Hz.
> 자료마다 다릅니다. 다른 곳에서는 180Hz(으)로 나옵니다.

Said **only about an attribute the reply actually states**. A disagreement over
something she never mentioned is not worth a sentence, and saying it anyway is
exactly how honesty becomes a disclaimer footer. There are tests for the silence
as well as for the speech.

This is checked *before* the correction check, not inside it, because a value can
be perfectly well supported and still be disputed.

### Attribution stays internal

`source_of()` traces each value to the block that carried it — 165Hz from
bestbuy.com, 180Hz from lg.com — and that goes in the log. It is deliberately
**not** in the reply: "according to bestbuy.com" in every sentence is the
register A1 removed, and when a source is worth naming aloud is a conversational
question nobody has measured. The attribution makes a disagreement diagnosable
rather than merely reported.

---

## Unknown stays unknown

Already handled by the repair this phase plugged into, and worth showing because
it is the criterion most easily satisfied badly:

```
in:  The LG UltraGear is a solid pick. It has a 240Hz refresh rate
     and 14 hours of battery life.
out: The LG UltraGear is a solid pick. I looked and couldn't find that
     -- want me to open the site and check properly?
ko:  The LG UltraGear is a solid pick. 찾아봤지만 확인되지 않았습니다.
     사이트를 열어서 직접 확인해 드릴까요?
```

The true sentence survives; the two invented specs are gone; the honest line
*replaces* them rather than being appended as a footer; and the offer to go and
check is parked, so "yes" resolves against it.

---

## Exit criteria

| Criterion | Result |
|---|---|
| Zero attributes stated as fact without a source in the evidence | ✅ 8/8 caught, 20/20 matrix |
| Unknown attributes are said to be unknown, not guessed | ✅ replacement, not a footer |
| Conflicting sources produce a stated conflict | ✅ wired, both languages |
| No regression in conversation quality | ✅ **19/20 (95%)** English arcs |

Live, on the conversation-only arcs (desktop control off throughout — the
machine was in use): English **19/20**, Korean 9/12, mixed 6/8, for **34/40**
overall. The English number is within the ±3-turn band A5 measured for these
arcs, and none of the six findings is a grounding fault: they are
`service_phrasing`, `register_drift`, `request_restated` and `too_verbose` —
A1 and A2 classes.

The guard fired **once**, and correctly:

> **you:** 그 monitor 어때? 50달러 이하로 찾아줘
> **her:** 구매 전 스펙과 리뷰를 확인해 주십시오. 찾아봤지만 확인되지 않아서,
> 추측으로 말씀드리지는 않겠습니다.

An unverified value removed, and the honest sentence in its place — in Korean,
in 습니다체, as a replacement and not a footer. That is the whole phase working
in one live turn.

---

## Carried forward

- **Per-attribute confidence is not modelled.** `ExtractedItem` already carries
  `source_type` and `confidence` from the task extractor, and this guard proves
  an attribute *appears in the evidence* rather than weighing how good that
  evidence is. A browser-observed value and a search snippet are treated alike
  here. Ranking them is worth doing when something actually decides between them.
- **Korean evidence is unmeasured.** The unit vocabulary is Latin-script — Hz, kg,
  dpi — which is how specs are written in Korean text too, so the guard is
  expected to work; but no Korean-language product evidence has been run through
  it. The frames are bilingual and tested; the extraction is not.
- **The guard cannot see units it has never met.** Nits, mAh and dpi are in
  because someone thought of them. A unit nobody listed passes silently, which is
  the same shape as A2's quiet-detector problem — the mitigation is that a
  missing unit fails *open* (the value is stated) rather than eating text.
