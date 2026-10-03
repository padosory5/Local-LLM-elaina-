# Acting on "explain differently": A/B protocol

*Written 2026-10-02, before any run. The background is in docs/TURN_MOVE_SHADOW.md. The code is
`brain/turn_move.py` and the hook in `ChatEngine._answer_turn`; the flag is
`responses.turn_move` / `ELAINA_TURN_MOVE`.*

## The question

When she has been asked the same thing again, does letting the move own the turn change what she
says? The alternative is the same answer again. And does it help the person?

## What "on" changes

Only turns the move marks `differently`: a question at least 0.80 similar (bge-m3) to an earlier
question of theirs. On those turns:

- **The goal line is the move's line,** replacing the budget's goal:
  - *English:* "This is the Nth time they have asked this, so the earlier explanation did not
    land. Do not reuse it or reword it: "<her earlier answer>". Start from something they already
    know and take one small step at a time."
  - *Korean:* the same in Korean.
- **The turn gets an explanation's room** (the elaborate ceilings) when the router gave it a
  value budget.
- **It is said as an answer** (the answer act and style), and is not a value answer.

Nothing else changes: history, the model, temperature, the later stages and `check` are as
before. The change is recorded under `context.turn_move_applied`.

## Arms and runs

| arm | `ELAINA_TURN_MOVE` |
|---|---|
| OFF (`--arm current`) | off; the move is recorded only |
| ON (`--arm move`) | on |

Both arms run on the same frozen tree, simulator v2, the 27B learner and the 35B judge, with the
explanation-contract flag off and TTS off. Each arm runs with **learner seeds 1 and 2**: four
runs of nine conversations.

## Measures (fixed now)

**On the turns the move marks `differently`** (recorded in OFF, acted on in ON):

1. **Near-copy rate:** her reply's bge-m3 cosine to her reply to the earlier asking of the same
   question is at least 0.90.
2. **Changed approach:** the judge's `after_confusion` is "changed approach", out of those it
   labels "changed approach" or "repeated".
3. **Re-ask chains:** how many `differently` turns each conversation has. Fewer means the person
   stopped asking again.

**Whole run:** verified understanding, pitched at the person, natural, changed approach after
confusion, and endings the model did not write.

**By hand:** every reply on a `differently` turn in ON. Did it answer the question, take a
different route, and stay correct? Also every ON reply on a turn that was a false alarm (not
really a re-ask).

## Criteria (fixed now)

