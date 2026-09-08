# Conversation state: who owns what (Milestone A, A3)

Every piece of state a turn can inherit, who writes it, how long it lives,
and what clears it.

This document is A3's primary deliverable. The phase exists because the same
fault has now appeared in three separate sessions, and it is not a routing
fault, a planning fault or a grounding fault:

> **User:** 얼마나 우려야 돼? *(how long should it steep?)*
> **Elaina:** 런던 시간은 16:41이고, 한국 시간보다 8시간 빠릅니다.

The router classified that turn correctly, as a steeping time. The answer
path handed back the previous question's answer. Nothing measured that,
because routing accuracy and grounding both pass a *correct answer to the
wrong question*.

## The instrument

`tests/contamination_matrix.json` + `scripts/live_contamination_check.py`.
Twelve cases, each one a conversation that actually went wrong, each run
against a freshly restarted backend — conversational state lives in the
running `ChatEngine`, so two cases in one process would let the first
contaminate the second, which is the thing being measured.

```bash
.venv/Scripts/python.exe scripts/live_contamination_check.py
```

Each case declares what the reply must **not** mention (the marker of
inherited state) and, where the right answer is known, what it must.

## The inventory

### Owned, with one writer and a stated lifetime

These are the ones A3 does not need to change. `TaskSessionStore`
(`brain/task_session.py`) holds them, each has exactly one writer, and each
expires on its own clock.

| State | Written by | Lifetime |
|---|---|---|
| `_focus` — what the conversation is about | `remember()`, via `conversation_focus.update()` | expires; `Focus.expired()` |
| `_problem` — the open recommendation | `note_recommendation_turn()` | expires; cleared by `clear_recommendation()` |
| `_context`, `_history`, `_answered` | the same store | session, cleared by `clear()` |

`conversation_focus` was itself built to fix this class of problem: its
docstring records that the router, the goal layer and the recommendation
problem each used to keep their own answer to "what is this about". One
subject authority already exists. **The remaining work is not to build
another one — it is to stop the rest of the engine keeping private copies.**

### Held directly on `ChatEngine`, turn-scoped

Reset at the top of `chat()`, and honest about it:

- `_supersedes`, `_turn_visual_subject`, `_invitation_stands`
- `_turn_language`, `_pinned_language` *(A2; the pin deliberately outlives
  the turn)*

### Held directly on `ChatEngine`, lifetime unstated

This is where the faults live. None of these is reset in `chat()`, none
declares when it stops being true, and each is read by layers that assume
it is about the current turn.

| Field | Reads | What it holds |
|---|---|---|
| `_pending_action` | 23 | the action a turn is waiting on |
| `_grounded_context` (+ `_grounded_context_text`) | 16+2 | the last verified evidence |
| `_browser_result_is_final` | 10 | whether a browser result is the last word |
| `_last_computer_action`, `_last_computer_goal` | 8+1 | what the desktop layer last did |
| `_last_search_query` | 7 | the query behind the current evidence |
| `_last_research_evidence` | 7 | the evidence itself |
| `_last_claim` | 5 | the last checkable thing she asserted |
| `_source_override` | 1 | a site the user named |

`context_policy.should_include_grounded_context()` already guards one of
these — evidence is only included when the subject agrees — and it is the
model for the rest: **a piece of state should carry the subject it is about,
and be refused when the subject changes.**

### Gates: owned, but with scattered lifetimes

| Gate | `offer()` | `clear()` | |
|---|---|---|---|
| `capability_offer` | 7 sites | **16 sites** | an offer waiting for a yes |
| `clarification` | 5 sites | **8 sites** | a question waiting for an answer |
| `action_ledger` | per-turn | `begin_turn()` | what actually ran |
| `task_strategy_consent` | 1 | — | a strategy offer |

Sixteen call sites deciding when an offer stops being open is the same
shape as six modules deciding what the conversation is about, and it has
the same consequence: the lifetime is not a rule, it is the sum of every
place someone remembered to call `clear()`. A2 found one of the misses —
an offer appended to a goodbye stayed open, and the next turn was read as
accepting it.

## What A3 changes

1. **Every field in the third table gets a stated lifetime** and, where it
   describes a subject, carries that subject so it can be refused when the
   subject changes — the `context_policy` pattern, generalised.
