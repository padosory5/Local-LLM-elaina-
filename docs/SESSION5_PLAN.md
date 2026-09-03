# Session 5 — release gate, second attempt

Short and targeted. Session 4 failed the gate and the fixes for it changed
code, so session 4 no longer counts as validation of anything.

**No code changes during this session.** If something fails, it is recorded
and the session ends there.

---

## The frozen state

| | |
|---|---|
| Branch | `phase4e-stabilization` |
| Tests | 2201, all green (1 expected failure) |
| Live routing | 41/41 |
| Consent walls | 5/5 |
| `main` | untouched at `6f427aff` |

---

## Why this one is shorter

Session 4 was a full pass and found five things. Four are fixed, and all
four fixes are one or two days old and unused by a person. This session
exists to check those four and confirm nothing near them broke — not to
re-walk everything.

The pattern worth naming, because it has now appeared five times in four
sessions and in five different layers: **held state beating the current
turn.** A pending offer, a stale anchor, a held subject, a locale default,
the model's memory of mishearing you. Every one of them was a case of the
system trusting something it already had over something you had just said.

Four of session 4's five failures were that same shape. If a sixth turns
up, that is the thing to look at first.

---

## Startup

```bash
cd C:/Users/pados/localProj/elainaAI && .venv/Scripts/python.exe main.py 2>&1 | tee runtime/session5.log
```

`debug.print_memory: true` — still no evidence either way after four
sessions.

---

## Part 1 — the five from session 4

### S4-01 — Seattle time *and* the offset

**Say:** "What time is it in Seattle right now?"

**Should:** the correct local time, **and** "16 hours behind" (not 13, not
anything else). No `[Tool] Searching web for:` line.

The time was right last session and the *relationship* was wrong, so check
both numbers. Try London too, where the answer should be 8 hours behind.

### S4-02 — UW rental query geography

**Say:** ask for a studio near the University of Washington with a budget.

**Should:** `[Query]` with no `in South Korea` in it.

**Also check the other direction:** ask for something with no place in it
at all ("where can I buy packing peanuts") — that one **should** still say
South Korea. Losing the market entirely would be the over-correction.

### S4-03 — UW I-20 contact lookup

**Say:** "Can you find me the contact information for the University of
Washington about my I-20?"

**Should:** a query containing *University of Washington*, not "I-20 form
processing" on its own.

### S4-04 — "open the website"

**Say:** get her talking about a specific site, then "use browser control
and open the website".

**Should:** the site she has been discussing. Not an Example Domain page.

**Over-correction to watch:** if you name a site outright ("open
zillow.com"), it must still go there and not to whatever was discussed
earlier.

### S4-05 — exact numeric repetition

**Say:** "My budget is 1500. Repeat that back to me."

**Should:** 1500.

**Over-correction to watch:** she should be able to say a number back at
all. If she starts refusing to repeat figures, or hedging on ordinary ones
("a coffee is about 5,000 won"), the guard has gone too far. Try both.

---

## Part 2 — the standing checks

Not the full fourteen. These are the ones nearest what changed, plus quit.

1. **Say "quit"** — one goodbye, one shutdown. Listen for whether the
   goodbye is actually *audible*; still never confirmed by anything.
2. Change the subject completely mid-task; the new query must carry none of
   the old one.
3. Let her make a factual claim, then say "but I've been there". She must
   check rather than restate.
4. Be rude once.
5. Answer a clarification with "same as I said".
6. Ask her to show you images of something (**S4-06**, still open) — does
   she refuse, or does she open them and say so?

---

## Part 3 — over-correction

The row that matters most this time is the last one; it is new.

| Watch for | Over-correction looks like |
|---|---|
| Locale suppression | A placeless query stops getting the market at all |
| Named-entity keeping | Queries pick up proper nouns that were not the point |
| Bare-definite resolution | "Open zillow.com" goes somewhere else |
| Number guard | She will not repeat a figure back, or hedges ordinary ones |
| Everything from `SESSION4_PLAN.md` Part 3 | unchanged |

---

## The gate

Release candidate when **all** hold:

- [ ] No P0 and no P1
- [ ] No regression against any earlier fix
- [ ] The five session-4 checks behave as described, in both directions
- [ ] Full suite green (2201)
- [ ] Live routing 41/41
- [ ] Consent walls 5/5

A P2 or P3 does not block; it ships as a known limitation.

**If a P0 or P1 appears, stop there.** Record it and end the session. That
is the rule session 3 broke and session 4 inherited.

---

## Known not-fixed, by decision

- **Media playback controls — pause/stop/resume.** B-48/B-49.
- **Vision in the browser planner.** B-08's fuller answer, and probably
  S4-06's too.
- **B-51**, STT homophone repair.
- **B-54**, desktop-PC packing specifics.
- **B-60**, "Spotify's gone, no trace left" — P3 tone.
- **S4-06**, image request refusal and phrasing — P3, judge it this session.
