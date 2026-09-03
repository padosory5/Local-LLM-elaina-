# Session 3 — validation

Sessions 1 and 2 were for finding things. This one is for proving the
findings are gone. **Nothing gets fixed during it**, and that is the point:
a session that changes code cannot also measure code.

Fifty-one fixes went in across the two sessions. Almost all of them change
what she *says* or *when she acts*, and a test suite cannot tell you
whether "Sorry — that one's on me" sounds like her. That is what your ears
are for, and it is the only thing this session is for.

---

## Before you start

Same launch as before, so the log is capturable:

```bash
cd C:/Users/pados/localProj/elainaAI && .venv/Scripts/python.exe main.py 2>&1 | tee runtime/session3.log
```

- Ollama up, `qwen3:8b` loaded
- No leftover backend
- Wait for `[Lifecycle] READY`
- **Set `debug.print_memory: true`** — still the one exercise area with no
  evidence either way, across both sessions
- Computer Control on when you want it, off otherwise

---

## What this session is testing

Not "does anything break". **Do the 51 fixes hold under real use, and did
any of them make her worse?**

Every fix is a behaviour change, and a behaviour change can be wrong in the
other direction. Two of my session-1 fixes were, and session 2 caught both
(B-44, B-47). Assume there are more.

### The regression risk, in priority order

These are the changes most likely to have over-corrected. If something
feels *newly* wrong here, it is probably mine.

| Watch for | If it over-corrected you will see |
|---|---|
| Grounding guards | She refuses to give a number or a name she genuinely found. Hedges on ordinary general knowledge. |
| Dispute escalation | An ordinary "that's not much" or "hm, really?" sends her searching again. |
| De-escalation bank | A mild "that's not what I meant" gets an apology instead of an answer. |
| Exit command | Anything with "quit"/"bye"/"close" in it ends the session when you meant something else. |
| Clarification narrowing | She *stops* asking a question that would genuinely have helped. |
| Consent narrowing | A real "yeah, go on" is not taken as consent. |
| Anchor expiry | She forgets context you expected her to keep ("near my school" three turns later). |
| Browser loop guard | She gives up on a page too early instead of trying a second approach. |
| Project-question guard | A real question about the codebase gets answered as conversation. |

### The fixes to confirm actually landed

Do these deliberately. Each is one specific sentence from a previous log.

1. **Say "quit".** It should say goodbye once and close. (B-55)
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

### What only you can judge

- Does she sound like a friend or a system?
- Do the new honest lines ("I haven't actually checked that", "Nothing's
  running right now") read as careful or as evasive?
- Is the de-escalation bank warm or is it customer-service?
- Does she repeat herself?

---

## Recording

Same block as before, in `bugs.md` under a **Session 3 issues** heading.
Two additions worth marking:

- **`REGRESSION`** if the behaviour is new since session 2. Those come
  first, ahead of everything else.
- **`CONFIRMED`** against a fix number when one visibly works. Confirmations
  are as useful as failures here — they are what lets the release be
  called.

---

## Exit criteria

Release-ready when:

- No P0 or P1 in session 3
- No regressions against session 1 or 2 fixes
- The fourteen deliberate checks above behave as described
- Full suite green, live routing 41/41, consent walls 5/5

If a P1 does appear, it gets fixed with a regression test like every other
one — but the session that found it is over, and the next session
re-validates. Fix-and-continue in the same session is how a validation
session quietly turns back into a development session.

## Known not-fixed, by decision

Not bugs and not counted against the release:

- **Media playback controls — pause/stop/resume.** Blocks B-48/B-49.
  "Stop the music" has no correct destination to route to.
- **Vision in the browser planner.** Would let her see an image results
  page instead of reporting the steps she took.
- **B-51**, STT homophone repair — a repair table would be the
  hardcoded-phrase collection this project forbids.
- **B-54**, desktop-PC packing specifics — domain knowledge, not a fix.