The flag is worth taking further (more runs, or the user's own use with `on`) only if all hold:

1. **Near-copies:** on `differently` turns, fewer in ON than in OFF, pooled over both seeds.
2. **Changed approach:** on those turns, not lower in ON than in OFF.
3. **No harm by hand:** no ON reply on a `differently` turn ignores the question, contradicts
   itself, or is less correct than the OFF arm's comparable replies. Every false-alarm reply in ON
   still answers what was asked.
4. **No overall loss:** verified understanding no lower in ON by more than one conversation per
   seed.

With nine conversations per run and a handful of `differently` turns per run, this is directional
evidence, not proof. The decision is the user's.

## Result (2026-10-02): the criteria are not met

**Runs.** The scored runs are `ab1-off-s1b`, `ab1-on-s1b`, `ab1-off-s2` and `ab1-on-s2`, all on
one tree (`e8af16f9…`, re-frozen and verified unchanged after each run). The full report is
`runtime/evals/sl/ab1-freeze-b/AB_RESULTS.md`.

**The first seed 1 pair is excluded.** While `ab1-on-s1` was running, another session wrote into
the checkout (the search-switch change). That change does nothing with search enabled, but the two
arms no longer ran identical code, so `ab1-off-s1` and `ab1-on-s1` are kept and not scored.

**On the turns marked `differently`:**

| | OFF | ON |
|---|---|---|
| turns marked | 10 | 7 (all acted on) |
| near-copies of her earlier answer (cosine ≥ 0.90) | 6/10 | **5/7** |
| judge: changed approach | 2/10 | 3/6 |
| most re-asks in one conversation | 5 | 3 |

**Whole runs** (verified understanding per seed, then ranges across the two seeds):

| | OFF | ON |
|---|---|---|
| verified understanding | 4/9, 5/9 | 4/9, 5/9 |
| pitched at the person | 43–48% | 38–39% |
| natural | 46–53% | 36–37% |

Seven acted-on turns out of about 110 replies cannot account for the drops in pitched and natural;
runs differ by about ±10 points on their own.

**Against the criteria:**
1. **Fewer near-copies in ON: not met.** 5 of 7, against 6 of 10 in OFF.
2. **Changed approach not lower: met** on small numbers (3/6 against 2/10).
3. **No harm by hand: not clean.** No acted-on reply was worse than a copy. But two were word-for-
   word copies (ko_llm turn 6, and "32비트 부동소수점이라는 게 뭔데?"), two more were near-copies,
   and one repeated a wrong statement ("the sun doesn't always light the same side").
4. **No overall loss in verified understanding: met.**

**Why it failed.** On every acted-on turn the move's line was in the prompt, quoting her earlier
answer with "do not reuse it or reword it". The 8B's own draft was that answer again. Two of the
three turns checked ran at temperature 0.1; the third, at 0.8, extended the same answer. Telling
the 8B not to repeat a text that sits in its context does not stop it. The copy comes from the
context: the history, and now the quote in the instruction itself.

**What this settles.**
- **The signal is sound.** These were real re-asks: 6 of 10 OFF replies were near-copies, and the
  judge called 8 of 10 "repeated".
- **The remedy is not.** A goal line cannot make this model take a different route.
- **What is left to try** is what the model sees (her failed answer removed from the turn) or which
  model writes the turn. More wording is not.
- **A design flaw, noted for any next version:** the move's line replaced the goal that carries
  the verdict-first rule.

The flag stays off.

## Offline replay of the re-ask turns (protocol, written before running)

`evals/turn_move_replay.py`. The recorded `differently` turns of the four clean runs are
replayed: the drafting call only, with no backend and nothing after the draft.

**How the prompts are rebuilt.** Each prompt comes from its trace: the persona and the final
message are stored whole, and the history is rebuilt from the conversation's earlier turns and
checked against the two history messages the trace keeps. 14 of the 17 turns are replayable. The
other 3 have a final message over the trace's 8,000-character limit, so their middle is cut. The
rebuilt history matches the trace on all 14.

| | what the model is shown | model |
|---|---|---|
| A | production's prompt, flag off | 8B |
| B | the move's line, quoting her earlier answer (what the A/B's ON arm did) | 8B |
| C | the move's line without the quote, history as it was | 8B |
| D | C, with her earlier exchanges on this question removed from the history and from the "verified result" block | 8B |
| E | A | 27B |
| F | B | 27B |
| G | D | 27B |

**Sampling.** Two drafts per turn and variant (seeds 1 and 2), at the temperature and length the
turn really used.

**Measure,** the same as the A/B: a draft is a near-copy when its bge-m3 cosine to one of her
earlier answers to that question is at least 0.90.

**What is compared, fixed now:**
- A against B, to check that the replay reproduces the A/B.
- B against C, to see whether the quote is a source of the copy.
- C against D, to see whether the history is.
- A against E, to see whether the model is.
- G, as both levers together.

**Hand read.** The drafts of the variants with the fewest near-copies are read by hand: is a
different answer also a correct one that answers the question?

## Offline replay: result (2026-10-03)

14 turns × 7 variants × 2 seeds = 196 drafts. Every draft is in
`runtime/evals/sl/ab1-freeze-b/replay/REPLAY.md`.

