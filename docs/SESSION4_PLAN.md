# Session 4 — final release validation

**The code is frozen.** Nothing is changed during this session, not even a
one-line fix that looks obvious. Session 3 was meant to be validation and
became development the moment I started fixing, which is why a fourth
session is needed at all.

If this one comes back clean, `phase4e-stabilization` is a release
candidate.

---

## The frozen state

| | |
|---|---|
| Branch | `phase4e-stabilization` |
| Commit | `55546ecd` |
| Tests | 2186, all green (1 expected failure) |
| Live routing | 41/41 |
| Consent walls | 5/5 |
| `main` | untouched at `6f427aff` |

Anything found in this session is **recorded, not fixed**. Fixing restarts
the clock and a fifth session becomes necessary.

---

## Why this session exists, honestly

Three of session 3's five findings were regressions from fixes I made in
session 2. That rate is the whole reason for a final pass: every fix is a
behaviour change, and behaviour changes have been wrong in the other
direction about half the time when they were freshly made.

**The four fixes from session 3 have never been used by a person.** They
are the least validated code in the build, and three of the four were
repairs to my own earlier mistakes. They get their own section below.

---

## Startup

```bash
cd C:/Users/pados/localProj/elainaAI && .venv/Scripts/python.exe main.py 2>&1 | tee runtime/session4.log
```

- Ollama up, `qwen3:8b` loaded
- No leftover backend
- Wait for `[Lifecycle] READY`
- **`debug.print_memory: true`** — still no evidence either way after three
  sessions
- Computer Control on when you want it

---

## Part 1 — the four newest fixes

Do these early, while you have patience for them. Each says what to say,
what should happen, and what the failure looks like in the log.

### B-56 — repetition after a rewrite

The founding complaint of the project, and it survived in the rewrite path
until three days ago.

**Say:** a factual question ("what are kiwis good for"), then `nice`, then
something unrelated and conversational ("are you gonna feed me?").

**Should:** three different replies, each answering its own turn.

**Failure looks like:** the same sentence twice. In the log, two
`[Response Rewrite] ... applied the advice fallback` lines followed by
identical text.

Worth repeating two or three times with different topics — this only
showed up because the conversation happened to go that way.

### B-57 — the world clock, not a web search

**Say:** "What time is it in Seattle right now?" then "and the date?"

**Should:** the correct Seattle time, and no search.

**Failure looks like:** `[Tool] Searching web for: What time is it...` in
the log at all. That line means the clock was bypassed. Also check the
*offset* she states — the session-3 answer said "one hour behind UTC" when
PDT is seven, and the wrong offset was stated as confidently as the wrong
time.

Try one more place too — Tokyo or London — and one she cannot know
("what time is it in Atlantis"), which **should** search.

### B-58 — no instruction in the answer

**Say:** send her to a page and ask for something that is not on it.
"Use browser control, go to the University of Washington academic
calendar, and tell me the tuition deadline."

**Should:** an honest report of what the page does show.

**Failure looks like:** a reply ending in a bare imperative — "Stop.",
"Done.", "Continue." — which is her reading her own instructions aloud.

### B-59 — your correction beats her memory of mishearing you

The hardest to force, because it needs a real transcription error.

**Say:** anything she mishears. When you see it in `You said:`, say the
same thing again, more clearly.

**Should:** the second turn uses **your** word. Look for
`[Router] restored '...' from the transcript.` — that line means it
worked.

**Failure looks like:** `[Router] Interpreted transcript as:` containing
the misheard word when `You said:` has the right one.

If nothing gets misheard naturally, skip it and say so. Do not contrive it.

---

## Part 2 — the fourteen checks

Unchanged from `SESSION3_PLAN.md`. Each is one specific sentence from a
previous log.

1. **Say "quit".** It should say goodbye once and close. (B-55)
   — **and listen for whether the goodbye is actually spoken.** It was
   added in session 2 and no log can show whether it was audible; this is
   the one fix in the build that has never been confirmed by anything.
2. Ask the time in Seattle, then the date. (B-22/B-24)
3. Start a rental search, give a budget, then change the subject entirely
   and ask something unrelated. The new query must carry none of it.
   (B-05/B-28/B-42)
4. Answer a budget question with a bare number, e.g. `1500`. (B-30)
5. Let her clarify something, then answer with "same as I said". (B-31)
6. Ask her to search on a site, mishear it, and correct with "no, <name>".
   (B-03)
7. Get her to list options, then say "open one of those". (B-36)
8. Have her open a page, then ask her to read a phone number off it.
   (B-39/B-40)
9. Ask her to show you images of something. (B-08)
10. Let her make a factual claim, then say **"but I've been there"**.
    She must check, not restate. (B-52)
11. Ask her something and then, mid-wait, ask "why is it taking so long?"
    (B-46)
12. Say "close my browser" without naming it. (B-50)
13. Be rude to her once. (B-18)
14. Say "forget about X" and change topic in the same sentence. (B-27)

---

## Part 3 — over-correction

The failure mode that produced three of session 3's five findings. If
something feels *newly* wrong, it is probably mine.

| Watch for | Over-correction looks like |
|---|---|
| Grounding guards | Refuses a number or name she genuinely found; hedges on ordinary general knowledge |
| Dispute escalation | An ordinary "hm, really?" sends her searching again |
| De-escalation bank | A mild correction gets an apology instead of an answer |
| Exit command | Something with "quit"/"bye"/"close" in it ends the session when you meant otherwise |
| Clarification narrowing | She stops asking a question that would have helped |
| Consent narrowing | A real "yeah, go on" is not taken as consent |
| Anchor expiry | She forgets context you expected her to keep |
| Browser loop guard | She gives up on a page too early |
| Repetition guard | A legitimate repeat — reading a number back, confirming — gets suppressed |

That last row is new and untested: B-56's fix rejects a rewrite that
repeats a recent answer, and sometimes repeating yourself is correct.

---

## What only you can judge

- Does she sound like a friend or a system?
- Do the honest lines ("I haven't actually checked that", "Nothing's
  running right now") read as careful, or as evasive?
- Is the de-escalation bank warm, or customer-service?
- Does she repeat herself?

---

## Recording

In `bugs.md`, under **Session 4 issues**. Mark each one:

- **`REGRESSION`** — new since session 3. These decide the release.
- **`CONFIRMED`** — a fix visibly working. Record these too; they are what
  the release is called on.
- **`P0`–`P3`** as before.

---

## The release-candidate gate

`phase4e-stabilization` becomes a release candidate when **all** of these
hold after the session:

- [ ] No P0 and no P1 in session 4
- [ ] No regression against any session 1–3 fix
- [ ] The four emphasis checks behave as described
- [ ] The fourteen checks behave as described
- [ ] Full suite green (2186)
- [ ] Live routing 41/41
- [ ] Consent walls 5/5

If all seven hold, the branch is tagged and the release is called on
evidence.

**If any P0 or P1 appears:** the session ends there. It is recorded, and
the fix — plus a fifth validation session — happens afterward. Do not fix
and carry on; that is precisely how session 3 stopped being a validation
session.

A P2 or P3 does not block the release. It is recorded and shipped
alongside the known limitations, because a small annoyance you know about
is worth more than a fix nobody has used.

---

## Known not-fixed, by decision

Not bugs, and not counted against the release:

- **Media playback controls — pause/stop/resume.** Blocks B-48/B-49.
- **Vision in the browser planner.** B-08's fuller answer.
- **B-51**, STT homophone repair.
- **B-54**, desktop-PC packing specifics.
- **B-60**, "Spotify's gone, no trace left" — free model phrasing on an
  action result, a P3 tone note.
