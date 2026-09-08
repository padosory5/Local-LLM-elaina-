# Conversation quality baseline (Milestone A, A1)

How much of what Elaina says sounds like an interface rather than a person,
measured the same way before and after a change.

Nine benchmark phases had measured routing, agency, execution, memory and
lifecycle. None of them measured *how she sounds*, which is the same gap
[dogfooding session 1](../runtime/session1.log) found in conversational task
state: the unbenchmarked layer is the one that is broken.

## How it is measured

```bash
# 1. a clean backend per arc -- history lives in the running ChatEngine,
#    and a repetition fault is invisible inside a warmed-up session
ELAINA_OPEN_DESKTOP=0 .venv/Scripts/python.exe main.py

# 2. drive one ordinary conversation
.venv/Scripts/python.exe scripts/live_dogfood_conversation.py \
    --arc everyday --out runtime/after_everyday.json

# 3. score it
.venv/Scripts/python.exe scripts/conversation_quality_report.py \
    --before runtime/before_*.json --after runtime/after_*.json
```

Three arcs, 32 turns, deliberately crossing between chat and tool use:

| Arc | Turns | What it exercises |
|---|---|---|
| `everyday` | 12 | greeting, feelings, a calculation, a world clock, a knowledge thread, a topic change, a bare "nah" |
| `tooling` | 12 | a weather lookup, an ability question, a shopping search, a desktop read, task reactivation |
| `social` | 8 | greeting, low mood, three bare acknowledgements, a trivial sum, a goodbye |

Each turn declares the **conversational act** its reply should perform. That
is a statement about the conversation, not about the implementation -- a
reply to "thanks" is a receipt whoever writes it -- so the same table scores
a run from before a change and a run from after it.

Scoring is `brain.conversation_style.RoboticTells`, which is the same module
the running system consults before it speaks. That is deliberate: a quality
metric measuring something the product does not enforce drifts away from it,
and then a green report and a robotic assistant are true at the same time.

## Failure classes

| Class | What it is |
|---|---|
| `service_phrasing` | the register of a support desk: "anything else", "happy to help", "please let me know" |
| `robotic_acknowledgement` | a canned receipt where an answer was owed |
| `request_restated` | opening by giving the person's own words back, adding nothing |
| `duplicate_offer` | more offers in one reply than the act has room for |
| `empty_response` | "All set." where something was owed |
| `stiff_followup` | the bureaucratic form of an offer: "shall I proceed", "please confirm" |
| `reactivation_awkward` | coming back to a subject the way a ticket system would |
| `tool_narration` | describing the machinery instead of the finding |
| `internal_language` | a log marker, an element handle, a snake_case identifier |
| `unnatural_confirmation` | saying a thing succeeded the way a return code would |
| `too_verbose` | longer than the act allows |
| `structural_artifact` | damage: an unpaired quote, markdown, a semicolon chain |
| `self_repetition` | the same opener, sentence, or whole line said again |
| `list_recital` | a registry read out as prose |

## Results

Measured against `qwen3:8b` on the same 32 turns, two runs each side (64
turns), with the backend restarted between every arc.

| Date | Build | Turns | Clean | Clean rate | Findings |
|---|---|---|---|---|---|
| 2026-09-08 | before A1 | 64 | 41 | 64% | 29 |
| 2026-09-08 | A1, sentence-bounded receipts | 64 | 50 | 78% | 19 |
| 2026-09-08 | A1, word-bounded receipts | 64 | 57 | 89% | 9 |
| 2026-09-08 | **A1 final** | 64 | **58** | **91%** | **6** |

By class, before to after:

| Class | before | after |
|---|---|---|
| `list_recital` | 6 | 2 |
| `service_phrasing` | 6 | 1 |
| `too_verbose` | 5 | 1 |
| `self_repetition` | 4 | 2 |
| `structural_artifact` | 3 | **0** |
| `internal_language` | 2 | **0** |
| `reactivation_awkward` | 2 | **0** |
| `request_restated` | 1 | **0** |
| **total** | **29** | **6** |

The middle row is the interesting one, and it is left in rather than tidied
away. A one-sentence ceiling on receipts looked right and measured worse:
it called "I'm here. You don't have to talk if you don't want to." a failure,
and then **rejected every rewrite offered for a genuine one**, because a good
short reply is usually two clauses as well. Receipts are bounded in words
now. A contract that condemns ordinary speech does not merely misreport --
it spends model calls making replies worse.