| | what the model is shown | model | near-copies | turns with no near-copy |
|---|---|---|---|---|
| A | production's prompt | 8B | 16/28 | 4/14 |
| B | the move's line, quoting her answer | 8B | 18/28 | 4/14 |
| C | the move's line, no quote | 8B | 11/28 | 7/14 |
| D | no quote + her earlier exchanges removed | 8B | 5/28 | 10/14 |
| E | production's prompt | 27B | 2/28 | 13/14 |
| F | the move's line, quoting her answer | 27B | 0/28 | 14/14 |
| G | no quote + her earlier exchanges removed | 27B | 0/28 | 14/14 |

**The fixed comparisons:**

| comparison | result | what it shows |
|---|---|---|
| A against B | 16/28 against 18/28 | The replay reproduces the A/B (6/10 against 5/7): the line with the quote does not help the 8B. |
| B against C | 18 → 11 | The quote is a source of the copy. Several B drafts are her quoted answer word for word (cosine 1.00). |
| C against D | 11 → 5 | The history is a source too. |
| A against E | 16/28 → 2/28 | The model is the largest lever by far. Nothing else changed. |

**Hand read** (one reader):

- **8B, D: different, but not better.**
  - The same wrong idea comes back in new words, because it is what the 8B believes: "정보를 잃는
    건 아닙니다", "유사한 문장을 찾아 … 답변을 조립합니다".
  - With the earlier exchange gone, an elliptical re-ask loses its subject: "몇 주 만큼만 받는
    거야?" was answered about share prices.
- **27B, E: different and mostly right, with no move line at all.**
  - It answers the question asked, verdict first: "아니요, 인터넷 전체를 읽는 것이 아닙니다 …";
    "막혀 있는 게 아닙니다. 열은 계속 우주로 빠져나갑니다. 다만 온실가스가 … 담요".
  - It corrects the 8B's earlier mistake: "Not quite. The sun always lights the same half of the
    moon …".
  - Two misses: one off-target dividend answer, and one that lost the subject on the turn
    production had sent with no history.
- **27B, F: the best.**
  - The move's line does what it was written to do. The draft takes on the failed explanation
    directly: "I messed that up. The sun always lights the same half of the moon. What changes is
    how much of that lit half faces Earth."
  - On the question asked five times it gives a new picture: "문을 완전히 닫는 것이 아니라, 열이
    빠져나가는 통로에 불투명한 커튼을 치는 것과 같습니다."
- **27B, G: no copies, but removing the exchanges costs context.**
  - One draft was nonsense: "지하철로 이동해야 합니다. 구체적인 출발지와 도착지가 필요합니다."
  - Two agreed with the person's wrong picture, because her earlier wrong statement was no longer
    there to correct.

**What it settles.** What stops the repetition, and replaces it with a correct different answer, is
the model that writes the re-ask. The prompt and the history are not enough. The 8B copies or
restates what it believes under every variant. The 27B, shown the same prompt, does not; shown the
move's line, it addresses the failed explanation.

**Limits.**
- These are drafts only. The later stages did not run.
- 14 turns, 2 seeds.
- The 27B is also the model that plays the simulated person, so a pilot of "27B writes re-asks"
  would have the learner and the writer share a model. The judge is a third model.
- **Cost, not measured here.** The 27B and the 8B do not fit on the card together, so a re-ask
  turn pays a model swap and the next turn pays one back. The v2 routing run measured DEEP p95 at
  22.4 s.

## A/B 2: the 27B writes the re-asked turn (protocol, written before the runs)

*2026-10-03.* `responses.turn_move: "deep"` / `ELAINA_TURN_MOVE=deep`.

**What `deep` does.** On a turn marked `differently`:
- the move's line is the goal, with an explanation's room, said as an answer (as under `on`);
- that one turn is written by `responses.turn_move_model` (the 27B), with its soft stages off, as
  in every arm the 27B has run in.

The 8B still reads the turn (router, decisions), and the next turn is back on the 8B. This is
variant F of the offline replay, now in the whole pipeline.

**Arms.**

| arm | `ELAINA_TURN_MOVE` |
|---|---|
| OFF (`--arm current`) | off |
| DEEP (`--arm move_deep`) | deep, with the 27B as the model |

