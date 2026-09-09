# A7 — Memory & personal context

**What the phase was for:** she is useful over weeks, not only within one
conversation, without becoming unpredictable.

**Instrument:** [`scripts/recall_report.py`](../scripts/recall_report.py) against
[`tests/recall_matrix.json`](../tests/recall_matrix.json), written before the
fix. Enforcement is [`brain/memory_gate.py`](../brain/memory_gate.py) — the same
module the report reads, per Rule 1.

```bash
.venv/Scripts/python.exe scripts/recall_report.py
```

| | Before | After |
|---|---|---|
| Turns needing memory the gate could even see | **4 / 18** | **18 / 18** |
| Recall matrix | — | **34 / 34** |
| Ways to delete a memory | **none** | soft delete, named aloud |
| Network calls in the memory package | 0 | **0, asserted** |
| Tests | 3145 | **3163** |

---

## A working memory system, gated on one of twenty-four intents

There is a real memory subsystem here and it is not a toy: SQLite, a FAISS
index, `bge-m3` embeddings, extraction, consolidation, and a ranker that
weights similarity, importance, recency and access count. It works.

Both ends of it sat behind the same gate:

```python
storing     route.intent == "conversation" and route.memory_candidate
retrieval   route.intent == "conversation" and route.memory_relevant
```

`conversation` is **one of twenty-four intents**. `web_search`,
`calendar_action`, `recommendation`, `project_question` and twenty more skip
memory entirely, in both directions.

And the turns most likely to *contain* a durable fact about someone are exactly
the turns where they are asking for something:

```
"I'm allergic to shellfish, find me somewhere for dinner"
    -> web_search -> the allergy is never stored

"what's a good restaurant near my school?"
    -> web_search -> nothing is recalled, and the gap is filled from
       the model. That is where an invented university comes from.
```

That second one is not hypothetical — it is the failure this phase was written
for, and it was on the test sheet as a predicted failure before the phase
started.

Measured across the recall matrix: **the intent gate could see 4 of the 18 turns
that needed memory** — before the two model-set booleans narrowed it further. A
model that mislabels `memory_candidate` loses the fact silently, and a lost fact
is indistinguishable from one that was never said.

So memory became a property of **the sentence**. The engine now asks
`memory_gate` alongside the router rather than instead of it: either saying yes
is enough, because a wrong yes costs a little latency and a wrong no loses
something you told her once.

---

## The hard part is not finding first-person statements

It is telling *"I'm allergic to shellfish"* from *"I'm tired"*.

The social dogfood arc is made of the second kind — "i had a rough night", "kind
of tired honestly", "yeah it was a long one" — and storing those as facts about
someone is how a memory turns into a caricature. `_TRANSIENT` is checked first
and wins, with one exception: **being asked outranks being guessed**, so
"remember that I'm tired every Monday" is kept.

The mirror rule on the recall side: *"my screen"* is a screen-vision request and
*"my browser"* is a capability question. Neither needs to know where you went to
school. Machine possessives are stripped before the personal ones are looked for.

### Two bugs the live run found, after the offline matrix read 29/29

The contamination re-run is what caught them, and only because its own setup
lines happened to use phrasings the matrix did not.

**"I'm going to UW in Seattle" was not recognised as durable.** The gate knew
"I go to school" and nothing else, so the fact was never stored — and the later
turn *"where is my school again?"* correctly fired retrieval, found nothing, and
she answered *"I can look that up for you."* Honest, and not recall. A
near-future time expression is what separates it from a trip: "I'm going to
Seattle next week" is not where you study.

**"where is my school again?" was being stored as a fact.** It matched the
durable pattern on "my school" — so a question about something personal would
have been saved as though it were an answer, and a memory fills up with the
questions someone asked rather than what they said. Asking is not telling.

The fix for the second nearly broke the first: judging the whole utterance by
its final question mark throws away *"I go to school at UW, where is the
international students office?"*, which states a fact **and** asks — and that
shape is the entire premise of this phase, since a durable fact usually arrives
attached to a request. The gate reads clause by clause now.

### One more worth naming

The first draft of `_DURABLE` wrote only the contraction, `\bi'?m\s+allergic`.
So *"I am allergic to shellfish"* did not match — and that sentence is the
capability registry's own example of what memory is for. Which of "I'm" and "I
am" arrives is not the person's choice; the transcriber picks. Both spellings
are now one pattern, with a test.

---

## Forgetting did not exist

Not "worked badly" — **there was no way to delete a memory at all.**
`MemoryManager` had `store_memory`, `search`, `update_memory`, and nothing else.
"Forget what I told you about my school" changed nothing and she carried on
knowing it.

It is a soft delete, via the `is_active` flag the model already carried and
`search` already honoured — so the FAISS vector stays in an append-only index and
its row is filtered out on read. No index surgery.

And it is **visible**, which is the actual criterion:

> Forgotten — I go to school at UW.
> 지웠습니다 — I go to school at UW.

