# Standing orders — two files you own

Two plain-text files that outlive every restart. Open them in Notepad, or
just tell Elaina.

```
runtime/data/directives.yaml   what to do differently, always
runtime/data/about_me.yaml     what she knows about you
```

Neither exists until there is something to put in it.

---

## Why it is not a list of past mistakes

The obvious shape for this — and the one the thesis describes — is a prose
file of "things you got wrong before, try not to repeat them", handed to
the model on every turn.

That is the weakest possible mechanism *for this model*. Nine dogfooding
sessions produced the same finding over and over: `qwen3:8b` does not
honour prose instructions reliably, which is why every guard in this
project is code and not prompt wording. A growing list of don'ts would
also grow the prompt, and routing already costs 9–12 seconds.

So a directive here is **executed, not read**:

| | mechanism | strength |
|---|---|---|
| `say` | deterministic substitution, before anything reads the turn | binding |
| `about_me` facts | always in the answering context, never retrieved | strong |
| `notes` | prose in the answering context | advisory, capped at 12 |

The file's prose is for you. The rules are for the code.

---

## Telling her

| You say | What happens |
|---|---|
| "Always make opennaver.com open naver.com" | a `say` rule; from then on that word *is* the other one |
| "When I say laver I mean naver" | the same |
| "Remember that my school is the University of Washington" | a fact in `about_me.yaml` |
| "Always check Naver Maps before Google" | a note |
| "Forget the rule about opennaver.com" | drops every rule and fact mentioning it |

She confirms what she wrote, because a rule you cannot see is a rule you
cannot correct.

---

## What `say` is for, and what it is not

`say` is the general speech repair this project deliberately refused to
build. A homophone table written by *us* would break more than it fixed —
that is B-51, still open by decision. A homophone table written by *you*
is different: you know which word you keep having mistranscribed, and you
are the authority on what you meant.

It runs before the router, the focus layer, the task state and the browser
— so every layer sees the repaired turn, and nothing downstream has to
know the rule exists.

It has one deliberate limit: **a rule may not rewrite the sentence that
manages rules.** "Forget the rule about opennaver.com" is read from your
raw words, or the rule would edit its own removal notice. (That is not
hypothetical; it happened while this was being built.)

---

## What already existed, and what this adds

Elaina already had two persistent stores, and this replaces neither:

- **`runtime/data/user_profile.json`** — typed preferences with evidence
  and standing. "Use Spotify for music" belongs there, not here, and the
  preference reader still claims those turns first. It is strictly better
  than free prose: it knows how sure it is, and a correction halves the
  competing value rather than being averaged in.
- **the FAISS memory** — everything you have ever said, searched
  semantically.

`about_me.yaml` is the handful of facts that must *never* be missed:
always present, never retrieved, no ranking to get wrong. If retrieval
misses your moving date once, that is a bad turn; if it misses which
university is yours, every rental search for a month is wrong.

---

## The files

```yaml
# directives.yaml
say:
- heard: opennaver.com
  means: naver.com
notes:
- check Naver Maps before Google
```

```yaml
# about_me.yaml
facts:
- I'm moving to Seattle on September 18
- my school is the University of Washington
updated: '2026-09-05'
```

A file you have broken by hand costs you a rule, not a startup — she says
so on the console and carries on with the rest.

---

## Limits worth knowing

- `say` is a literal, case-insensitive substitution. It has no idea what a
  word means, so a rule that is too short will fire where you did not
  intend ("no" → something) — keep them specific.
- `notes` are advisory. If a note is not being honoured, that is expected
  rather than a bug; say it as a `say` rule or ask for it to become code.
- Nothing here is inferred. She never writes a rule because she noticed a
  pattern — only because you said so.
