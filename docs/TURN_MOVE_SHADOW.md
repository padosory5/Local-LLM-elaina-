# Turn moves in shadow

*2026-10-02. Steps 2–3 of the understanding-loop plan, cut to the minimum and in shadow. The code
is `brain/turn_move.py`, the replay is `evals/turn_move_shadow.py`, and the tests are
`tests/test_turn_move.py`. Nothing the model is shown changes.*

## Why

Every wrong or repeated explanation in pilot 4 came from the 8B's own draft. The choice of how to
answer each turn ignored the conversation: it came from the router's label of the latest sentence
alone.

- **The same question, four times, got the same answer.** Each time it got the same "explain"
  goal. The model copied its previous answer word for word: greenhouse turn 6 = turn 7. The repeat
  was noticed only after drafting, by a step that may reword and nothing more.
- **A challenge got the same goal.** "How does that become 23?" questioned her own number, but was
  filed as an ordinary explanation question, and the model said "23" again.
- **Sub-questions dropped the conversation.** "학습 데이터가 뭐야?" counted as a new subject, so the
  prompt carried no history. That happened on 13 of 47 follow-ups in pilot 3 and 5 of 45 in pilot 4.

## What it decides

Before the draft, from a small record of the conversation (the person's questions and her replies
on this thread):

| move | when | what it would tell the model (unused in shadow) |
|---|---|---|
| `first` | a new thread | nothing extra |
| `continue` | a follow-up on a new point | nothing extra |
| `differently` | they asked this before | "This is the Nth time they have asked this… Do not reuse it or reword it: "…". Start from something they already know." |
| `check` | they question something she said | "They are questioning something you said: "…". Check it before anything else…" |
| `not_explaining` | the turn is not an explanation | nothing |

**The thread** carries on across a change of subject when the router marks a follow-up, so a
sub-question stays in it. It ends when a turn is not a follow-up or the topic moves.

**The signals** are worked out from the words; no model is called.

- **Asked again:**
  - At least 60% of the content words in this question appear in one earlier question of theirs.
  - That share is at least 3 words, or the whole question when it is shorter.
  - Content words are as `told_not_asked` reads them: Korean by the first two syllables, English
    whole. Words that carry how a person talks ("아니 근데", "wait, so") don't count.
- **Challenged:**
  - The router reads the turn as a correction; or
  - the message quotes at least 2 of her numbers back and sets a number of its own against them.

**Recorded in every answered turn's trace** under `context.turn_move`: the move, thread length,
attempts, which earlier turns asked the same thing, the kind of challenge, the goal production
used, whether production kept the history, and the line the move would have added.

## Replay over the recorded pilots

`python -m evals.turn_move_shadow pilot4-current pilot3-current pilot2-current`

**Written expectations, set before the first replay: 7 of 7 met.**
- Greenhouse (pilot 4): turn 4 `continue`; turns 5–8 `differently`.
- CS student (pilot 4): turns 6 and 7 `check`.

| | pilot 4 | pilot 3 | pilot 2 |
|---|---|---|---|
| `differently` | 5 | 5 | 0 |
| `check` | 2 | 0 | 0 |
| production's goal on those turns | explain on 6 of 7 | explain on all 5 | – |
| follow-ups the thread kept that production started clean | 5 of 45 | 12 of 46 | 6 of 31 |

Every `differently` and `check` turn was read by hand:
- **Asked again:** greenhouse (pilot 4, turns 5–8; pilot 3, ozone, turns 4–5); "학습 데이터가 뭐야?"
  again; the dividend misconception asked again; "왜 메모리가 덜 쓰인다는 말이야?" again; "how do
  you actually map a 32-bit float…" again.
- **Challenged:** "0.0023 times 1000 is 2.3, how does that become 23?" and "2.3 rounds to 2, not
  23".

**The first replay had two false alarms, and the thresholds were raised for them:**
- "회사 이익이 뭐야?" shares only 회사 and 나눠 with an earlier question, so asked-again now needs 3
  words or the whole question.
- "map a 32-bit float to an 8-bit integer" names one of her numbers in a how-question, so a
  challenge now needs 2 of her numbers.

These were tuned on the same pilots they are checked on, so a fresh run is the real test.

**Known misses:**
- **Challenges without numbers are not seen.** "열을 반사한다고?" questioned "반사", and only
  numbers and the router's own correction label count.
- **One loose repeat is missed:** pilot 2's "인터넷에서 찾아온 거 아닌데?" shares only 2 words with
  the earlier question.

## Pilot 5: live, untuned (2026-10-02)

**The recording works.** The engine recorded a move on all 58 turns, and a replay of the same turns
agrees on every one.

**The signals failed the untuned test: 0 of 12.** Reading pilot 5 by hand, the person re-asked or
said they hadn't been answered 12 times, and the shadow flagged none:
- greenhouse "왜 바람에 안 날아가?" four ways;
- "인터넷에 없는 걸 어떻게 아는 거야?" three times;
- "그거 답이 아니잖아", "you didn't actually answer the 'how'", "That ruler thing is a bit vague".

Production's own "elaborate" goal fired on 4 of the 12.

**Why:**
- **Paraphrased re-asks share under 60% of their content words.** Greenhouse turn 5 shares 바람,
  날아 and 공기 with turn 3: 3 of 6. Pilot 4's re-asks were near-verbatim; pilot 5's person
  paraphrased, as real people do. The pilot-4 replay's 7/7 was fitted to the first kind.
