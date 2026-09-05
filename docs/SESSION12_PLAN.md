# Session 12 — the browser acceptance test, eight checks

Nothing else. No broader dogfooding until these pass.

**No code changes during this session.**

---

## The frozen state

| | |
|---|---|
| Branch | `phase4e-stabilization` |
| Tests | 2386, all green (1 expected failure) |
| Live routing | 41/41 |
| Consent walls | 5/5 |
| Browser stress | 7/7 |
| `main` | untouched at `6f427aff` |

---

## What session 11 found, and what changed

Session 11 was the closest browser build so far. The lifecycle ran end to
end: fused-domain recovery, wrong-destination recovery, the UW typo chain,
verified Naver, honest hedging, and standing orders creating, executing
and deleting rules.

One P1 remained, and it was narrow:

    requested: Zillow.com
    actual: https://zillow.com
    title: International Student Services - ISS
    status: target_verified

The address bar commits the moment a navigation starts; the window title
follows when the new document says so. A fresh URL and the previous
page's title are **two observations of two pages**, and combining them is
how a page nobody had loaded was reported as open.

Fixed in two places, neither of which weakens a check:

* the verifier refuses to verify when the title still belongs to the page
  the browser was on before -- `classification: stale_observation`, and
  because it *was* observed it stays eligible for recovery;
* the screen driver already waits for two matching scans, and the
  comparison it makes now includes where it came from.

Also fixed, from S11-02: one misheard turn no longer erases the
correction target. An address survives a single drifting turn -- enough
for "I met only one S" to be repeated correctly -- and a turn that asks
for something else still retires it, as does a second drift.

And the wording from S11-04: a page that could not be confirmed no longer
opens with "I opened". She says what she did do -- she sent the browser
there.

## Startup

```bash
cd C:/Users/pados/localProj/elainaAI && .venv/Scripts/python.exe main.py 2>&1 | tee runtime/session11.log
```

Browser control on first.

---

## The eight

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

### 4. ISS, then Zillow — the stale-title regression

Do check 3 first, so ISS is the page she was last on. Then:

"Open zillow.com."

**Should:** `target_verified` **and** Zillow actually on your screen, with
`title:` naming Zillow.

**The failure to watch:** `title: International Student Services - ISS`
with `actual: https://zillow.com`. That must now come back as
`stale_observation`, never `target_verified`. This is the one check that
compares what she says against what you can see.

### 5. a correction after one uncertain turn

With an address open, say something the transcriber is likely to mangle,
then correct the address:

1. "Can you use my browser control and open isss.washington.edu?"
2. anything short and easily misheard
3. "I meant only one S."

**Should:** step 3 still corrects the address. It must not have become a
conversational subject because step 2 drifted.

### 6. "that didn't open"

After any of the above, say it.

**Should:** she goes back to that address and checks again. Never a
repeat of the success claim, and never "I can't do that one."

### 7. one standing-order correction

Pick a word STT keeps getting wrong for you. Tell her:

    "Always make <what it hears> <what you mean>."

**Should:** she confirms, and on the next turn `[Standing Orders]` shows
the substitution before anything else reads the turn.

Then: "Forget the rule about &lt;what it hears&gt;." It must actually go.

### 8. quit

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

**Over-recovery.** Recovery is reachable from more states than it was. If
she substitutes an address when the one you asked for was fine, that is
release-blocking — record the `[Navigation]` block and the `recovering:`
line.

**Over-staleness.** The new stale test refuses to verify when the title
still belongs to the page before. If a site legitimately shares its title
with the one you just left, she will hedge on a page that did load. Note
it if you see it; the answer is a longer settle, not a looser test.

---

## The gate

- [ ] All eight behave as described
- [ ] Check 4 matches what is on the screen
- [ ] No P0 and no P1
- [ ] Full suite green (2386)
- [ ] Live routing 41/41
- [ ] Consent walls 5/5

**If a P0 or P1 appears, stop there.**

If these pass with the automated suite green, browser stabilization
stops. Anything left in the browser after that ships as a known
limitation:

- **S11-03.** A deliberately invalid host comes back `ambiguous` rather
  than a recognised browser error. She hedges honestly, which is the
  behaviour that matters; the classification is less precise than it
  could be.
- **S7-12.** Latency. `route_model` at 9–12s is the dominant term and
  nothing in sessions 5–11 touched it.
