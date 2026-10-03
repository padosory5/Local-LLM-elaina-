# Simulated learners: does the conversation help this person understand?

*2026-10-01. Step 1 of the understanding-loop direction: define and measure success before
building anything. `evals/sim_learner.py`, with profiles in `evals/learners/learners_v0.json`.*

## Why a new evaluation

The objective is: **can Elaina carry on a natural conversation that helps this particular person
understand the concept?**

Every earlier evaluation scored single replies against a rubric, in scripted conversations. The
person's next line was fixed, whatever Elaina said, so no evaluation could test whether she infers
what someone didn't get and adapts. Here the person reacts.

`concrete_first`, `terms_explained`, `leads_with_the_answer` and the rest stay useful as signals.
They are not the target.

## How it works

**The learner** is a person with a hidden profile. A different model plays them (the 27B; Elaina
runs on the 8B). Each profile has:
- who they are and how they talk (English, or casual Korean 반말);
- what they know and what they don't;
- a misconception, if they have one;
- 2–3 ideas they need to grasp.

Each turn, the learner reads Elaina's reply and decides:
- which goal ideas now make sense, an idea counting only if it was explained through things they
  know;
- what they didn't follow;
- what to say next: a question about a word, "I'm lost", a wrong guess from their misconception,
  or "oh, got it".

They don't pretend to understand to be polite. Understanding only accumulates.

**Elaina** is the real backend over its websocket, one fresh backend per conversation, at
production settings. She is told nothing about the person.

**The conversation ends when:**
- the person understands everything;
- they give up;
- the simulator's output can't be read;
- or after 8 of Elaina's replies.

**The judge** is a third model, the rubric judge's `qwen3.6:35b-a3b`. It reads the finished
transcript with the person's hidden profile, never the learner's own claims. It says:
- **for each goal idea:** whether it was actually conveyed in words this person could follow, and
  in which reply;
- **for each of Elaina's replies:**
  - pitched at this person, neither over their head nor talking down?
  - after confusion, a changed approach or the same explanation repeated?
  - a question asked, and was it needed?
  - natural speech?
- **overall:** helped understand (yes / partly / no), and a natural conversation (yes / no).

## What is measured

| measure | meaning |
|---|---|
| **understood (verified)** | the learner understood every idea *and* the judge finds every one conveyed. The primary outcome. |
| **replies to understanding** | median, verified conversations |
| **gave up** | the person stopped trying |
| **pitched at this person** | share of replies |
| **changed approach after confusion** | share of replies that followed a confused message |
| **unneeded questions** | replies asking something she could have worked out (your "don't always ask which part") |
| **natural** | share of replies |
| **ending not drafted** | replies whose last sentence says something the model's draft did not, i.e. added or replaced by a later stage (from the turn traces; simulator v2) |
| **reply time** | includes model swaps in this setup, so read it relative to other runs |

**Concept-specific and person-specific by design.** Two learners share a concept, model
quantization: a novice with a misconception and a CS student. The same explanation can be right for
one and wrong for the other.

## Calibration comes first

Simulated learners are known to understand too easily, and the judge is a model too. **No number
here is trusted until a person agrees with it:**

```bash
python -m evals.sim_learner --export-calibration RUN --count 5
```

This writes transcripts with the person's hidden profile. For each, you answer:
1. Did Elaina help this person understand?
2. Was it a natural conversation?
3. Does the person sound like a real, confused person?

If your ratings and the learner's or judge's outcomes disagree, the simulator or the judge is
fixed first. That might mean the learner being too easy to convince, or the judge being too
generous.

## Running it

```bash
python -m evals.sim_learner --run pilot-current --arm current
python -m evals.sim_learner --judge pilot-current
python -m evals.sim_learner --report pilot-current
```

| arm | what it runs |
|---|---|
| `current` | production, explanation experiment off |
| `revised` | the revised explanation contract on (for comparison) |

Each conversation is a fresh backend at production settings, with no TTS.

**Cost:** the 27B learner and the 8B Elaina don't fit on the card together, so every exchange swaps
models (about 12 s). Nine conversations of up to 8 replies take about 20–30 minutes, plus a few
minutes of judging.

## Pilot 1 (2026-10-01, current production): what it showed

**About Elaina.** These are conversation-level failures that no single-reply test could see:

