# A4 — Capability contracts

**What the phase was for:** a capability should declare what it takes, what it
returns, and how it fails — as types, not prose. The registry
(`brain/capabilities.py`) already answered *what can I do* and *may I do it
right now*. It did not answer any of the three above.

**Instrument:** [`scripts/capability_contract_report.py`](../scripts/capability_contract_report.py),
written before the fixes, run before and after. Enforcement is
[`brain/capability_contract.py`](../brain/capability_contract.py) — the same
module, per Rule 1.

```bash
.venv/Scripts/python.exe scripts/capability_contract_report.py
```

| | Before | After |
|---|---|---|
| Capabilities with a contract | **0 / 8** | **11 / 11** |
| Authored sentences naming internals | **14** | **0** |
| Capabilities the selector could dispatch and the registry denied | **3** | **0** |
| Ability answers available in Korean | **0 / 11** | **11 / 11** |
| Tool selection accuracy | 95.6% | **95.6%** (43/45) |
| Conversation quality (EN arcs) | 30/32 (94%) | **31/32 (97%)** |
| Tests | 3035 | **3086** |

---

## The failure set was the broken half

Success paths were already in reasonable shape — `browser_outcome`,
`task_outcome` and `CalculationPlan.as_trusted_result_text()` all predate this
phase. Failure was where nothing was declared, so every `except` block invented
its own sentence. Twenty-two of them, independently.

Counted at the start: **43 places interpolate a Python exception class name into
a string**. Most are log lines and correct. Fourteen were sentences the user
hears:

```
I couldn't complete that web search: ConnectionError:
HTTPSConnectionPool(host='duckduckgo.com', port=443)
```

Three things are wrong there and only one is cosmetic. It says nothing anyone
can act on; it is written in English regardless of the language of the turn, so
a Korean turn ends in a foreign-language stack fragment — which A2 already
measured as the worst kind of reply, *worse than the wrong language outright*;
and the words were chosen at the moment someone caught an exception, which means
the product's failure vocabulary was the union of twenty-two guesses.

A failure is now a declared thing: a code, a sentence in each language, a fix
where one exists, and `retryable` (which A5 will read). The exception still
exists — it goes to `CapabilityResult.detail`, which is logged and never spoken.

### The worst of the fourteen

```python
elif uses_vision_model:
    reply = (
        "I couldn't analyze the screen. Please check that the "
        f"Ollama model '{self.vision_model}' is installed and "
        "supports images."
    )
```

Nothing rephrases that one. It *is* the reply, spoken aloud, naming an
inference runtime and a model tag to someone who asked what was on their
screen — in English, whatever language they asked in.

---

## Ten sentences named a JavaScript framework

`"A Git proposal is visible in Electron."` — true, and useless. What the person
is looking at is a panel with an Approve and a Reject button. All ten now say
*on screen*.

`config.yaml` was deliberately **not** treated the same way, and there is a test
saying so. The person owns that file and edits it; "enable browser_control in
config.yaml" is the most actionable sentence a blocked capability can produce.
The line between the two is whether the word names something they can act on.

---

## Three surfaces the selector could choose and the registry denied

`capability_selection.py` has dispatched `project_edit`, `git` and
`agent_building` since Phase 4. `brain/capabilities.py` had never heard of any
of them. Its own comment said so and called registering them "the tidier end
state", which undersells it — the registry is what tells the model what she can
do, so an undeclared surface is one she will deny having.

She was being told to deny them, in as many words. `personality_en.txt`, line 6:

> Never claim, imply, or offer a capability that is not on that list (tracking
> something over time, future alerts, reminders, **editing files, Git,
> calendar**) unless a listed agent and tool actually support it.

Three of those six examples are abilities she has. `calendar_action` has been in
the registry since before A1. This is the same shape as the bug the registry was
built to kill — *"That PC action isn't supported yet"* for browser control —
still live, and this time pointed at by the prompt rather than merely unnoticed
by it.