- **"You didn't answer me" has no signal at all.** It is about her reply, not a repeat of their
  question.
- **Two ordering faults:**
  - A correction on a turn given the value budget was marked `not_explaining` before it could be
    marked `check` (dividend turn 7, "그거 답이 아니잖아", which the router read as a correction).
  - The thread resets on the router's follow-up flag, which was wrong on CS turn 4.

**Exploratory, not adopted:** similarity to an earlier question of theirs using bge-m3, the
embedding model memory already uses. Over pilots 4 and 5 with hand labels (one labeller):

| threshold | re-asks caught | other follow-ups flagged |
|---|---|---|
| 0.80 | 7 of 14 | 0 of 80 |
| 0.75 | 9 of 14 | 2 of 80 |

It misses the "you didn't answer me" complaints (0.57–0.70), which need her reply read against
their question.

## v2, before pilot 6 (2026-10-02)

Changed after pilot 5. The threshold was set before pilot 6 and will not be retuned on it.

- **Asked again** now uses sentence embeddings, not shared words. It fires when cosine similarity
  to an earlier question of theirs is at least **0.80**. The embedder is bge-m3, memory's model,
  reused rather than loaded twice. With no embedder (memory off), the signal is recorded as
  `unavailable`; there is no word-overlap fallback.
- **The signals are read before the budget.** A re-ask or a correction that the router gives a
  value budget is still `differently` or `check`.
- **The thread clears only when the router says the topic moved,** and is otherwise capped at the
  last 12 exchanges. The router's follow-up flag decides only `first` vs `continue`.
  - An embedding floor for "still the same thread" was tried and was not separable. Off-topic
    probes ("내일 날씨 어때?", "환율 얼마야?") reached 0.62 against a thread; real sub-questions
    ("가중치가 뭐야?") went down to 0.42.
  - Keeping the thread is cheap because both signals are narrow.
- **Each trace also records** the best similarity to an earlier question, and whether the signal
  was available.

**Replay with v2** (this threshold was chosen on these same runs):
- Pilot 4: the 7 written expectations still hold.
- Pilot 5: 4 of the 12 hand-labelled turns are caught — two greenhouse re-asks, the dividend
  re-ask, and "그거 답이 아니잖아" as a correction.

**How pilot 6 is scored:** the transcripts are labelled by hand first (`reask`, `complaint`,
`challenge`), before any recorded move is looked at. Then
`python -m evals.turn_move_shadow --score pilot6-current LABELS.json` reports what was caught,
what was missed, and false alarms.

## Pilot 6: v2 live, untuned (2026-10-02, learner seed 2)

**The recording works.** A move was recorded on all 57 turns, and the replay agrees on every one.
The labels (`runtime/evals/sl/pilot6-current/labels.json`) were written from the transcripts before
any move was looked at.

| kind | labelled | caught | false alarms (of the 40 unlabelled follow-ups) |
|---|---|---|---|
| re-ask (`differently`) | 12 | **9** | 2 |
| challenge (`check`) | 3 | 0 | 3 |
| complaint | 2 | 0 | — (no signal for these) |

- **`differently` works on fresh data:** 9 of 11 flags right, and 9 of 12 re-asks caught (pilot 5
  with v1: 0 of 12).
  - **The two false alarms** are short definition questions on the same subject, both just over
    the line: "배당률이 뭐야?" after "주식 배당금이 뭐야?" (0.812), and "does that mean the effect is
    big?" after "what does statistically significant mean?" (0.811).
  - **The three misses** were asked again inside a compound question, or with a new element:
    "학습 데이터가 뭐야? + 인터넷에서 매번 찾아오는 거 아니야?", or "if you just zip the file,
    doesn't that mess up…".
- **`check` does not work:** 0 of 3 flags right, 0 of 3 challenges caught.
  - The router labels checks of their own understanding as corrections: "so it's not the Earth's
    shadow…, right?" and "so it's about being sure it's real…, right?".
  - The number rule fired on a new how-question ("if the floats are tiny like 0.001 and the integers
    are -128 to 127…").
  - The real challenges were all semantic: "0.325 is a float, how does that fit in an integer?",
    "how do you magically get the decimals back?", "0 times anything is still 0".
- **Challenges and complaints both need her last reply read against what they said.** That is a
  model-level read: a router field or a separate call.

**Seen in passing:** `completion_retry` replaced a correct closing answer ("You're right—it's
about verifying reality…") with an invented "The calculation is complete. The requested amounts
are: 12, 15, and 20." It treated a conversational check as an unfinished calculation.

## What it does not do

- **It changes nothing.** The move is decided and recorded, never acted on. The history decision,
  the goal and the repeat check are as before.
- **No understanding state yet,** meaning which ideas the person holds. That is the record's
  next layer, and it needs the move owner working first.
- **It does not catch a wrong fact said for the first time.** It only stops the same wrong answer
  being given again, and gives a challenge somewhere to go.

## Next

1. **Live shadow.** Moves are recorded on every real turn from now on. Read them with
   `python -m evals.turn_move_shadow --live runtime/turn_trace`, and, on the next pilot, from that
   run's traces.
2. **Then behind a flag:** the move's line replaces the goal on `differently` and `check` turns,
   and the thread keeps the history on sub-questions. Then an A/B pilot against current production.
