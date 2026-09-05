# Page-element acceptance run

Frozen at `cb1c9632` on `phase4e-stabilization`. `main` untouched.

**Do not test navigation again.** Nine steps, all on one page.

> **A note on step naming.** Last sheet said "click an element with two
> identical matches" and that got read aloud to Elaina as if it were the
> command. Say a **real element name** from the page — `click About`,
> `click Calendar`. The bracketed text below is a description for you, not
> something to say.

| # | Say | What should happen |
|---|---|---|
| 1 | `open iss.washington.edu` | once, to get a page |
| 2 | `click Apply` *(or any name that appears once)* | clicks it; she names what she clicked |
| 3 | `click About` *(a name that appears twice)* | asks using **neighbours**: "the one next to X and the one next to Y" |
| 4 | `the one next to <X>` | clicks **that** one |
| 5 | `click About` again, then `the second one` | position still works |
| 6 | `Can you click the first one?` *(straight after 5)* | still resolves — **never** a Google search |
| 7 | `click Calendar`, then `click any of them` | picks one and clicks it |
| 8 | Move the physical mouse once during a click | `reason=real_input`, it stops |
| 9 | Quit | clean |

## What the gate is

- the element clicked is **inside the page**, never browser chrome
- only elements that are **actually visible** are offered as candidates
- an ambiguous question names **where each candidate is**, usefully
- a follow-up choice still works after one has been made
- no choice ever becomes a web search
- real physical input still stops a run; drift alone does not

## New log lines

```
[Page Action] waiting on a choice between 2 candidates for 'about'.
[Page Action] chose the one next to Home: 'ABOUT' (78d2c290-e25)
[Page Action] click_element target='about' status=clicked resolved='about'
[Input Watch] pointer moved with no input event; re-baselining and continuing.
```

Two lines that should **not** appear again:

```
[Reference] 'one of those' -> 'ABOUT'
[Computer Control] open_search target=ABOUT
```

## What changed, in one line each

- **Neighbours.** Candidates are described by what sits beside them — same
  row first, then directly above/below — and a landmark all of them share
  is dropped as useless. Position is the fallback.
- **Visibility.** The observer already walked only the page document and
  dropped anything outside the viewport; it now also asks the page what it
  has hidden (collapsed menus, inactive tab panels). An observer that can't
  answer keeps the element rather than emptying the page.
- **Geometry.** The screen observer always had a rectangle per element; the
  planner's page model was throwing it away. It carries it now, zero where
  an observer has none.
- **Drift.** Navigation types into a real address bar with the real cursor
  and had no interruption window, so drift was the only signal. It has a
  run scope now.
- **The choice survives.** Answering "which one?" no longer consumes the
  candidates.

## Known and unchanged

If step 3 offers position rather than neighbours, the two links have no
distinct nearby label — that is the honest fallback, not a failure. Worth
capturing the page if it looks wrong.

Carried forward by choice: A-03 invalid-host classification, S11R-11 Piper
TTS exit code 1, S7-12 router latency, S7-10 geographic containment.
