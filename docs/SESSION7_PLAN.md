# Session 7 — release gate, fourth attempt

Session 6 failed the gate and the fixes for it changed code, so session 6
no longer counts as validation. Same rule as the last two.

**No code changes during this session.** If something fails, it is recorded
and the session ends there.

---

## The frozen state

| | |
|---|---|
| Branch | `phase4e-stabilization` |
| Tests | 2246, all green (1 expected failure) |
| Live routing | 41/41 |
| Consent walls | 5/5 |
| Browser stress | 7/7 |
| `main` | untouched at `6f427aff` |

---

## What session 6 was

Ten findings, six of them P1. The user's name for what the six share is
the one worth keeping, because it is more precise than "held state":

    CURRENT TURN + IMMEDIATELY ACTIVE REFERENT/ACTION
    must beat
    STALE FOCUS / OLD CORRECTION TEXT / OLD PARSE / GENERIC SUBJECT

| Boundary | What the system believed | What was true |
|---|---|---|
| Correction to an address | "only one S" is a new topic | it is the address, respelled |
| Failed action | "you didn't open it" is a new request | it is that action, denied |
| Anchor | "no, open Zillow.com" is what we're discussing | it is an errand |
| Anchor | "my school" means the last subject | it means the university named four times |
| Pointer test | "are there casinos" points backwards | it asks whether something exists |
| Parse | two versions of one turn may coexist | the corrected one is the turn |
| Candidate fit | it fits Korea, so it fits | it is not packing material |

The reason there were six is that each layer decided for itself what the
turn was about. Where two layers must both look, the one that already
knows now says so.

---

## Startup

```bash
cd C:/Users/pados/localProj/elainaAI && .venv/Scripts/python.exe main.py 2>&1 | tee runtime/session7.log
```

---

## Part 1 — the six P1s

**In one conversation, in this order.** Four of the six are about what
survives between turns; starting fresh for each would hide them.

### S6-01 — correcting an address

1. "Can you use my browser control and open isss.washington.edu?"
2. "I meant only one S."

**Should:** `[Rescue] respelled the address -> is.washington.edu` and that
address opened. The `Current subject` must **not** become "only one S",
and `browser_action` must not appear under `No longer the focus`.

It will open `is.washington.edu` — one S is literally what those words
ask for. That is intended. Then say:

3. "There's three S's in there. I just want two S's in there."

**Should:** `iss.washington.edu`. The operative clause is the last one.

**Over-correction:** an ordinary "no, I mean X" about a *topic* must still
change the subject. Try it later in the session.

### S6-03 — denying an action worked

4. "open zillow.com"
5. "you didn't open it."

**Should:** it goes back to `open_url zillow.com`. **Not** "I can't do
that one" followed by a list that includes browser control.

**Over-correction:** "you didn't understand me" must **not** re-run an
action.

### S6-04 — an errand is not a topic

6. "No, no, open naver.com"
7. "Are there casinos in Seattle?"

**Should:** the `[Tool] Searching web for:` line contains **only** the
casino question. No `naver`, no `no, open`.

### S6-06 — "my school"

8. Ask for something near the University of Washington by name.
9. Two turns of small talk ("I like strawberries", "you better be").
10. "Can you find me a rent near my school?"

**Should:** `Anchor: University of Washington`. **Not** `Anchor:
Conversation`, and the query must not contain the word "Conversation".

**Over-correction:** "near my school" must still work when the school was
established by a correction ("no, I mean I'm going to UW") rather than by
being named in a request.

### S6-05 — one version of the turn

11. Ask something with a word the transcriber is likely to mangle, then
    correct it: "No, no, are there casinos in Seattle?"

**Should:** if `[Router] restored ...` appears, the misheard word must
appear **nowhere** in the `[Router] <intent> (0.95): ...` reason line
either.

**Over-correction:** the reason line must still read as English. If words
like "the" have turned into "there", the stop-list is wrong.

### S6-02 — the thing, not just the constraint

12. "Where can I buy packing peanuts?"
13. "In Korea though."

**Should:** a recommendation that is packing material. If a food product
appears at all it must be marked MISMATCH and must never be the
recommendation.

**Over-correction:** an ordinary search ("find me an electric guitar under
500,000 won") must still return something. If everything comes back
MISMATCH, the compound-name rule is too strict.

---

## Part 2 — the two P2s

14. **S6-07.** The studio request must log `Domain: apartments` or
    `realestate`, not `hotel`, and the query must contain `studio`.
15. **S6-08.** When she has candidates, the answer must name one of them.
    A `[Grounding Guard] Naming what was found:` line is the good
    outcome; "want me to look up real ones?" *while candidates exist* is
    the bad one.
16. **S6-09.** Say "I like strawberries" (or anything that is not thanks)
    after a few turns. No reply may open with "You're welcome".

---

## Part 3 — the standing checks

1. **Say "quit"** — one goodbye, one shutdown. Listen for whether the
   goodbye is actually *audible*; still never confirmed by anything.
2. The five from `SESSION5_PLAN.md` Part 1 — Seattle's offset, the UW
   geography, the I-20 entity, "open the website", repeating `1500` back.
3. The five from `SESSION6_PLAN.md` Part 1 — all confirmed last session,
   and all have had code changed underneath them since.
4. Be rude once, with an expletive mid-phrase.
5. Ask her to show you images of something (**S4-06**) — she should run
   the browser rather than refuse.

---

## Part 4 — over-correction

Nine narrowings went in. Each is a way the next session can break.

| Watch for | Over-correction looks like |
|---|---|
| Repair-layer claim | an ordinary "I mean X" stops changing the subject |
| Spelling grammar | ordinary sentences get read as address corrections |
| Denial reading | "you didn't understand me" re-runs an action |
| Anchor: no errands | a real topic correction stops being remembered |
| Anchor: no placeholders | a legitimate short subject is rejected as one |
| Role resolution | "my school" reaches the wrong institution |
| Existential *there* | "is it cheaper there?" stops pointing back |
| Mishearing stop-list | a real mishearing of a short word is left in |
| Compound-name fit | good candidates come back MISMATCH |
| Sentence over paraphrase | a conditional clause types the request again |
| Naming what was found | a stale candidate is named on a new question |
| Courtesy removal | "thanks" stops getting "you're welcome" |
| Everything from `SESSION6_PLAN.md` Part 4 | unchanged |

---

## The gate

Release candidate when **all** hold:

- [ ] No P0 and no P1
- [ ] No regression against any earlier fix
- [ ] The six session-6 checks behave as described, in both directions
- [ ] Full suite green (2246)
- [ ] Live routing 41/41
- [ ] Consent walls 5/5

A P2 or P3 does not block; it ships as a known limitation.

**If a P0 or P1 appears, stop there.** Record it and end the session.

---

## Known not-fixed, by decision

- **Media playback controls — pause/stop/resume.** B-48/B-49.
- **Vision in the browser planner.** B-08, and the rest of S4-06.
- **B-51**, STT homophone repair.
- **B-54**, desktop-PC packing specifics.
- **B-60**, "Spotify's gone, no trace left" — P3 tone.
- **S5-06 retrieval.** The query is right; a general web search does not
  reliably surface the UW international-students contact page.
- **S6-10**, agreeing about a term she has never heard of — P3.
