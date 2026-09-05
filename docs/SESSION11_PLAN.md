# Session 11 — the browser gate, seven checks

Nothing else. No broader dogfooding until these pass.

**No code changes during this session.**

---

## The frozen state

| | |
|---|---|
| Branch | `phase4e-stabilization` |
| Tests | 2382, all green (1 expected failure) |
| Live routing | 41/41 |
| Consent walls | 5/5 |
| Browser stress | 7/7 |
| `main` | untouched at `6f427aff` |

---

## What the investigation found

The brief was to make dispatch and observation refer to the same tab.
**They already did.** The five questions, answered from the code and from
session 10's own log:

1. **What `open_url` uses.** `ComputerControl` is built with
   `SafeBrowserControl(opener=lambda url: self.browser_service.open_url(url))`
   whenever `browser_control.enabled` is true — which it is. So an
   `open_url` goes through the same service that observes.
2. **What the screen driver observes.** The window it typed the address
   into. `_go_to` picks an HWND, focuses it, types, presses Enter, waits
   for two matching scans, and builds the receipt against *that* handle.
3. **`webbrowser.open_new_tab`.** Not used in production. It remains the
   default opener for standalone `SafeBrowserControl` callers only.
4. **Focus and timing.** Handled: `_await_landing_ready` waits for a
   settled page, and `bind_navigation` keeps the dispatcher's HWND even if
   focus moves.
5. **A stable identity.** Already captured, and already in the log:
   `observation: hwnd:525894:50cfa6ff`.

Session 10's two navigation blocks both show the observer working — the
right window, the right address, the right title:

    actual: https://opennaver.com
    title: opennaver.com
    observation: hwnd:525894:50cfa6ff
    status: page_loaded_unverified
    classification: ambiguous
    detail: the page has no name of its own, so it could not be checked

So the blocker was not observation. It was that **"I could not look" and
"I looked and could not judge" went to the same dead end.** Both produced
`page_loaded_unverified`, and recovery only ran on statuses below that —
so the `naver.com` candidate sitting in the conversation was never tried.

Recovery now runs on anything that was *observed* and did not arrive.
Nothing became a success that was not one; the verification rules are
untouched.

Second change, same cause: the fused-verb split used to need parse
provenance, and "opennaver.com" supplies none — one lowercase token, and
the router's own paraphrase says "open opennaver.com". After the address
has been tried and observed, provenance is no longer what decides. The
session-7 rule still holds and is what keeps `openai.com` safe: **a site
that works never reaches recovery at all.**

---

## Startup

```bash
cd C:/Users/pados/localProj/elainaAI && .venv/Scripts/python.exe main.py 2>&1 | tee runtime/session11.log
```

Browser control on first.

---

## The seven

### 1. `open naver.com`

**Should:** `status: target_verified`, an ordinary success line, no hedge.

If the transcript comes out `opennaver.com`, that is fine — it should
still end on naver.com, via
`[Navigation] recovering: opennaver.com -> naver.com`.

### 2. a deliberately invalid hostname

"Open nosuchhost.example."

**Should:** `error_page` or `navigation_failed`, and a line saying it did
not load. Never "it is open". No `recovering:` line — there is nothing to
recover to.

### 3. bad UW spelling → automatic recovery

1. "Can you use my browser control and open isss.washington.edu?"
2. "I meant only one S."

**Should:** step 2 corrects to `is.washington.edu`, that address is
observed to be nothing, and then **without you saying anything else**:

    [Navigation] recovering: is.washington.edu -> iss.washington.edu
    status: recovered_target_verified

### 4. open Zillow, and look at the screen

"Open zillow.com."

**Should:** `target_verified` **and** Zillow actually on your screen.
This is the one check that compares what she says to what you can see.

### 5. "that didn't open"

After any of the above, say it.

**Should:** she goes back to that address and checks again. Never a
repeat of the success claim, and never "I can't do that one."

### 6. one standing-order correction

Pick a word STT keeps getting wrong for you. Tell her:

    "Always make <what it hears> <what you mean>."

**Should:** she confirms, and on the next turn `[Standing Orders]` shows
the substitution before anything else reads the turn.

Then: "Forget the rule about &lt;what it hears&gt;." It must actually go.

### 7. quit

One goodbye, one shutdown. Listen for whether the goodbye is actually
*audible*; still never confirmed by anything.

---

## What to watch

**The honest hedge must survive.** If the browser genuinely cannot be
read, the right answer is still

> I sent the browser there, but I couldn't check whether it loaded.

If you see that on a page you can plainly see loaded, note the
`[Navigation]` block verbatim — that is the observer failing, and it is a
different bug from the one just fixed.

**Over-recovery.** Recovery is now reachable from more states. If she
substitutes an address when the one you asked for was fine, that is
release-blocking — record the `[Navigation]` block and the
`recovering:` line.

---

## The gate

- [ ] All seven behave as described
- [ ] Check 4 matches what is on the screen
- [ ] No P0 and no P1
- [ ] Full suite green (2382)
- [ ] Live routing 41/41
- [ ] Consent walls 5/5

**If a P0 or P1 appears, stop there.**

Only after this passes does the wider dogfood resume.
