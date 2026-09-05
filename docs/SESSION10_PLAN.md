# Session 10 — nine checks

Same shape as session 9: a short, fixed list. **Do not broaden it unless
a P0 or P1 appears.**

**No code changes during this session.**

---

## The frozen state

| | |
|---|---|
| Branch | `phase4e-stabilization` |
| Tests | 2356, all green (1 expected failure) |
| Live routing | 41/41 |
| Consent walls | 5/5 |
| Browser stress | 7/7 |
| `main` | untouched at `6f427aff` |

---

## What changed

Session 9's cluster was not routing or state. It was that **normal speech
produces targets nobody can act on** — and every layer downstream took the
damaged version literally:

    "browser control"    heard as  "brass control"    -> she claimed to open it
    "open host.example"  heard as  "openhost.example" -> web-searched, then
                                                         described as opened
    "naver.com"          heard as  "laver.com"        -> the correction became
                                                         a research topic

Not fixed with a homophone table. Two of the three belong to **closed
vocabularies**: her abilities are a list of eight, and an address is a
string with a grammar. A near-miss inside a closed set can be repaired; a
near-miss of nothing must be asked about, never invented.

The comparison is on consonants, because vowels are what a transcriber
loses first:

| repaired | left alone |
|---|---|
| brass → browser (0.67) | mouse → browser (0.29) |
| desk → desktop (0.75) | volume → browser (0.00) |
| scream → screen (0.75) | remote → desktop (0.25) |

Separately: a failed action now leaves one record behind, so "try again"
and a bare "yeah" after "try again?" both have something to mean.

---

## Startup

```bash
cd C:/Users/pados/localProj/elainaAI && .venv/Scripts/python.exe main.py 2>&1 | tee runtime/session10.log
```

Turn browser control on first.

---

## The nine

### A. `open naver.com`, said naturally

Say it at normal speed. **Do not** put an artificial pause between "open"
and the domain — that is the whole point.

**Should:** naver.com opens and verifies. If the transcript comes out
`opennaver.com`, that is fine — check that it still ends up on naver.com,
via `[Navigation] recovering: opennaver.com -> naver.com`.

### B. `browser control` under a bad transcription

Say "use my browser control" a few times. If STT ever produces something
like "brass control", look for:

    [Speech Repair] 'brass control' -> 'browser control'

**Should never happen:** her saying she opened a thing by a name that is
not one of hers.

**Over-correction to watch:** ask for something she genuinely does not
have — "can you do mouse control?" — she must say she does not have it,
**not** silently turn it into browser control.

### C. `laver.com` → "It's not an L, it's an N."

**Should:** `[Rescue] corrected the address -> naver.com` and naver.com
opened, in that same turn. **Not** a web search, and **not** an extra
"do it" from you.

### D. `isss` → "I meant only one S" → "I meant two S's"

**Should:** `is.washington.edu`, then `iss.washington.edu`. If
`is.washington.edu` fails, she should recover to `iss` on her own without
the second correction.

### E. a failed action, then "try again"

Open something that will fail, then say "Can you try again?"

**Should:** the same operation on the same target. **Not** "I can't do
that one."

**Over-correction:** "say that again" must not re-open anything.

### F. "try again?" → "Yeah."

When she says a page did not load and asks, answer "Yeah."

**Should:** the retry runs. **Not** "I got it. Let me know what you need
next."

### G. `open openai.com`

**Should:** openai.com, and it stays there. If she "recovers" to ai.com,
the fused-verb split is firing on a site that works.

### H. the electric-guitar lookup

"Find me an electric guitar under 500,000 won."

**Should:** a web search, no "electric or acoustic?", and no listicle as
the recommendation. If the reasoning says **no clear fit**, she must say
she could not verify one — **not** name a model from memory.

### I. quit

One goodbye, one shutdown. Listen for whether the goodbye is actually
*audible*; still never confirmed by anything.

---

## What to watch throughout

**Over-repair.** Two of this session's changes rewrite what you said
before anything reads it. If she ever acts on a word you did not say,
that is worse than the mishearing it was meant to fix — note the exact
transcript and the `[Speech Repair]` line.

**Over-hedging**, still. Verification is strict since session 8. A page
that is plainly on your screen being reported as failed is release-
blocking.

---

## The gate

- [ ] A–I all behave as described
- [ ] No P0 and no P1
- [ ] Nothing acted on that you did not say
- [ ] Full suite green (2356)
- [ ] Live routing 41/41
- [ ] Consent walls 5/5

**If a P0 or P1 appears, stop there.**

Only if all nine pass does the next session go wide again — the five from
`SESSION5_PLAN.md`, the six from `SESSION6_PLAN.md`, the four non-browser
ones from `SESSION7_PLAN.md`, and session 8's A–E.

---

## Known not-fixed, by decision

- **Media playback controls — pause/stop/resume.** B-48/B-49.
- **Vision in the browser planner.** B-08, and the rest of S4-06.
- **B-51**, STT homophone repair in open vocabulary. Session 9 repaired
  the *closed* vocabularies — ability names and addresses. A general
  homophone table remains deliberately not built.
- **B-54**, desktop-PC packing specifics.
- **B-60**, "Spotify's gone, no trace left" — P3 tone.
- **S5-06 retrieval.** The UW international-students contact page.
- **S6-10**, agreeing about a term she has never heard of — P3.
- **S7-10**, geographic containment — needs a gazetteer.
- **S7-12**, latency. `route_model` at 9–12s is the dominant term and
  nothing in sessions 5–9 touched it. Worth measuring, after this passes.
