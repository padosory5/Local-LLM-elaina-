# Ambiguity + drift acceptance run

Frozen at `a906653c` on `phase4e-stabilization`. `main` untouched.

Eleven steps. **Do not test navigation again.**

| # | Say / do | What should happen |
|---|---|---|
| 1 | `open iss.washington.edu` | once, to get a page |
| 2 | Click an element with **one** match | `click_element status=clicked`, and she says she clicked *that* |
| 3 | Click an element with **two identical** matches | the question names *where* each one is, not `ABOUT, ABOUT` |
| 4 | `the first one` | resumes the **same click** on candidate 1 — never a search |
| 5 | Repeat, then `the middle one` | resumes on the middle candidate |
| 6 | Repeat, then `click any of them` | resumes on the first candidate |
| 7 | Cause pointer drift with no physical input | `re-baselining and continuing` — the action **survives** |
| 8 | Move the physical mouse once, deliberately | `reason=real_input` — it stops |
| 9 | `Can you try again?` | repeats the click, same element, same page |
| 10 | `I'm not clicking anything` after a drift stop | recognised as a dispute; runs nothing |
| 11 | Quit | clean |

## What the gate is

- ambiguity selection resumes the original click
- no selection ever becomes a Google search
- retries preserve the click target and candidate
- pointer drift without real input does not kill legitimate actions
- real physical input still does
- the result text stays about the requested action

## New log lines to watch for

```
[Page Action] waiting on a choice between 2 candidates for 'about'.
[Page Action] chose the first one in the page navigation: 'ABOUT' (e17)
[Input Watch] pointer moved with no input event; re-baselining and continuing.
              parked_at=(1574,448) now=(1581,596) tolerance=6
```

Step 4 is the one that was broken. It previously read:

```
[Reference] 'one of those' -> 'ABOUT'
[Computer Control] open_search target=ABOUT
```

That line should not appear again. If it does, the choice is not reaching
the page-action layer, and that is the finding.

## Step 7 — causing drift without touching anything

Drift now comes from Elaina's own focus calls, so it should happen on its
own during a click that changes window focus. If it does not appear at all,
that is fine and the step passes by absence — the point is that no action
dies from it. If you want to force it, clicking on another window's title
bar with the **keyboard** (alt-tab) while she works will move focus without
generating mouse input.

## Note on step 8

Real input is still an immediate, unconditional stop, and that is
deliberate. If it fires when your hands are genuinely away, capture the
`[Input Watch] takeover reason=real_input` line in full — the per-event
fields added last session (`lower_il`, `buttons`, `queue_lag`,
`distance_from_injection`) are what identify the source, and that is a
different investigation from this one.

## Not addressed, by choice

Carried forward: A-03 / S11R-03 invalid-host classification, A-08 wording,
S11R-11 Piper TTS exit code 1, S7-12 router latency, S7-10 geographic
containment.
