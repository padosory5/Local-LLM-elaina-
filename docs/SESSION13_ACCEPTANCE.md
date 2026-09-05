# Page-interaction acceptance run

Frozen at `0e070289` on `phase4e-stabilization`. `main` untouched.

Nine steps, in order. Do **not** rerun the whole browser suite — navigation
has enough evidence.

## The steps

| # | Say / do | What should happen |
|---|---|---|
| 1 | `open iss.washington.edu` | direct `open_url`, `target_verified` |
| 2 | `click About on this page` | direct target is **`About`**, not `About on this page` |
| 3 | `click Calendar on this page` | direct target is **`Calendar`** |
| 4 | Force an ambiguous click | "I found more than one X item — *a, b*. Which one do you mean?" |
| 5 | Move the physical mouse during one click | it stops, and says so |
| 6 | `Can you try again?` | repeats **the click**, on the same element and page |
| 7 | Repeat step 5's click, hands **completely away** | compare the `[Input Watch]` records |
| 8 | `openiss.washington.edu` | fused URL stays direct — never a pending-offer acceptance |
| 9 | Quit | clean |

## Step 4 — forcing the ambiguity

On the ISS site, a term appearing in both the nav bar and the page body works:
`click Housing`, `click Students`, or `click Contact`. Any element name that
occurs more than once will do it.

The failing behaviour to watch for is the old one: search/recommendation
language ("I couldn't get actual listing names out of that search…"). That
should now be impossible — a structured browser result is the last word.

## Steps 5 and 7 — the input-watch comparison

**This is the one that matters, and it is the one thing I could not settle
locally.** No threshold was changed. What changed is that the log now
carries enough to identify the source.

Two measurements taken here first, so they do not have to be re-derived:

- `SendInput` moves arrive **flagged**, 20 of 20, and an idle machine
  produces **zero** hook callbacks. A recorded unflagged event is a real
  event; `real_mouse` is not background noise.
- `SetCursorPos` produces **no hook events at all** while still moving the
  pointer (measured: 946,499 → 740,400, zero callbacks). pywinauto's
  `set_focus` moves the cursor that way, to (-10000, 500). So it **cannot**
  cause `real_input` — that hypothesis is ruled out — but it is invisible to
  the hook and would appear as pointer drift.

### What to capture

Each takeover now prints one line. Save **both** runs verbatim:

```
[Input Watch] takeover reason=real_input mark=… parked_at=(x,y)
  N unflagged event(s) after mark;
  first=(mouse/move at=(931,502) flags=0x00 lower_il=False buttons=0x0
         extra=0 queue_lag=12ms since_injection=1840ms
         injected_at=(500,400) distance_from_injection=431
         action=click_element:About);
  kinds=[…]; action_counters={…}; lifetime_counters={…}
```

### Reading the two records side by side

| Field | Hands away (step 7) | Hand on mouse (step 5) |
|---|---|---|
| `what` | anything but a plain move stream is suspicious | `move`, many of them |
| `buttons` | `0x0` | `0x0` moving, `0x1` dragging |
| `lower_il` | `True` ⇒ **another program injected this**, not a person | `False` |
| `queue_lag` | large ⇒ the event happened **before** the action and was processed late | small |
| `distance_from_injection` | small ⇒ it is at Elaina's own pointer, so likely ours | large |
| `since_injection` | just over 350ms ⇒ our own echo fell outside the window | seconds |
| `action_counters.real_mouse` | a handful ⇒ one stray event; hundreds ⇒ a stream | hundreds |

`lifetime_counters.real_mouse` answering the earlier question directly: it is
**cumulative for the whole session** and counts every unflagged mouse
message including each `WM_MOUSEMOVE`. One physical swipe is on the order of
100–200 of them, so 4457 across a session is ordinary human use — which is
exactly why it could not settle anything. `action_counters` is the number to
read now.

Please do not guess from step 7 alone. The comparison is the evidence.

## Not addressed, by choice

Carried forward unchanged and previously ranked below this work: A-03 /
S11R-03 invalid-host classification, A-08 wording, S11R-11 Piper TTS exit
code 1, S7-12 router latency, S7-10 geographic containment.