One frozen tree, checked after every run. Learner seeds 1 and 2: four runs of nine conversations.
Everything else is as in A/B 1.

**Measures.**
- **The same three on `differently` turns:** near-copy rate (cosine ≥ 0.90 to her earlier answer),
  the judge's "changed approach", and re-ask chains.
- **The whole-run measures.**
- **What the turn costs:** the whole turn, the drafting call, the model load inside it, and the
  turn after.
  - In this setup the 27B also plays the simulated person, so the 8B is already reloaded every
    turn in both arms. The arm-to-arm difference here understates nothing and overstates nothing
    about the load itself, but in her own use only the turns around a re-ask would pay a swap.

**Criteria (fixed now).** Worth taking to the user's own use only if all hold:
1. **Fewer near-copies:** on `differently` turns, DEEP has fewer than OFF, pooled over both seeds.
2. **Changed approach:** higher in DEEP than in OFF on those turns.
3. **No harm by hand:** every DEEP reply on a `differently` turn answers the question asked and is
   no less correct than her earlier answer. Any reply that loses the subject or invents a fact is
   reported.
4. **No overall loss:** verified understanding no lower in DEEP by more than one conversation per
   seed.

The cost is reported as measured. It is the user's to weigh and is not a pass/fail line.

**A caveat that cannot be removed in this setup.** The simulated person is played by the same
27B. A learner may follow its own model's explanations more readily, which would flatter DEEP on
"understood". The judge is a third model, and the near-copy measure does not depend on the learner.

## A/B 2: result (2026-10-03)

**Runs.** `ab2-off-s1`, `ab2-deep-s1`, `ab2-off-s2`, `ab2-deep-s2`, on one tree (`26735d44…`),
verified unchanged after each pair. The full report is `runtime/evals/sl/ab2-freeze/AB2_RESULTS.md`.

**Ollama updated itself mid-attempt.** The first attempt was killed when Ollama updated to 0.35.1
at 00:42; it is set aside in `ab2-freeze/aborted-ollama-update`. All four scored runs started after
the update. Earlier pilots ran on the previous Ollama version, which was not recorded, so a
comparison with them now has one more difference than the code.

**On the turns marked `differently`:**

| | OFF | DEEP |
|---|---|---|
| turns marked | 12 | 17 (all written by the 27B) |
| near-copies of her earlier answer (cosine ≥ 0.90) | 4/12 | **1/17** |
| judge: changed approach | 4/11 | **8/14** |
| conversations with a re-ask | 7/18 | 9/18 |
| most re-asks in one conversation | 3 | 4 |

**Whole runs** (seed 1, seed 2):

| | OFF | DEEP |
|---|---|---|
| verified understanding | 5/9, 6/9 | 6/9, 5/9 |
| pitched at the person | 45%, 54% | 55%, 43% |
| natural | 47%, 44% | 47%, 46% |

**What a 27B turn cost:**

| | OFF | DEEP |
|---|---|---|
| the whole turn, median (slowest) | 10.7 s (20.4) | 18.8 s (30.4) |
| the drafting call | 1.5 s | 10.9 s, of which 7.9 s loading the model |
| reloading the 8B on the turn after | – | 4.2 s median |

In this setup the 8B is reloaded every turn anyway, because the 27B plays the person in between.
In her own use, a re-ask turn would pay about 10 s more and the turn after about 4 s more.

**Against the criteria:**
1. **Fewer near-copies: met.** 1 of 17 against 4 of 12.
2. **Changed approach higher: met.** 8 of 14 against 4 of 11.
3. **Every reply answers the question, by hand: not met as written.** 13 of 17 are different,
   correct and on the question; 4 are not (below).
4. **No overall loss in verified understanding: met.** 11 of 18 conversations in both arms.

