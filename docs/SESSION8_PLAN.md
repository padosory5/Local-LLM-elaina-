# Session 8 — the browser gate

Short, and almost entirely about one question:

> **Did the page I asked for actually open?**

Session 7 could not answer it. That is now a lifecycle rather than a
status, and this session exists to check the lifecycle before anything
else is re-validated.

**No code changes during this session.** If something fails, it is
recorded and the session ends there.

---

## The frozen state

| | |
|---|---|
| Branch | `phase4e-stabilization` |
| Tests | 2283, all green (1 expected failure) |
| Live routing | 41/41 |
| Consent walls | 5/5 |
| Browser stress | 7/7 |
| `main` | untouched at `6f427aff` |

---

## What changed

`open_url` used to return `url_opened` and stop. That status means
Windows accepted the navigation command; it never meant the page loaded.

Now:

    requested -> dispatched -> observed -> verified | failed -> recovered

Only **verified** and **recovered** may be spoken as "it is open".

| She sees | She says |
|---|---|
| the requested site (redirects and paths included) | the ordinary success line |
| a browser error page, or a search *for* the address | "X didn't load" |
| a different page, or a blank tab | "X didn't load" |
| the browser cannot be read at all | "I sent the browser there, but I couldn't check whether it loaded" |
| a failure with one recoverable candidate | opens it, verifies it, says what changed |
| a failure with two candidates | "Did you mean A or B?" |

Recovery never invents a domain. It has exactly two sources, both things
the conversation supplied: a command verb the transcriber ran into the
host, and the spellings between the address first asked for and the one
just tried.

---

## Startup

```bash
cd C:/Users/pados/localProj/elainaAI && .venv/Scripts/python.exe main.py 2>&1 | tee runtime/session8.log
```

Turn browser control on before Part 1.

---

## Part 1 — navigation, in order, one conversation

### A. a site that exists

1. "Can you use my browser control and open naver.com?"

**Should:** a `[Navigation]` block with `status: target_verified`, and an
ordinary success line. **No** hedge.

### B. a site that does not

2. "Open nosuchhost.example."

**Should:** `status: error_page` or `navigation_failed`, and a line
saying it did not load. She must **never** say it is open.

### C. the fused verb

3. Say "open Zillow.com" quickly enough that the transcript comes out as
   `openZillow.com` (check the `You said:` line — if it comes out with a
   space, say it faster or just type it).

**Should:** the first attempt fails, then
`[Navigation] recovering: openzillow.com -> zillow.com`, and she says she
opened zillow.com instead.

**Over-correction to watch:** "open openai.com" must go to openai.com and
**stay** there. If she "recovers" to ai.com, the split is firing before
the failure.

### D. the correction chain — the whole of S7-01/S7-03

4. "Can you use my browser control and open isss.washington.edu?"
5. "I meant only one S."
6. "I meant two S's."

**Should:** step 5 gives `[Rescue] respelled the address ->
is.washington.edu`. Step 6 gives `iss.washington.edu` — **not** a new
subject called "two S's", and not "Sorry, I answered the wrong thing".

If `is.washington.edu` does not resolve, step 5 should recover to
`iss.washington.edu` on its own and say so.

### E. your eyes beat her status

7. Open something, then say — one per turn, over several attempts —
   "didn't open it", "that's not it", "the website is not opened on my
   browser".

**Should:** each goes back to the browser and checks. She must **never**
answer by repeating that it is open.

**Over-correction:** "you didn't understand me" must not re-open
anything.

---

## Part 2 — the other four from session 7

8. **S7-07.** "Can you find me some good Korean restaurants near the
   University of Washington?" The query must **not** end "in South
   Korea". Then ask for something with no place in it at all — that one
   still should.
9. **S7-08.** "Find me an electric guitar under 500,000 won." She must
   **not** ask "electric or acoustic?". Separately, ask for a plain
   guitar — she still should ask — and answer it "Electric, I said
   electric." It must be accepted the first time.
10. **S7-09 / S7-11.** The guitar recommendation must be a guitar, not an
    article about guitars. The packing-peanuts answer must not be a Bob
    Vila listicle.

---

## Part 3 — the standing checks

1. **Say "quit"** — one goodbye, one shutdown. Listen for whether the
   goodbye is actually *audible*; still never confirmed by anything.
2. "Near my school", after establishing a university by name.
3. "In Korea though" after a placeless shopping search.
4. Be rude once, with an expletive mid-phrase.
5. Seattle's time and its offset.

---

## Part 4 — over-correction

| Watch for | Over-correction looks like |
|---|---|
| Navigation verification | a page that *did* open is reported as failed |
| Unverified hedging | every open becomes "I couldn't check" |
| Fused-verb recovery | openai.com is redirected to ai.com |
| Spelling correction | ordinary sentences read as address corrections |
| Denial reading | "you didn't understand me" re-opens a page |
| Named-place test | a placeless query stops getting the market |
| Kind already stated | she stops asking electric/acoustic when she should |
| Round-up test | a real product with a number in its name is rejected |
| Everything from `SESSION7_PLAN.md` Part 4 | unchanged |

The second row is the one to watch hardest. If the browser cannot be read
in your setup, **every** navigation will hedge, and that is a worse
experience than the old false confidence even though it is more honest.
If that happens, say so — the fix is to make the observer see the browser
she is opening tabs in, not to go back to claiming success.

---

## The gate

Release candidate when **all** hold:

- [ ] No P0 and no P1
- [ ] No regression against any earlier fix
- [ ] Part 1 A–E behave as described
- [ ] Full suite green (2283)
- [ ] Live routing 41/41
- [ ] Consent walls 5/5

A P2 or P3 does not block; it ships as a known limitation.

**If a P0 or P1 appears, stop there.**

---

## Known not-fixed, by decision

- **Media playback controls — pause/stop/resume.** B-48/B-49.
- **Vision in the browser planner.** B-08, and the rest of S4-06.
- **B-51**, STT homophone repair.
- **B-54**, desktop-PC packing specifics.
- **B-60**, "Spotify's gone, no trace left" — P3 tone.
- **S5-06 retrieval.** The UW international-students contact page.
- **S6-10**, agreeing about a term she has never heard of — P3.
- **S7-10**, geographic containment. Saying which casinos are inside
  Seattle city limits needs a gazetteer this system does not have.
- **S7-12**, latency. Recorded and unmeasured. `route_model` at 9–12s is
  the dominant term and it is not something sessions 5–7 changed.