2. **The gates get one owner each.** Clearing becomes a rule about the
   conversation, not sixteen decisions.
3. **The routing/state half of `_answer_turn` becomes a named stage** with
   a typed hand-off, per Rule 2 of the plan.

## Results

| Run | Turns answered without inheriting |
|---|---|
| baseline | 9/12 (75%) |
| after a history rule bolted onto two of three builders | 8/12 (67%) |
| after one TurnContext, read before the turn moves the subject | 11/12 (92%) |
| **after the topic boundary and the results that follow it** | **12/12 (100%)** |

Target is ≥95%. **Met.** English conversation quality is unchanged at
30/32 (94%), so nothing was bought from A1.

### What actually fixed it: an ordering bug, found by logging both outcomes

The first attempt added a subject-agreement rule to `reset_history`. It was
correct, it was covered by tests, and it **never fired once** in twelve
cases. Two separate reasons, and neither was visible until the log reported
the decision when it *inherited* as well as when it acted:

**One.** The rule read `task_sessions.focus()`, which only the task planner
ever writes. On an ordinary conversational turn there is no focus at all,
so the comparison had nothing to compare. The open recommendation is the
layer that carries a subject through chat.

**Two, and the real one.** With that fixed, the log said:

```
[Context] Inheriting the conversation (was mathematics, now mathematics)
```

`note_recommendation_turn` folds the current turn into the open problem
*during routing* — before the answer is assembled. So by the time the
inheritance decision read the subject, this turn had already overwritten
it. The comparison was always "current against current" and was
structurally incapable of disagreeing.

The subject is now captured at the top of `chat()`, before anything touches
it, and the same turn reports:

```
[Context] Starting clean: the turn is about 'mathematics',
                          the conversation was about 'emotional state'
```

**A rule that never fires and a rule that is never reached look identical
from the outside.** The only reason this was found is that the log was
changed to report the decision either way. That is worth more than the fix.

### The extraction

`brain/turn_context.py` — one `TurnContext` per turn, built before anything
branches, read by all three prompt builders. The third of them,
`_build_tool_result_messages`, previously had **no history control at all**,
and it is the path a successful calculation plan takes: no rule added to the
other two could have reached `what's 2+2`, and none did.

### A topic change moves a line, it does not excuse a turn

`which_one_means_the_nearest_subject` survived all of that, and the trace
showed every decision being made correctly:

```
[Context] Starting clean: the router says the topic moved      <- dinner
[Context] Inheriting the conversation (was dinner, now dinner) <- "which one"
reply: I'd go with the RTX 4080 ...
```

The subject was dinner. The topic change was seen. The next turn genuinely
did belong to the dinner conversation. The *scope* was wrong: starting
clean emptied that one prompt and left the GPU exchange in the manager, so
the next turn -- which legitimately inherits -- pulled it straight back.

`ConversationManager` now holds a boundary, and history is taken from it
forward. The transcript is still the transcript: the boundary is counted,
not deleted, because other layers audit the whole session.

And then the same lesson one layer over. With history retired, the reply
came back:

> **User:** actually forget the mouse, what's a good film for tonight?
> **Elaina:** A perfect pick for tonight? The one I actually found is
> AmazonBasics Wireless Mouse.

Assembled by the guards that name what a search found, reading a result
set from the subject the turn had just left. **Held results are as
inheritable as held turns**, and are now retired by the same decision.

### Three instrument corrections, recorded because they are corrections

Changing an instrument after seeing its results is the move that most needs
to be visible, so all three are in `tests/contamination_matrix.json`:

1. `refusal_is_not_a_request` matched the bare noun 추천, which also appears
   in "영화 추천은 잠시 보류하겠습니다" -- the refusal being *accepted*. The
   markers are now the verb forms that offer a film.
2. `a_correction_outranks_what_was_held` had the correction itself as the
   turn under test, and "That's great news" is a fine reply to a statement
   that names no city. The turn is now a question that forces the held
   value to surface.
3. The same case then failed on `must_mention: Tacoma` because she
   *invented* a location. That is a grounding failure and a real one, but
   it is A7's question, not this matrix's. `must_mention` is now scoped in
   the file's own header: it belongs only where its absence **is** the
   contamination -- the inherited answer had no number, or named the clock
   instead of the coffee.