| conversation | what went wrong |
|---|---|
| dividend | **Repeated herself.** Asked "배당률이 뭐야?", she said the same sentence again without explaining it. Her first answer also reinforced the misconception ("배당금은 주가의 일정 비율로 결정"). |
| how AI answers | **Contradicted herself.** First "no internet search", then "a real-time access function knows tomorrow's weather". Asked about the contradiction, she denied it. The person gave up. |
| greenhouse effect | **Invented an analogy and would not let go.** "The Earth sweats." Three times she rejected the person's own correct greenhouse comparison. The output was garbled ("트 trapped"). |
| quantization, both languages | **Jargon-first openings for a novice:** 32-bit floats, 가중치, 정밀도. |
| statistical significance | **A first example that taught the misconception:** "80% improvement … is significant". |
| API | **A stray offer instead of a definition:** "want me to look up real ones?" |

**Tone.** The judge found 44% of her replies natural and 51% pitched at the person; the rest read
as formal and lecture-like.

**About the measurement.**

| problem | fix (simulator v1) |
|---|---|
| A person said "포기할래" but was recorded as understood, because they had also claimed every idea | "gave up" always wins |
| The learner supplied the key idea itself (and in the CS case named the answer), then credited it | The learner may only say back what Elaina said, or guess from their misconception. Agreement ("you're getting close") is not understanding. |
| English novices accepted "32-bit floats" without objecting | Each profile now lists the words the person does not know (`unknown_words`). One used unexplained blocks that part. |
| The judge credited ideas the person stated when Elaina only agreed | The judge prompt now says agreement is not conveying |

Pilot 1's figures are kept in `runtime/evals/sl/pilot-current` and are not used for any
comparison. Pilot 2 reruns the same production Elaina with simulator v1.

## Pilot 2 (2026-10-01, current production, simulator v1): the user's ratings

The learner said it understood in 8 of 9 conversations, and the judge verified 7. The median was 4
replies to understanding. 54% of replies were pitched at the person and 44% were natural. 70%
changed approach after confusion.

The user rated 8 transcripts and mostly agreed with the judge. Their notes:

**About the simulated person.** "Most people just ask simple questions like I don't understand
this part or whats blahblahblah … realistically people will just ask simple questions until they
figure out." The 27B asked two or three analytic questions per message and recapped Elaina's
explanation back to her. It was also too lenient on the dividend and greenhouse conversations.

**About Elaina.**
- **"She keep on saying weird and unrelated stuffs on the end of the sentence."**
- **Check questions weren't answered as asked.** "If the person asks 'something something, is
  this correct?' Elaina should reply to that question saying yes your understanding it correctly
  or no thats not quiet it and give on easier explanation."

### Where the odd endings came from

Each reply was compared with the model's own draft in the turn traces
(`ending_not_drafted`; a sentence counts as new when under 60% of its content words are in the
draft, so a 해요→습니다 switch is not new). Across pilots 1 and 2, **14 of 80 replies ended on
something the model did not say:**

| source | replies | what it did | now |
|---|---|---|---|
| `premise_correction` | 7 | Replaced the answer with a context-blind one-liner. 3 of 7 were wrong: "오존층이 파괴되면 … 온도가 상승할 수 있습니다"; "정밀도가 낮아도 정확도가 떨어지지 않을 수 있습니다"; "비닐하우스는 열을 트 trapped하여 … 땀은". 2 helped, 2 changed nothing. | Not judged on a follow-up inside an explanation (`premise_check.applies`), and the skip is noted in the trace. First questions are still checked. |
| `grounded_entities` | 2 | "Application Programming Interface" was read as an unchecked business, because "a weather app" counts as sending someone somewhere. The definition was deleted and "I don't want to send you somewhere I haven't checked" was said instead. | A name whose initials spell a term already said is that term's expansion (`_spells_a_term`). Invented shops are still caught. |
| `repetition_retry` | 1 | A garbled retry: "트 trapped". | Measured again in pilot 3 |
| `her_voice` re-say | 2 | One was a harmless paraphrase. The other: "지금까지 그런 생각이셨다면, 그런 식으로 답변해드리겠습니다." | Measured again in pilot 3 |
| `length_rewrite` | 1 | A paraphrase | No change |
| `grounded_values` | 1 | "찾아봤지만 구체적인 숫자는 확인되지 않아서 빼고 말씀드렸습니다." This was on a conceptual follow-up the router had sent to web search. | Measured again in pilot 3 |

