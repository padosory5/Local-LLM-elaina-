# Phase 4F.4 — router latency

Measured before and after, on the same workload, with the same model.

The short version: **model calls fell 29% and 40% of turns now cost nothing,
but p50 did not move** — because on this workload the median turn is still one
that genuinely needs routing. A tiered classifier that would have moved it was
built, measured, and **removed**: it cost 2 of 41 routing decisions.

---

## What the cost actually is

Established in 4E-I and re-confirmed here. The routing call is **decode-bound**:

```
prompt tokens : 3235      prefill 0.06s   <- 1% of the call
output tokens :  268      decode  5.31s   <- 31 fields at ~52 tok/s
```

Shrinking the 3,850-token prompt buys nothing. The only lever that moves the
number is **emitting fewer tokens, or not calling at all**.

## Method

`scripts/router_tier_benchmark.py` drives whole turns through
`ChatEngine._route_turn` with a real model and a counting client. The workload
is 20 turns taken from the dogfooding transcripts — musings, follow-ups,
corrections, commands, acknowledgements, small talk — in roughly the
proportions they actually occur, rather than an invented spread.

`scripts/router_latency_check.py` remains the tool for timing `route()` in
isolation; this one answers the different question of how many turns pay for
it at all.

## Before → after

| | before | after |
|---|---|---|
| turns never reaching the model | 3 / 20 (**15%**) | 8 / 20 (**40%**) |
| model calls (20 turns) | 17 | **12** (−29%) |
| router output tokens | 4,339 | **3,101** (−29%) |
| p50, all turns | 2.17s | 2.27s |
| p95, all turns | 2.36s | **2.50s** |
| p50, routed turns only | 2.19s | 2.30s |
| p95, routed turns only | 2.36s | 2.51s |

Per kind, after:

| kind | turns | reached model | median |
|---|---|---|---|
| follow_up | 3 | **0** | 0.00s |
| acknowledgement | 3 | **0** | 0.00s |
| cancellation | 1 | 0 | 0.00s |
| social | 1 | 0 | 0.00s |
| chat | 5 | 5 | 2.31s |
| command | 2 | 2 | 2.31s |
| knowledge | 2 | 2 | 2.25s |
| musing / request / correction | 3 | 3 | ~2.4s |

p50 and p95 are *within noise* of the baseline, and that is the honest
headline. With 12 of 20 turns still routed, the median turn is a routed one,
so the median cannot move until more than half of all turns bypass. What did
move is total work: a third less decoding, and eight turns that now answer
instantly.

## Tier 0 — deterministic · **KEPT**

Two more closed classes, added to the acknowledgements and cancellations that
4E-I already handled:

- **follow-ups about a result set that already exists** — "anything cheaper?",
  "which one would you choose?", "is it actually good?", "what about the
  second one?". The router can only ever answer *"conversation, and it is a
  follow-up"*; the answer lives in the session, not in the sentence.
- **bare acknowledgements while a recommendation is open but has produced
  nothing yet.** An open problem used to block this path, so "ok" two turns
  into a conversation about monitors paid a full call. It cannot mean "yes,
  go ahead" — every gate that could have asked something is checked first.

**The design mistake worth recording.** The first version returned a whole
`TurnRouting` and skipped the rest of the routing phase. Measured, that
skipped 4F.2's interaction decision as well, so the follow-up answered from
general knowledge instead of from the session's own results — a test caught
it. Tier 0 now substitutes for **the model call only**; recall, the
interaction decision and capability selection all run exactly as on a routed
turn.

Every pattern is paired with negatives, because the danger of a fast path is
never the turn it was written for:

```
anything cheaper?                          -> bypasses
anything cheaper in Seoul that has parking? -> routes  (names a place)
which one would you choose?                -> bypasses
which one would you choose for a 4K workflow? -> routes  (names a use)
is it actually good?                       -> bypasses
is it good for gaming under 500?           -> routes  (names a budget)
what about the second one?                 -> bypasses
can you pull it up?                        -> routes  (asks for an action)
```

## Tier 1 — a cheap first pass · **REMOVED**