Named, not counted. *"Forgotten — 2 things"* tells you nothing about whether the
right two went, and a forget that reports nothing is indistinguishable from a
forget that did nothing — which is precisely what the previous behaviour was.

Handled deterministically before routing, for the same reason cancellation is:
there is nothing here for a model to classify, and a request to be forgotten
should not depend on one agreeing. A1's finding is respected — *"forget the
mouse, what's a good film?"* is a topic change, not an instruction to the memory,
and there is a test for that.

---

## A3's headline number was one run of a flaky matrix

The phase's own contamination re-run is what found this, and it is a correction
to something already reported.

A3 shipped **12/12** on the contamination matrix. Re-running it here gave 12/12,
then 11/12. The failing case was `a_new_subject_closes_the_old_one`:

> **you:** actually forget the mouse, what's a good film for tonight?
> **her:** Enjoy the ride! The one I actually found is Best Wireless Gaming
> Mouse under $50.

None of A7's gates fire on any turn of that case — the code path was
byte-identical to before this phase — so I ran the case alone, repeatedly.
**Three of seven runs failed.** A3's 12/12 was a single run of a matrix
containing a case that fails about 40% of the time, and it was reported as
though it were deterministic.

### The cause was not variance

The variance was only in *whether the guard was reached*. When she happened to
invent a film name, the grounding guard fired and replaced it — reading the held
recommendation. And its relevance check was:

```python
if self._candidate_is_about(first, active_problem.subject):
```

The candidate is checked against **the problem's own subject**, which it is
about by construction. The check could never fail. So a mouse from an abandoned
problem was offered as the answer to a question about films.

Checking against the *turn* instead does not work either: this turn contains the
word "mouse", because dropping something means naming it.

### What settles it is that the person said so

`supersession.drops_a_named_subject()` reads the subject a turn explicitly
abandons — `"actually forget the mouse, what's a good film"` → `mouse` — while
leaving bare cancellations (`"forget it"`) to the pattern that already owns them,
and memory instructions (`"forget what I told you about my school"`) to A7's own
gate. It is now consulted in two places: the inheritance decision, ahead of the
model's `topic_shift` label, and this guard, which drops a held result about a
subject the person just abandoned.

**After: 5 of 5.** She answers with the honest fallback — *"I don't want to send
you somewhere I haven't checked"* — instead of the mouse.

A signal that is right two times in three is not a gate. That is the same lesson
as A5's ±3-turn noise floor, arriving from the other direction: **a live matrix
needs repeated runs before a number from it means anything.**

---

## Privacy, asserted rather than assumed

The plan says *"local-first storage — privacy is a design constraint, not a
setting"*. It is already true by construction: SQLite on disk, FAISS on disk,
sentence-transformers running locally, and **zero network calls anywhere in the
`memory/` package**.

So it is now a test. A cloud sync added for Milestone D's shared memory will fail
that test rather than ship quietly.

---

## The line between task state and memory already existed

Worth recording because it was a phase bullet and the answer is "it's done":

- **Task state** lives in `TaskSessionStore` — in-process, TTL'd at 15 minutes,
  explicitly session-scoped, and its own comment says historical state must never
  silently take part in the current turn.
- **Long-term memory** lives in SQLite, survives restarts.
- Research evidence shares the index but is excluded from personal recall by
  category, so *"how has my week been"* cannot come back with a hotel price as a
  fact about you.

Different objects, different lifetimes, one already-enforced exclusion.

---

## Exit criteria

| Criterion | Result |
|---|---|
| Recall accuracy ≥90% on the continuity scenarios | ✅ **34/34** on the gate |
| Zero cross-task contamination with memory on | ⚠️ **11–12/12**, and the matrix is flaky — see above |
| Forgetting works and is visible to the user | ✅ built from nothing |
| Nothing leaves the machine without the user asking | ✅ asserted by test |

---

## Carried forward

- **Cross-session decision history is not built.** *"You picked the M330 last
  time"* works for fifteen minutes and then does not, because
  `TaskSessionStore` is session-scoped by design. Making it durable means
  deciding what counts as a pick — a browser click, a spoken choice, an accepted
  recommendation — and that is a question worth measuring rather than a hook to
  bolt on at the end of a phase. A5 is the reason for the caution: a `decision`
  category that nothing meaningfully populates is another verified subsystem
  wired to nothing.
- **Shared memory across devices (Milestone D) is undecided.** The plan asks for
  the decision here. The honest answer is that the storage design does not have
  to change yet: SQLite plus a FAISS index is syncable as two files, and the
  question that actually needs answering — whether a second device gets a copy or
  a shared truth — depends on what D's device is. What A7 fixes in place is that
  the privacy constraint is now a test, so whatever D does has to argue with it.
- **The gate is a word-shape rule and will miss phrasings nobody listed.** It
  fails *open* in the safe direction: an unrecognised sentence falls back to the
  router's own booleans, which is exactly the previous behaviour, so a miss costs
  what it always cost rather than something new.