All three are registered now, with contracts. Their failure sets are the most
load-bearing in the table, because all three sit one approval away from changing
something real: *"it didn't work"*, *"you said no"* and *"it is waiting for you"*
have to be three different sentences. `UNREGISTERED` is now an empty frozenset
rather than deleted — an empty exception list is the assertion that there are
none.

The deny-list examples in both personality files now name things that are
genuinely out of reach (watching over time, alerting later, acting on a
schedule), plus one added line: *do not deny an ability the list carries.*

---

## A failed search was being used as evidence

Found while typing the result path, and the most serious thing in the phase.

```python
try:
    results = self.search_web_structured(query, max_results)
except Exception as error:
    return f"Web search failed: {error}"      # <- a str, like a success
```

A backend failure came back as a **string, indistinguishable at the type level
from results**. It went into the prompt as the research the answer was built
from, so the model answered from its own memory while the system believed the
answer was grounded. And `ChatEngine.search_web` caches whatever it gets — so
one network blip was served as evidence to every matching query for the rest of
the cache window.

What stood between the two was `ResearchAgent._is_failed_result`, matching the
**English prefix of that sentence**:

```python
normalized.startswith("web search failed:")
```

A guard that any rewording disarms — and this phase spent its first hour
rewording exactly these sentences. That is the failure mode this project has a
standing rule against.

A backend failure now raises, matching what `search_web_structured` already
documented (*"only a genuine search-backend failure raises"*), so the two halves
of the same module finally agree. Finding nothing is still a returned string,
because finding nothing is a real outcome and not a failure. The caching bug
went away for free: the exception propagates before the cache write.

---

## Registering a capability was not enough

The static half said 11/11 and PASS. One live probe said otherwise:

> **You said:** can you commit changes to git for me?
> **Elaina:** I can't commit changes to Git right now. The Git Agent says
> nothing was staged, committed, or pushed. Check the console error for details.

She had not answered the question — she had **run the git action**, which
correctly reported nothing staged, and rendered that as an inability. Two gaps
behind it:

1. `_MATCH_PATTERNS` had no pattern for any of the three, so
   `CapabilityRegistry.match()` could not find them.
2. `_answer_ability_question` allow-listed `browser_control` and `ui_control`
   only, so even a match would have fallen through.

Both fixed; a question about any registered ability is now answered from the
registry. The three new patterns require a word naming the project, the code or
the repository, so *"make a folder on my Desktop"* stays `ui_control` — there is
a test asserting the four existing surfaces were not stolen.

**And the last sentence was a leak the scan had not been looking for.** There is
no console in front of the person; they are talking to her. `developer_surface`
was added to the detector, and it immediately found the *authored* source of
that clause in `chat_engine.py` — the model had not composed it after all.

## The registry answered in English, deterministically, every time

The same probe, third question:

> **You said:** 깃 커밋 할 수 있어?
> **Elaina:** Yes. I can prepare a commit for this project. Want me to use it now?

This path is deterministic on purpose — it answers from the table so the model
cannot deny an ability she has — and **deterministic is exactly what "silently
English" looks like from outside.** A correct answer in the wrong language,
every single time, and nothing in a green suite says so. It was pre-existing;
adding a Korean match pattern for `git` is what made it reachable and therefore
visible.

Every capability now carries `name_ko` and `summary_ko` with the same dash
convention, and the five answer frames live in `guard_lines.py` beside the other
sentences guards say. A missing translation falls back to English rather than to
silence: a wrong-language answer is a bug you can see, and a missing one reads
as her not having the ability — which is the failure the whole registry exists
to prevent.

`context_text` stays English. It is one prompt block the model reads either way,
and duplicating the inventory would spend tokens saying the same thing twice.

---

## Typed inputs, and the one place they had to earn their keep

A declaration nothing reads is documentation. The calendar path is where the
needs became load-bearing, and it had the duplicate sitting in it already:

```python
missing = []
if not str(values.get("summary", "")).strip():
    missing.append("the event title")
if not str(values.get("start", "")).strip():
    missing.append("the date and start time")
```