Separately, **the preference parser** read "Okay, I get that it's not the shadow. But does the sun
always light up …?" as a saved favourite and answered "Got it — that it's not the shadow for
shadow." The question was never answered. "I always get confused by this part" had the same
problem. A favourite now has to be a name, held to the same test as "use X".

### Check questions

These turns are filed under every explanation shape (clarification, conversation, knowledge
question), and nothing told her to give a verdict. Every explanation goal now carries one rule
(`response_budget.CHECKING_UNDERSTANDING`), and so does the revised contract (rule 5; version
`cc22900f17a9`, which replaces `d398489d1bd7`):

> If they ask whether they have understood it right, start by saying whether they have: yes,
> partly, or not quite. Then, if any of it is off, put right just that part, in simpler words
> than before.

### Simulator v2

- **The person talks like most people.** One short, plain question at a time about what she
  just said ("what's a weight?", "그 비트 부분 잘 모르겠어", "wait, why?"). No recaps. Only now and
  then a one-line check ("so it's like X, right?"). Their questions come from what she said, not
  from their private goal.
- **The closing line is said to Elaina** (giving up included), and her reply is recorded. The
  judge and the rater see it, so no conversation ends on a question she never answered.
- **A new measure:** replies ending on something the model did not say.

## Pilot 3 (2026-10-02, production with the fixes above, simulator v2)

| measure | pilot 2 (sim v1) | pilot 3 (sim v2) |
|---|---|---|
| learner understood | 8/9 | 6/9 |
| ...verified by the judge | 7/9 | 5/9 |
| replies to understanding (median) | 4 | 4 |
| pitched at this person | 54% | 38% |
| changed approach after confusion | 70% | 47% |
| unneeded questions | 2% | 10% |
| natural | 44% | 34% |
| ending not drafted | 8/41 | 9/56 |

**The two columns cannot be compared.** The simulated person changed: in v2 they no longer carry the
conversation with long, analytic questions that hand Elaina half the answer. One run of each also
falls within run-to-run noise. Read these numbers as the new baseline.

**The person now asks plain questions:** "Wait, what's a weight?", "So the sun just keeps lighting
the same side of the moon the whole time?" Korean learners still often join a check and a new
question in one message ("…인 거지? 근데 왜 …?"), and remain lenient: the greenhouse learner
counted the ozone idea as understood while Elaina was still stating the misconception.

**The earlier fixes held.** No premise replacement fired on a follow-up. The one that did was on
a first question ("Why does the moon change shape?" → "Actually, the moon doesn't change
shape…"), where it is still meant to run.

**Three more stages added content the model didn't write.** Each is fixed, with a test:

| what happened | cause | fix |
|---|---|---|
| "What's a p-value?" got "Sap is a term used … to describe a type of tree … Could you clarify what you're referring to?" | The router read "p-value" as a spelled name and normalized it to "Sap". The draft defined a p-value. The draft was over the length budget, and the rewrite was shown "Sap" as the question. | `length_rewrite` is shown the person's own words, since it shortens a draft that answered them |
| "API" retracted as an unchecked place on a follow-up ("so it's not something I install, right?") | Only the current message counted as the person's words, and "API" was said in their first message. | Earlier messages in the conversation count as theirs too, except the current one when it disputes (unchanged) |
| A quoted example question, "오늘 날씨 어때?"라고 묻는다면, retracted as a title, and "실제로 찾아볼까요?" said | The quoted-speech rule only knew polite endings | A quoted question is speech, in any register |

**Still open, measured and not changed:**
- **The `grounded_values` line on a conceptual follow-up routed to web search** ("찾아봤지만 구체적인
  숫자는 확인되지 않아서 빼고 말씀드렸습니다"). This is the second run in a row; the cause is routing.
- **The model's own service closers**, re-said by `her_voice` ("궁금한 점이 있으면 구체적으로 말씀해
  주십시오").
- **Verdict first on check questions: about 4 of 10.** The rule reached the prompt on every
  check turn. The 8B follows it on a plain check ("You're right — …", "No, the sun doesn't…") and
  drops it when a check and a new question come together, answering only the new question.
- **Wrong facts from the model itself.** The 8B believes an ozone hole warms the Earth and said
  so in three drafts, with no later stage involved.

## What it does not do yet

- **One conversation per profile per run.** The learner samples, so runs vary, and repeats are
  needed before any comparison.
- **Nine learners.** Enough to see whether the measure works, not to rank designs.
- **No memory across sessions.** Each conversation starts fresh.
- **No visual or tool-based techniques.** It is voice and text only.