Built as specified: a small-schema call emitting `{"kind": "chat" |
"knowledge" | "needs_more"}` (~10 output tokens, ~0.4s), trusted only when it
said the turn needed nothing, with a deterministic veto sending anything
instruction-shaped straight to the full router.

The arithmetic was good. On this workload it would have resolved ~9 of the 12
routed turns at 0.4s instead of 2.3s — a projected **58% cut** in routing
time, and a p50 of ~0.4s.

The accuracy was not:

| | routing checks |
|---|---|
| Tier 0 only | **41 / 41** |
| Tier 0 + Tier 1 | 39 / 41 |

Two failures, and fixing each surfaced another:

1. *"What's in the Sound Settings window?"* — the veto required a surface noun
   directly after a determiner, and this puts two words in between. A request
   to inspect a window came back as conversation. **This is the dangerous
   class**: a real action, silently answered instead.
2. Widening the veto fixed that and broke *"Split 650 proportionally among
   contributions of 100, 100, and 50."* — read as chat rather than escalated
   to `calculation`.
3. *"Why do currency exchange rates fluctuate?"* came back `conversation`
   rather than `knowledge_question` throughout, and sharpening the prompt did
   not fix it.

The pattern is the finding. A cheap classifier without the routing-rules block
cannot reproduce the full router's taxonomy, and each patch moved the error
rather than removing it. 4E-I predicted this ("a correctness risk rather than
an engineering oversight"); this phase now has the measurement behind it
rather than the estimate.

**Removed via `git checkout`, and 41/41 confirmed restored afterwards.** The
brief is explicit that the latency targets "are goals, not reasons to sacrifice
correctness".

## Against the exit criteria

| Criterion | Result |
|---|---|
| Simple conversational turns bypass the expensive router | ✅ 40% of turns, up from 15% |
| Obvious tool/action commands have a fast path | ⚠️ **No.** Commands still route (2.31s) — see below |
| Router p50 materially improved | ❌ **No.** 2.17s → 2.27s, within noise |
| Router p95 measured and documented | ✅ 2.50s all turns, 2.51s routed |
| Full semantic routing reserved for cases that need it | ✅ 60% of turns, all genuinely ambiguous |
| No significant regression in 4E routing reliability | ✅ 41/41 routing, 35/35 agency, 0 unrequested actions |
| Benchmarks compare before/after | ✅ this document, reproducible via the script |
| Model-upgrade decision postponed until architecture is measured | ✅ and now separately measured — see below |

### Why p50 did not move, stated plainly

p50 is a median over turns. With 40% bypassing, the median is still the 10th
of 20 sorted turns — a routed one. Moving p50 needs **more than half** of all
turns to bypass, or the routed turns themselves to get cheaper. Tier 0 cannot
reach 50% on this workload without claiming turns that genuinely need routing,
and Tier 1 was the way to make routed turns cheaper and failed on accuracy.

The honest position: the remaining 60% is *irreducible without either a schema
redesign or a different model*, both of which put 41/41 back in play.

### A command fast path was considered and not built

`open spotify` / `close discord` are 2 of 20 turns at 2.31s, and taking them
would reach exactly 50% bypass. It was left alone: matching `open <anything>`
would claim "open the door" and "open up about it", and the safe version needs
the installed-app catalog, which makes routing depend on machine state and on
a Start Menu scan. After Tier 1, spending accuracy for one turn in ten is not
a trade this phase should make. It is the first thing to revisit if 4F.4 is
reopened.

## The model-upgrade question

Measured this phase against `Qwen3.8-27B-i1-IQ4_XS` (27.3B, 13.5 GB):

| | qwen3:8b | 27B IQ4_XS |
|---|---|---|
| router p50 | **2.33s** | 7.16s (3.1×) |
| router p95 | **2.47s** | 11.93s (4.8×) |
| whole turn | **3.9s** | 16.7s (4.3×) |
| agency decisions | **35/35** | 31/35 |
| unrequested actions | **0** | **1** |
| tool selection | **45/45** | 42/45 |

A bigger model makes the number this phase is about between three and five
times worse, and loses the "don't act on a remark" guarantee that 4F.1's offer
policy is built on. The upgrade stays postponed, now on evidence.