Two inputs the contract already declares, described a second time, and asked for
in a sentence written in English — of someone who may well have said
*"금요일 저녁 약속 잡아줘"*.

`agents/` does not import `brain/`, and that boundary is worth keeping, so the
split follows the knowledge: the agent reports **which contract keys** are
missing, because it is the thing holding the draft; `ChatEngine` phrases the
question from `capability_contract.ask_for(...)` in the language of the turn,
because it is the only layer that knows what language that is. The agent's
English sentence stays as the fallback for a caller with no language.

`calendar_action.when` is declared `inferable=False` — a guessed time on a real
calendar is a wrong appointment, not a wrong sentence.

---

## Where the runtime guard is, and why it is narrower than the scan

Fixing fourteen authored sentences stops Elaina *choosing* those words. It does
nothing about routes nobody authored — a model echoing an exception back out of
a tool-result prompt, a library message arriving through an MCP tool, a call
site written next month. So `redact_internals` runs in
`TextFilter.for_voice_response`, the method every reply path in `chat_engine`
already ends in. It drops the clause, not the fragment: *"I couldn't reach the
site: ConnectionError"* minus two words is a sentence that stops mid-promise.

It is deliberately **less strict than the static scan**, in exactly one place,
and this was a correction made during the phase rather than a design:

> The first version flagged any `.py` filename and any capitalised
> `SomethingError`. Both are correct for a sentence a developer wrote into the
> source. Both are wrong at runtime, because Elaina reads *this project's own
> code* — "that function raises a ValueError when the list is empty" is the
> answer to a project question, not a leak. The guard as first written would
> have started censoring the substance of every project answer the day project
> access was used in anger.

The rule is now the punctuation, not the word. Every one of the forty-three
leaking sites wrote the same shape — `f"...: {type(error).__name__}: {error}"` —
so a class name introduced by a colon or bracket, or trailing its own colon, is
debris; a class name in the middle of a sentence is the subject. `leaks_internals`
takes `authored=True` for the scan, which adds back the module-path rule.

---

## Exit criteria

| Criterion | Result |
|---|---|
| Every capability has typed I/O and a declared failure set | **11 / 11** ✅ |
| A capability cannot be added without a contract | registry test ✅ |
| Tool selection accuracy ≥95% | **95.6%** (43/45) ✅ |
| No tool result reaches the reply as unstructured prose | **partial** — see below |

No regression elsewhere: conversation quality **31/32 (97%)** on the same three
English arcs, against 30/32 before. The finding that cleared was a
`list_recital` in the tooling arc — which is the path this phase rewrote — but
one run is one run, and the honest reading is "held, possibly improved", not
"improved by three points".

### The one that is partial, and why it is not being finished here

Failure results are typed across every capability. Of the four success paths,
three were already typed before A4 (`browser_outcome`, `task_outcome`,
`CalculationPlan`). The fourth — the web-search payload — still reaches the
prompt as formatted prose, even though `search_web_structured` sits right beside
it returning title/url/summary.

Converting it is **A6's work, not A4's**. A6 is attribute grounding: every
attribute attached to the candidate *and* to where it came from, with
per-attribute confidence and conflict handling. Typing the payload now to A4's
requirements and re-typing it in three phases' time to A6's is the same work
done twice, and the intermediate shape would be the one nobody wanted.

The concern the criterion was written for — *"which is why a raw accessibility
tree could ever have reached speech"* — is separately covered, and was already
covered before this phase: A1's `conversation_style.speakable_fragment()` holds
that boundary at the fragment level, and `redact_internals` now holds it at the
sentence level.

---

## Carried forward

- **The web-search success payload** → A6, above.
- **`ResearchAgent._is_failed_result`** still exists and still matches English
  prefixes. It is now a second line rather than the only one — the raise is the
  real signal — but it is dead weight that should go when A6 types the payload.
- **`agent_building` has no availability requirement** (`needs=()`), so it is
  never reported as blocked. Whether building a new agent should be gated the
  way desktop control is has not been decided; it is a consent question, and A5
  is where consent-before-committing lives.
