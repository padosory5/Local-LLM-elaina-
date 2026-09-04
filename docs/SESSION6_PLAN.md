# Session 6 — release gate, third attempt

Session 5 failed the gate and the fixes for it changed code, so session 5
no longer counts as validation of anything. Same rule as last time.

**No code changes during this session.** If something fails, it is recorded
and the session ends there.

---

## The frozen state

| | |
|---|---|
| Branch | `phase4e-stabilization` |
| Tests | 2222, all green (1 expected failure) |
| Live routing | 41/41 |
| Consent walls | 5/5 |
| Browser stress | 7/7 |
| `main` | untouched at `6f427aff` |

---

## What session 5 actually was

Eight findings. Five P1, and the five were one bug: **held state beating
the current turn**, at three boundaries the earlier fixes had not reached.

| Boundary | What lost | What won |
|---|---|---|
| The query | the user's own market | a city from a clock question eight turns back |
| The correction | "In Korea though" | a task that had already recorded Korea and did nothing with it |
| The correction | "Only one S" | a router label of *conversation* |
| The continuation | "So open it" | the desktop planner, for fourteen rounds |
| The dispute | "but I've been there" | an offer to check, instead of checking |

The rule now written at each of them is the same one, and it has two
halves that must both hold:

* **what the turn says outranks what the system is holding**, and
* **what the turn does not say may still be filled in.**

Every check below tests both halves, because the fix for either one alone
is a new bug. That is what "in both directions" means in this document.

---

## Startup

```bash
cd C:/Users/pados/localProj/elainaAI && .venv/Scripts/python.exe main.py 2>&1 | tee runtime/session6.log
```

`debug.print_memory: true` — still no evidence either way after five
sessions.

---

## Part 1 — the five P1s from session 5

Do these **in this order, in one conversation**, because the bug was
about what survives between turns. Starting fresh for each would hide it.

### S5-01 — a place mentioned in passing

1. "What time is it in Seattle right now?"
2. "Can you find me a studio near the University of Washington with a
   budget?"

**Should:** a `[Query]` with **no `time`** in it, and **no `Seattle`**
carried from step 1 as a topic. `University of Washington` must be there,
and so must `studio`.

**Other direction:** say "I'm moving to Seattle on September 18" instead
of step 1, then ask for the studio. Seattle **should** be in that query —
it is a fact about you, not about a clock.

### S5-02 — the corrected place

3. "Where can I buy packing peanuts?"

**Should:** the query says **South Korea**, not Seattle. This is the
"fallback is allowed" half — you named no place, so your market fills in.

4. "In Korea though."

**Should:** a `[Task Resume]` line, and the search runs again **in
Korea**. Not "Cool, you're in Korea! What's new there?"

**Other direction:** later, say something ordinary while a lookup is open
("I like kiwis"). It must **not** re-run anything.

### S5-03 — a correction to the address

5. "Can you use my browser control and then open isss.washington.edu?"
6. "Only one S."

**Should:** she goes back to the address rather than answering it as
conversation. It will open `is.washington.edu`, which is literally what
those words ask for — that is the intended behaviour, and it is not the
same failure as apologising and asking you to say the whole thing again.

**Watch for:** if she starts treating ordinary sentences containing a
number and a letter as address corrections, the grammar is too loose.

### S5-04 — the continuation after a failure

7. Open a URL, then say "though it's not" when she reports success.
8. "So open it."

**Should:** `[Rescue] continue the last action -> open_url`, and the
address from step 7. **Not** the desktop planner, and not fourteen rounds
of anything.

**Other direction:** "open zillow.com" and "open Spotify" must still go
to the address and to the app respectively.

### S5-05 — the dispute

9. Get her to make a checkable claim about somewhere ("are there casinos
   on <somewhere obscure>").
10. "but I've been there."

**Should:** she *checks*. A `[Tool] Searching web for:` line on that turn.
Not "say the word and I'll go through casinos", and not the same claim
again.

**Other direction:** a plain complaint that asks for nothing ("Spotify
won't play anything today") must still be an offer, not a silent search.

---

## Part 2 — the three P2s

11. **S5-06.** "Can you find me the contact information for the University
    of Washington about my I-20?" The query must name the university. If
    the search finds nothing, she must say **she looked and could not
    find it** — not that she has not checked. Finding it is not expected;
    the search does not reliably surface that page and that is recorded
    as a limitation.
12. **S5-07.** Be rude, plainly, with an expletive in the middle of it.
    She should take it, not defend herself. Then be rude *and* ask for
    something in the same breath — that one has to be answered.
13. **S5-08.** The studio query from step 2 must contain the word
    `studio`.

---

## Part 3 — the standing checks

Not the full fourteen. These are the ones nearest what changed, plus quit.

1. **Say "quit"** — one goodbye, one shutdown. Listen for whether the
   goodbye is actually *audible*; still never confirmed by anything.
2. Change the subject completely mid-task; the new query must carry none
   of the old one.
3. Answer a clarification with "same as I said".
4. Ask her to show you images of something (**S4-06**) — she should run
   the browser rather than refuse. She still cannot say what is in the
   picture, and that is expected.
5. The five from `SESSION5_PLAN.md` Part 1 — Seattle's offset, the UW
   geography, the I-20 entity, "open the website", and repeating `1500`
   back. All five passed last session and all five have had code changed
   underneath them since.

---

## Part 4 — over-correction

The whole of this session's risk is here. Every fix above narrowed
something that used to fire freely.

| Watch for | Over-correction looks like |
|---|---|
| Location retirement | "I'm moving to Seattle" stops steering later queries |
| Relational references | "rent near my school" stops meaning the school |
| Place-only resume | every remark during an open task re-runs the search |
| Address respelling | ordinary sentences get read as spelling corrections |
| Deictic continuation | "open zillow.com" reopens the previous thing |
| Dispute execution | idle complaints trigger silent searches |
| Insult reading | "you're so good at this" gets an apology |
| Housing type in queries | unrelated searches pick up "studio" |
| Everything from `SESSION5_PLAN.md` Part 3 | unchanged |

---

## The gate

Release candidate when **all** hold:

- [ ] No P0 and no P1
- [ ] No regression against any earlier fix
- [ ] The five session-5 checks behave as described, in both directions
- [ ] Full suite green (2222)
- [ ] Live routing 41/41
- [ ] Consent walls 5/5

A P2 or P3 does not block; it ships as a known limitation.

**If a P0 or P1 appears, stop there.** Record it and end the session.

---

## Known not-fixed, by decision

- **Media playback controls — pause/stop/resume.** B-48/B-49.
- **Vision in the browser planner.** B-08, and the remaining half of
  S4-06.
- **B-51**, STT homophone repair.
- **B-54**, desktop-PC packing specifics.
- **B-60**, "Spotify's gone, no trace left" — P3 tone.
- **S5-06 retrieval.** The query is right; a general web search does not
  reliably surface the UW international-students contact page.
- **S5-08 classification.** A studio rental is classified `hotel`. The
  query is right either way now, but the domain is still wrong.
