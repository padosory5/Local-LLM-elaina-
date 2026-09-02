# Router latency optimization

One change at a time, measured before and after. The headline result is that
**the assumption behind the phase plan was wrong in a way that matters**: the
router's cost is not its prompt, and cutting the prompt would have bought
nothing.

---

## Baseline

| | |
|---|---|
| `router.route()` | **median 4.82s**, p90 5.58s (20 calls) |
| `route` stage in a full turn | 3.02s median (4E-I) |
| Router prompt | 15,417 chars (~3,854 tokens) |
| Router accuracy | 131/134 (97.8%), 0 dangerous false positives |
| Agency / Tool | 35/35 / 44/45 |

## The measurement that redirected the whole phase

The plan's priority 2 was "reduce router prompt/schema overhead", and the
prompt is enormous — 15.4k chars, of which the `computer_action` rule alone is
5,977 (39%). It looked like the obvious target.

Splitting the call into prefill and decode says otherwise:

```
prompt tokens : 3235
output tokens :  268
prefill time  : 0.06s      <- the entire 3,235-token prompt
decode time   : 5.31s      <- 268 tokens of JSON at ~52 tok/s
```

**Prefill is 1% of the call.** The RTX 5080 processes the whole prompt in 60
milliseconds. Every hour spent shrinking that prompt would have bought
roughly nothing, and would have risked the accuracy 4E-B showed is sensitive
to prompt edits.

The cost is entirely **decode**: 31 JSON fields, 268 output tokens, at this
model's decode speed.

Also checked, and also not the cause: `format="json"` constrained decoding is
**free** — 52.5 tok/s with it, 52.1 tok/s without. The schema does not slow
sampling; there is simply a lot of output.

---

## Optimization 1 — deterministic fast paths · **KEPT**

Turns that authorise nothing, ask nothing and name no subject were paying the
full ~4.8s routing call to be told they were conversation.

Two closed grammatical classes now bypass the router entirely:

- **bare acknowledgements** — "ok", "I see", "got it", "right", "makes sense",
  "알겠어" — guarded by *nothing outstanding*: any pending offer, consent,
  clarification or open recommendation is checked first and wins, because in
  that context "ok" means something;
- **cancellations** — "never mind", "forget it", "cancel that", "stop",
  "취소" — which drop everything outstanding and end the turn.

| | Before | After |
|---|---|---|
| `route` on these turns | ~4.8s | **~0.00s** (no model call) |
| Router accuracy | 131/134 | unchanged — the router is not touched |
| Suite | 1949 | **1957** |

Verified by test rather than by inspection: a counting client asserts these
turns reach the model **zero** times, and — the half that matters — that
`"what's the weather in Seattle"` still *does*. Raising an exception in the
fake client would have proved nothing: the router catches everything and
falls back to conversation by design, so the test would have passed for the
wrong reason.

The paired negatives are the point. `"ok open spotify"`, `"sure, find me a
hotel"`, `"stop the music"` and `"never mind that hotel, find another"` all
still route. The danger of a fast path is never the turn it was written for.

## Optimization 2 — prompt reduction · **NOT ATTEMPTED**

Cancelled by the measurement above, before any code was changed. Prefill is
0.06s; there was nothing to win.

## Optimization 3 — schema trimming · **NOT KEPT**

Investigated and rejected on arithmetic.

`request_explicitness` is the one provably dead field — measured in 4E-C, the
model answers `"direct"` for every input, and the real signal is computed
deterministically by `_REQUEST_SHAPE`. Its only remaining consumer tests for
`{"indirect", "statement"}`, which the model's value never is, so that branch
is already inert.

Removing it saves roughly **8 tokens of 268 — about 3%, or 0.15s.** Trimming
the free-text `reason` as well (its largest single field, ~35 tokens) would
reach roughly 16%, or 4.3s.

Neither approaches the 1.5s target, and `reason` is not free to remove: a
short justification field acts as brief chain-of-thought and is the
diagnostic that made four phases of router debugging tractable. Spending
router accuracy for 0.15s is exactly the trade the brief says not to make.

---

## Honest assessment against the exit criteria

| Criterion | Result |
|---|---|
| Router median materially reduced | **Partly.** ~4.8s → ~0.00s on fast-path turns; unchanged on routed turns |
| ≤1.5s median | **Not achieved, and not safely achievable** — see below |
| Router accuracy ≥95% | 131/134 (97.8%) ✅ |
| Dangerous false positives 0 | 0 ✅ |
| Agency ≥90% / Tool ≥90% | 35/35 / 44/45 ✅ |
| All suites green | 1957 ✅ |
| No unsafe shortcuts | every fast path is paired with a negative test ✅ |
| No information removed | nothing removed ✅ |

**Why ≤1.5s is not reachable by safe means.** The call is decode-bound at
~52 tok/s. 1.5s buys about **78 output tokens**. The schema currently emits
**268** across 31 fields, and the fields are load-bearing — `search_query`,
`computer_operation`, `information_freshness`, `verification_required`,
`speech_act` and the rest each have live consumers established across 4E-B
through 4E-F. Getting to 78 tokens is a schema redesign, not a trim, and it
would put every one of those benchmarks back in play.

The remaining options, both needing their own measured phase:

1. **Staged classification** (the plan's priority 3). A cheap first pass
   emitting `{intent, action_requested}` would cost ~0.3s. But most intents
   then need the full schema anyway — `web_search` needs freshness and the
   query, `computer_action` needs the operation and target — so the escalation
   rate would be high and two calls would often cost *more* than one. It helps
   `conversation` and `knowledge_question` turns, which the fast paths above
   already partly cover. Worth measuring; likely a wash.
2. **A smaller/faster router model.** The brief rules this out without a
   benchmark proving behaviour is preserved, and rightly: this router carries
   97.8% accuracy and zero dangerous false positives.

**Recommendation: stop here.** The safe win has been taken, the expensive
assumption has been disproved with evidence, and the remaining gap is a
correctness risk rather than an engineering oversight. With the release ten
days out, the next thing worth doing is dogfooding, not a schema redesign.

## What changed the perceived number

Perceived latency (transcript → first token) was 3.41s median, of which
routing was ~88%. On a fast-path turn that is now essentially zero. On a
routed turn it is unchanged, and the honest figure for those turns remains
~3.4s — dominated by 268 tokens of router JSON at 52 tok/s, which is now
measured rather than assumed.
