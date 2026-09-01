# Tool-selection baseline

Which surface satisfies the request: none, a search, a page, or the machine.

```bash
.venv/Scripts/python.exe scripts/live_tool_check.py
.venv/Scripts/python.exe scripts/live_tool_check.py --kind browser_control
```

Runs the same chain production does, in the same order:

```
route -> goal_intent.read -> interaction.decide -> capability_selection.select
```

| Date | Model | Scenarios | Correct | Research→browser | UI false positives |
|---|---|---|---|---|---|
| 2026-09-01 | `qwen3:8b` | 45 | 31 (68.9%) | 1 | 1 |
| 2026-09-02 | `qwen3:8b` | 45 | **43 (95.6%)** | **0** | **0** |

Stable across three consecutive runs with identical mismatches.
**Phase 4E-D exit criteria met.**

Three numbers, and the last two have hard targets rather than percentages:
mode accuracy is a quality measure, while a research request that reaches for
the browser, or Windows UI control driving the machine on a turn that
authorised nothing, are defects.

## Coverage (45 scenarios)

| Kind | Cases | Result | What it pins |
|---|---|---|---|
| `no_tool` | 7 | 6/7 | stable facts, opinions, arithmetic need no tool |
| `remark` | 4 | 4/4 | a statement reaches no surface, whatever it mentions |
| `web_search` | 11 | 11/11 | research, discovery, comparison, price, news |
| `browser_control` | 8 | 8/8 | clicks, forms, scrolling, live availability |
| `ui_control` | 7 | 7/7 | local apps, windows, files |
| `screen` | 2 | 1/2 | observation vs image analysis |
| `followup` | 3 | 3/3 | a short imperative inherits the live browser surface |
| `boundary` | 3 | 3/3 | browser-sounding words that are not page actions |

---

## What was wrong

### 1. The surface map keyed on the wrong field — *deterministic correction layer*

`_MACHINE_CAPABILITY` mapped `route.intent` to a capability and carried
`browser_action`, `browser_tab` and `browser_search` as intents.

**The router has never emitted any of those as an intent.** Every machine
request arrives as `intent="computer_action"` and carries the distinction in
`computer_operation`. All three keys were unreachable, so *every* page action
was filed as Windows UI control:

| Request | Chose | Should have chosen |
|---|---|---|
| "Click the Sign in button on this page." | `ui_control` | `browser_control` |
| "Fill the search box on this hotel page…" | `ui_control` | `browser_control` |
| "Open youtube.com in a new browser tab." | `ui_control` | `browser_control` |
| "Click the first result." (browser in front) | `ui_control` | `browser_control` |

Eight of eight browser cases and three of three follow-ups. Verified against
`feature_matrix.json`: no case expects any of those three intents, while
thirteen distinct `computer_operation` values are expected.

Fixed with `_SURFACE_BY_OPERATION`, consulted before the intent map. A side
benefit worth stating: this layer now *inherits* the router's
`ui_action`/`browser_action` corrections from 4E-B instead of re-deriving
them, so the two cannot disagree.

Two unit tests asserted the dead mapping and were updated to drive the real
path — not relaxed. A new test asserts no browser intent survives in the
label map, and another asserts every executable operation has a surface.

### 2. A domain name read as a verb — *tool metadata*

```
"What do reviews on booking.com say about the Peninsula?"  ->  browser_control
```

`_AVAILABILITY` matches `\bbook(?:ing)?\b`, and **"booking.com" contains
"booking"**. The domain was read as an availability request, which made the
turn *live state*, which is exactly what the browser is for. A research
question was answered by driving a page — the failure the brief names.

Fixed by stripping host names before the live-state tests. A host says
*where*, never *what*. Deliberately general rather than a fix for one site:
`expedia.com`, `hotels.com` and any other domain that is also a verb had the
same problem waiting.

### 3. Mentioning a site counted as naming one to operate — *tool metadata*

`_NAMES_A_SURFACE` listed a bare domain as evidence that a page should be
driven. That is the rule the brief states outright — browser control is not
warranted just because a request mentions a website — encoded backwards.

The bare-domain alternative is gone. A domain with an actual verb ("open
booking.com and check the price") still matches, on the verb.

### 4. The browser could not win when it was the only thing that could work

Raising the browser's fit to 0.95 was not enough on its own: a search still
outscored it on cost (0.80 vs 0.76), so "open booking.com and check the
price" came back as a search. The other side of the same fact was missing —
a lookup cannot operate a page at all.

Scoped, on the second attempt, to turns that actually need something
external. Without that guard it took "How do I open Spotify myself?" — a
question *about* an action, needing nothing opened — straight to the browser.

---

## Remaining failures (2)

Reported, not hidden, and neither is weakened in the matrix.

| Case | Cause | Assessment |
|---|---|---|
| `nt_worldcup` — "Who won the World Cup in 2018?" → `web_search` | **router** marks it as needing external evidence | Against the stated policy (a non-current question needing no external information), but defensible: the project's grounded-value layer prefers a checked source over a recalled one. Costs a search; risks nothing. Low priority. |
| `sc_display` — "Analyze what is currently open on my main display." → `ui_control` | **router**, already tracked in [ROUTER_BASELINE.md](ROUTER_BASELINE.md) | Comes back `describe_window` rather than `screen_analysis`. Needs an intent-level change, not a surface correction. Low value. |

---

## Compound strategies

Supported and unchanged. `search → shortlist → verify` is the task planner's
`discover` / `verify` split, and the escalation to a page is driven by
`live_state_required` rather than by the tool choice itself — which is why
`bc_availability` ("rooms available for the 18th") reaches the browser while
`ws_hotels` ("good hotels in Seoul") does not, from the same starting intent.

## Note on the numbers

`no_tool` and `screen` each carry one known failure, so the headline is 95.6%
rather than 100%. That is the honest figure and it is left that way; the
benchmark earns its keep by holding the line, so treat a drop as the signal.
