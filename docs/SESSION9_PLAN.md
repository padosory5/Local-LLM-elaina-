# Session 9 — seven checks

Deliberately the shortest plan yet. Seven items, in order, one
conversation. **Do not broaden this until they pass.**

**No code changes during this session.**

---

## The frozen state

| | |
|---|---|
| Branch | `phase4e-stabilization` |
| Tests | 2299, all green (1 expected failure) |
| Live routing | 41/41 |
| Consent walls | 5/5 |
| Browser stress | 7/7 |
| `main` | untouched at `6f427aff` |

---

## What changed since session 8

Session 7 separated *dispatched* from *arrived*. Session 8 found that
*arrived* was still being decided by the address bar alone — and a
browser keeps the address you asked for through DNS failures, parked
domains and error pages. Four non-existent hosts verified.

The signal was in session 8's own log:

    title: host.example          <- nothing loaded
    title: opennavier.com        <- nothing loaded
    title: openzillow.com        <- nothing loaded
    title: isss.washington.edu   <- nothing loaded

    title: NAVER                                    <- arrived
    title: International Student Services - ISS     <- arrived

**A page that rendered has a name of its own.** A browser with nothing to
show falls back to the address it was given. That, the page's own text,
a title that names a different site, and a fingerprint taken before the
navigation, are what decide arrival now.

---

## Startup

```bash
cd C:/Users/pados/localProj/elainaAI && .venv/Scripts/python.exe main.py 2>&1 | tee runtime/session9.log
```

Turn browser control on first.

---

## The seven

### A. valid naver

"Can you use my browser control and open naver.com?"

**Should:** `status: target_verified`, an ordinary success line. No hedge.

### B. a domain that does not exist

"Open host.example."

**Should:** `status: error_page`, and a line saying it did not load. She
must **never** say it is open. If a `[Navigation] recovering:` line
appears here it is a bug — there is nothing to recover to.

### C. automatic recovery — the one that matters

1. "Can you use my browser control and open isss.washington.edu?"
2. "I meant only one S."

**Should:** step 2 gives `[Rescue] respelled the address ->
is.washington.edu`, that address fails, and then **without you saying
anything else**:

    [Navigation] recovering: is.washington.edu -> iss.washington.edu
    status: recovered_target_verified

and a line saying she opened `iss.washington.edu` instead.

You should **not** need to say "I meant two S's". If you do, this check
has failed — record it and stop.

### D. the wrong-tab case

Open zillow.com after having opened something else. Watch the
`[Navigation]` block.

**Should:** if `title:` names a different site than the address bar, the
status must **not** be `target_verified`. Either `wrong_destination`
(with a line saying what the browser is showing) or
`page_loaded_unverified` (if the same pair was there before) is correct.

### E. the fused command

Say "open Zillow.com" fast enough that the transcript comes out
`openZillow.com`.

**Should:** the fused host fails, then
`[Navigation] recovering: openzillow.com -> zillow.com`.

**Over-correction:** "open openai.com" must go to openai.com and stay.

### F. the explicit request after an offer

Get her to make an offer ("want me to look it up?"), then say:

    "Find me an electric guitar under 500,000 won."

**Should:** `[Consent] The turn asks for something of its own.` and a
product lookup. **Not** the browser planner, and not the offer's stored
goal.

**Over-correction:** a plain "yeah" or "go ahead" after an offer must
still accept it.

### G. quit

One goodbye, one shutdown. Listen for whether the goodbye is actually
*audible*; still never confirmed by anything.

---

## The one thing to watch throughout

**Over-hedging.** Verification is now much stricter, which means the
failure mode has flipped: instead of claiming success falsely, she may
now say "I couldn't check" or "it didn't load" about pages that are
genuinely on your screen.

If that happens, say which page and what the `[Navigation]` block said.
The fix is to read the browser better, not to loosen the test — but it is
release-blocking either way, because an assistant that says a working
page failed is no more useful than one that says a broken page worked.

---

## The gate

- [ ] A–G all behave as described
- [ ] No P0 and no P1
- [ ] No over-hedging on pages that really opened
- [ ] Full suite green (2299)
- [ ] Live routing 41/41
- [ ] Consent walls 5/5

**If a P0 or P1 appears, stop there.**

Only if all seven pass does the next session go wide again — the five
from `SESSION5_PLAN.md`, the six from `SESSION6_PLAN.md`, and the four
non-browser ones from `SESSION7_PLAN.md`.

---

## Known not-fixed, by decision

- **Media playback controls — pause/stop/resume.** B-48/B-49.
- **Vision in the browser planner.** B-08, and the rest of S4-06.
- **B-51**, STT homophone repair. **B-54**, desktop-PC packing.
- **B-60**, "Spotify's gone, no trace left" — P3 tone.
- **S5-06 retrieval.** The UW international-students contact page.
- **S6-10**, agreeing about a term she has never heard of — P3.
- **S7-10**, geographic containment — needs a gazetteer.
- **S7-12**, latency. `route_model` at 9–12s is the dominant term and
  nothing in sessions 5–8 touched it. Worth measuring, after this passes.
