# bugs.md

Issues found using Elaina for real, not from benchmarks. Benchmarks say the
parts work; this says whether she is usable.

**Status:** dogfooding session not yet run. No issues recorded.

---

## Severity

| | Meaning | When it must be fixed |
|---|---|---|
| **P0** | Dangerous action, severe corruption, crash loop, unsafe machine behaviour | Immediately. Blocks release. |
| **P1** | Core task failure, unrecoverable state, major startup/shutdown failure, a feature becomes unusable | Before release. |
| **P2** | Wrong decision, annoying repeated behaviour, context mistake, unnecessary tool use, latency problem, recoverable incorrect behaviour | Fix if repeated or high-impact; otherwise record as a known limitation. |
| **P3** | Cosmetic or minor polish | After release. |

A P0 is anything that touched the machine when it should not have, or that
touched the *wrong* thing. When in doubt between P0 and P1, write P0 — it is
cheaper to downgrade than to miss one.

---

## How to record one

Copy this block. Partial is fine — the timestamp and what you said matter most.

```markdown
### [B-NN] Short title

- **When:** 14:32, ~40 min into session 1
- **Severity:** P2
- **I said:** "open the second one"
- **Context before:** she had just listed three hotels
- **Expected:** opens the second hotel
- **Actual:** asked which one I meant
- **Route/tool:** browser_control (from the [Router] line)
- **Reproduced:** yes / no / not tried
- **Suspected area:** references / router / planner / memory / voice / lifecycle
- **Log:** paste the [Timing], [Router] or [Task ...] lines around it
```

The console lines worth grabbing when something goes wrong:

| Line | Tells you |
|---|---|
| `[Router] <intent> (<confidence>): <reason>` | what she thought you meant |
| `[Timing] ... perceived=N.NNs` | where the wait went |
| `[Capability] Selected: ...` | which tool she picked and why |
| `[Task Planner] step=N ...` | each step of a multi-step task |
| `[Task Outcome] step -> ...` | success / retryable / terminal / cancelled |
| `[Reference] resolved to ...` | how "the second one" was resolved |
| `[Lifecycle] ...` | startup, degraded mode, shutdown |
| `[Response Guard] ...` | a repeat or echo was caught and regenerated |

---

## Open issues

_None recorded yet._

## Fixed

_None yet._

## Accepted known limitations

Carried in from earlier phases, already documented and deliberately not fixed
before release:

| # | Issue | Where |
|---|---|---|
| K1 | Analogical follow-up inherits the target but not the criteria — "do the same for keyboards" drops "under $300" | [MEMORY_CONTINUITY_BASELINE](docs/MEMORY_CONTINUITY_BASELINE.md) |
| K2 | `ChatEngine()` can hang at startup; contained by a 240s bound, root cause unknown | [FAILURE_RECOVERY_BASELINE](docs/FAILURE_RECOVERY_BASELINE.md) |
| K3 | Electron's close is a force-kill, so backend cleanup is skipped (nothing orphans) | [RUNTIME_BASELINE](docs/RUNTIME_BASELINE.md) |
| K4 | Cancellation reaches the planner between steps, not inside one — a running browser call finishes its own 60s bound first | [FAILURE_RECOVERY_BASELINE](docs/FAILURE_RECOVERY_BASELINE.md) |
| K5 | Routed turns cost ~3.4s perceived; decode-bound, not fixable without a schema redesign | [ROUTER_LATENCY_OPTIMIZATION](docs/ROUTER_LATENCY_OPTIMIZATION.md) |
| K6 | A deleted memory leaves an orphaned FAISS vector (skipped harmlessly) | [MEMORY_CONTINUITY_BASELINE](docs/MEMORY_CONTINUITY_BASELINE.md) |
| K7 | 3 router benchmark cases fail as judgement calls: `health_advice_3`, `screen_3`, `offer_3` | [ROUTER_BASELINE](docs/ROUTER_BASELINE.md) |

If the session hits one of these, note it against the K-number rather than
opening a new entry — that tells us it matters in practice, which is the thing
we do not yet know about any of them.