## What the style layer actually did

Counted from the backend logs over 64 turns:

| | |
|---|---|
| turns judged | 64 |
| said again in her own voice, accepted | 11 |
| said again, still wrong, original kept | 6 |
| rejected for changing a value or dropping a name | 2 |
| structural repairs | 3 |

The value guard fired twice in sixty-four turns: a rewrite came back
having dropped a name, and the original was kept. That is the number to
watch, and it is why the guard is two-sided -- an invented value and a
dropped one are the same defect wearing different clothes.

## Cost

| | before | after |
|---|---|---|
| median turn | 3.8s | 3.8s |
| mean turn | 4.5s | 4.6s |
| p90 turn | 7.7s | 13.0s |

The median and the mean are unchanged: most turns pass the review and
cost nothing. The p90 is the re-say call, which fires on about a quarter
of turns and is the whole price of this phase. It is one call, never two
-- a reply that is merely late is worse than one that is merely stiff.

## The architecture behind the numbers

`brain/conversation_style.py` is the new authority on *how a reply sounds*.
It cannot change a value and knows nothing about truth; it names the
**conversational act** a reply performs, states the **contract** for that
act, and detects the failure classes above.

Five controls read it, all in `ChatEngine._answer_turn`:

1. **Act classification.** One name, decided once per turn, from what the
   turn actually is: a waiting question, a waiting consent question, a
   goodbye, a greeting, a bare acknowledgement, something that happened on
   the machine, a remark about feeling, or an answer. Ordered most specific
   first, and the order is the design -- the two locked acts are tested
   before anything else.
2. **Response-style policy.** `style_instruction(act)` goes into the
   generation prompt last, nearest the message being answered.
   `personality.txt` says who she is once at the top of a long prompt; this
   says what *this* reply is for.
3. **Length policy.** The act tightens the configured length and never
   loosens it, and steps aside entirely when the user asked for detail.
   Acts whose whole job is brevity are bounded in words, not sentences.
4. **Realization.** `_say_it_in_her_voice` repairs structural damage
   always, and asks for the reply again when its *register* is wrong.
   Locked acts are repaired, reported, and left alone. The rewrite brief
   names the fault the detector found and, for repetition, lists what she
   has already said -- a generic "sound natural" brief measured badly.
5. **Offer policy.** `_append_recommendation` now asks the act whether
   there is room. A receipt, a greeting and a goodbye have none.

Everything the style layer produces then passes the existing
action-commitment, grounded-value, named-candidate and unearned-success
guards, unchanged. Style runs before them on purpose: a realized sentence
is still a claim.

## Known limitations

- **`self_repetition` halved but did not clear** (4 before, 2 after). The
  layer detects it, asks for the line again, and tells the model what it
  already said -- and qwen3:8b still returns to the same opener ("The
  UtechSmart Venus Pro...", "You're not alone in this."). Six of seventeen
  rewrites were rejected for still reading wrong. This is the residual, and
  it is a model-capability limit rather than a missing control.
- **A factual question inside a social thread is intermittently answered
  from the thread.** "what's 2+2" after four sympathy turns came back "The
  sum of 2 and 2 is 4." in one run and "That's straightforward. Need help
  with." in the other. The router classified it correctly as a calculation
  both times; the answer path lost it. That is a context fault, not a style
  one, and it is the strongest argument for A2 continuing here.
- **The two remaining `list_recital` findings are the instrument, not the
  reply.** Asked "what windows do I have open", she lists the windows,
  which is what was asked for. The detector cannot currently tell an
  enumeration the user requested from a registry read out unbidden.
- **The style layer only bounds offers it appends.** An offer the *model*
  writes into a receipt is still counted and still re-said, but a
  truthfulness guard's parked offer outranks the act contract by design --
  truth wins over brevity.
- **A page title can still reach a card or a sentence** as though it were a
  thing ("Movie recommendations: What should you watch tonight?"). Known
  entity-acquisition gap, unchanged by this phase.
- **A truncated sentence is not yet a failure class.** "Need help with."
  ends in a full stop and passes every structural check.