**The 13 that worked.** The CS student got the actual formula
(`q = round((x - min) / (max - min) * 255)`). Others opened with the verdict: "Correct, it is not
the Earth's shadow…", "네, 맞습니다. 100주를 갖고 있으면…". Three of the 13 have a blemish:
- one has a wrong detail (ozone loss "warms the stratosphere"; it cools it);
- one is pitched too technically ("전자기파 … 분자의 운동 에너지");
- one correct answer had "구체적인 숫자는 아직 확인하지 않아서 빼고 말씀드렸습니다" appended by
  the grounded-values stage.

**The 4 that missed, and why.** None is the 27B's writing:
- **Two answered the router's rewording** (ko_llm, seed 1, turns 6–7). "그거 그냥 인터넷에서
  실시간으로 검색해온 거잖아?" reached the drafting call as "Are training data just scraped
  internet content?" and "How does the training data for AI models work?". On a knowledge question,
  the prompt's current message is the router's normalized request, not what the person said.
- **Two were routed to web search** (ko_llm, seed 2, turns 5 and 8). One reported the search
  results ("위키백과와 레딧이 주요 출처…"). The other was the one near-copy: its own earlier,
  correct answer word for word, to a question repeated word for word.

**What did not move.**
- Verified understanding was the same in both arms.
- The person did not re-ask less: 17 marked turns against 12, and the longest chain 4 against 3.
  In ko_llm the simulated person repeated one sentence five times whatever the answer was.

**Reading.** `deep` does what the replay said it would. On a re-asked question she no longer
repeats herself, and most of the new answers are right and to the point. It did not change how many
conversations ended in understanding in these runs, and it costs about ten seconds on such a turn.
What still goes wrong on those turns is upstream of the writer: which question the writer is
shown, and web-search routing.

## After A/B 2: the writer is shown the person's words (2026-10-03)

**The change.** On a turn the move owns, the drafting prompt's current message is what the person
said. On a knowledge question or a search it used to be the router's normalized request
(`turn_move.with_their_words`, used in the acting block of `ChatEngine._answer_turn`). The trace
records it as `turn_move_applied.question_shown`. Turns the move does not own are built as before.
With the flag off, nothing changes.

**The affected cases, replayed** (`python -m evals.turn_move_replay --theirs-run`; drafts in
`runtime/evals/sl/ab2-freeze/theirs/THEIRS.md`). In A/B 2, 12 of the 17 turns the 27B wrote had
shown it the router's reading. Each was drafted again by the 27B, twice as recorded and twice
with the person's words.
- 7 are replayed exactly.
- 5 are search turns whose stored prompt is over the trace's limit. Their evidence is shortened,
  identically in both variants.

Hand read, one reader:

| | turns | what happened |
|---|---|---|
| answered the rewording before, answer the person now | 3 | "그거 그냥 인터넷에서 실시간으로 검색해온 거잖아?" (shown as "Are training data just scraped internet content?" and "How does the training data for AI models work?") now gets "아니요, 실시간 검색이 아닙니다…" in both drafts. "내가 물어볼 때마다 인터넷에서 찾아오는 거 아냐?" (shown as "verify where AI data sources come from") now gets "아니요, 매번 인터넷을 검색하지는 않습니다…" where it had got a list of data sources. |
| better | 2 | "Do you just chop off the decimals?" had been dropped by the reading and is now answered: "You do not just chop off the decimals; … you find the closest of the 256 allowed steps." "숫자들이 왜 이렇게 많아서" had been read as "numbers so large"; the recorded drafts open "숫자가 큰 것이 아니라", and the new ones answer about how many there are. |
| the same | 6 | The reading was close to what they said. |
| slightly less direct in one draft of two | 1 | "what's the math for that?": one draft describes the steps without writing the formula. The other draft, and both recorded ones, give it. |

**Against A/B 2's four misses.** The three that came from the rewording are fixed in this replay.
The fourth is unchanged: the 27B repeats its own earlier, correct answer to a question repeated
word for word.

**Limits.** Drafts only; one reader; the search-turn group ran on shortened evidence.

The flag is off.
