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

## Targeted regression before deciding on `deep` (2026-10-03)

`python -m evals.turn_move_regression --run NAME` sends scripted conversations
(`evals/regressions/turn_move_regression.json`) to the real backend in the `move_deep` arm, with no
simulated person, and checks the turn traces. Reports: `runtime/evals/tmr/<name>/REPORT.md`.

**reg1** (12 conversations, 46 turns; the move owned 18).

| on turns the move owned | passed | | on every other turn | passed |
|---|---|---|---|---|
| written by the 27B | 18/18 | | written by the 8B | 28/28 |
| shown the person's words | 17/18 | | built as before | 25/25 |
| the router's reading is not the current message | 18/18 | | | |
| the trace says which was shown | 18/18 | | | |
| not a copy of her earlier answer (< 0.90) | 14/18 | | | |

- The one "their words" miss: an input-repair step drops a leading "no I mean" before the turn
  starts. It is not the router's rewording.
- Hand read of the 18: 13 pass. 4 answer correctly but repeat her earlier answer word for word, on
  yes/no questions repeated word for word. 1 is off-target on a search turn ("숫자들이 왜 이렇게
  많아서").
- Latency without a simulated person: a turn the 27B writes takes 15.6 s (median; slowest 27.3 s)
  against 8.4 s; its drafting call 10.9 s, of which 7.9 s is loading the model.

**Asked again is read before challenged.** In reg1 "아니 그러니까 지금 제일 최신 아이폰 모델이
뭐냐고?" was labelled a correction by the router, marked `check`, and so never reached the 27B.
`Record.decide` now reads the asked-again signal first; the router's label stays in the trace under
`challenged`. Replayed over every recorded run, 2 of 19 `check` turns become `differently`: that one
(0.96 to the question before it) and "…그게 아니라는 거지?" in A/B 2 (0.88), which is a
confirmation of something already asked. The other 17 are at most 0.72 from an earlier question.
With the flag off this changes only what is recorded.

**Re-asked lookups, rerun four times after that change** (reg2–reg5, the two iPhone cases):

| | good | what went wrong |
|---|---|---|
| English | 3 of 4 | reg2: "My previous answer was incorrect. The iPhone 18 series does not exist yet." |
| Korean | 0 of 4 | all four withdraw or contradict her first answer; one also shows `</think>` |

- The router called the Korean re-ask a correction in reg1 only (0 of 4 here), so the new order was
  not exercised live. It is pinned by the unit tests and by replaying reg1's recorded turn.
- **The cause is the second search, not the writer.** Where the re-ask was routed as a search
  (English, 3 runs), the same two queries ran as on the first turn, the same pages came back, and
  the 27B's answer was good; it named the models where the 8B's first answer had lost them to a
  stage. Where it was routed as conversation with live freshness (all four Korean runs, and reg2 in
  English), one query was built from the sentence as spoken ("아니 그러니까 지금 제일 최신 아이폰
  모델이 뭐냐고"), with no dated or official-source query. It returned blog posts about the
  iPhone 16 and 17, where the first turn had Apple's own announcement of the iPhone 18 Pro.
- The 27B went with the new pages and withdrew the first answer. In reg1 the 8B, shown the same
  weak pages on that turn, repeated its first answer. On a lookup, repeating was the better outcome.
- **`</think>` reached the reply** once in the 45 turns the 27B has written (never in over 900 by the 8B).
  The 27B wrote its reasoning, then `</think>`, then the answer. Nothing in the engine removes it.

**Reading.** On re-asked explanations `deep` holds up. On re-asked lookups it is worse than the 8B
whenever the second search is the weak one, and the new order sends one more kind of lookup re-ask
to it. The flag is off.

## The follow-up search, and `</think>` (2026-10-03)

**Why a question asked again was a fresh lookup.** Two things keep a follow-up on what she already
found, and a re-ask gets past both.
- Recall (`_recall_context`) answers from held evidence only when the sentence points back ("which
  one"). A re-ask says the whole question over, so it has no such word.
- The search cache returns the same pages for the same query for five minutes. The router filed
  these re-asks as conversation needing live information and wrote no query, so the query was built
  from the new sentence: one search, unverified, and a cache miss.

Where the router did write the query again (English, 3 runs) both of the first asking's searches
came out of the cache and the answer stood.

**The change** (`ChatEngine._lookup_as_first_asked`). When a turn that is about to search asks
again a question that was searched earlier in the conversation, and the router wrote no query for
it, it gets the lookup that question got: the same query, other-language query and verification.
Inside the cache's five minutes that is the first answer's evidence; later it is a fresh search made
as carefully as the first.
- "Asked again" is the turn-move record's signal (bge-m3, 0.80), read before the search through
  `Record.asked_earlier`. Each exchange now keeps what it searched.
- It does not depend on `responses.turn_move`. Without an embedder nothing changes.
- A turn the router wrote a query for is searched as the router said. The trace records a
  substitution as `lookup_as_first_asked`.

**`</think>`.** `realize.invariants`, which every shown and spoken line already passes through,
keeps what follows a closing reasoning tag. If nothing follows, it keeps the text without the
markup.

**Tests.** `tests/test_reask_lookup.py` (15; the six re-ask tests fail with the rule switched off,
the nine "as before" tests pass either way) and `NoReasoningTests` plus one whole-turn test in
`tests/test_realize.py` (5 of 6 fail without the invariant).

**Live, after the change** (the two iPhone re-ask cases; 2 runs with the flag off, 4 in the
`move_deep` arm; two ordinary follow-up cases with the flag off):

| | before (reg2–reg5) | after |
|---|---|---|
| re-asks that searched something other than the first asking's lookup | 5 of 8 | 0 of 11 |
| re-asks whose evidence lost the iPhone 18 where the first turn's named it | 4 of 7 | 0 of 6 |
| 27B arm: withdrew the first answer on weaker evidence than the first turn had | 4 of 8 | 0 of 8 |
| ordinary follow-ups ("how much does it cost?") searched with the router's own query | | 4 of 4 |
| replies showing reasoning markup | 1 of 16 (the 27B wrote 8) | 0 of 32 (the 27B wrote 8) |

- No draft carried the tag in these runs, so the invariant is shown by its tests, not here.
- The rule fired on 7 re-asks. In 5 the evidence was the first turn's own, from the cache. In 2, one
  of the first asking's two searches had come back empty and came back full this time.
- English, 27B arm: the router wrote a query for all four re-asks, so the rule was not needed. No
  retraction.
- Korean, 27B arm: 1 stood, 1 moved to the iPhone 18 Pro when the re-run brought Apple's page, and
  2 named a different model from the first answer and said they were correcting it. In those two
  the evidence was the same on both turns and named no iPhone 18: the 8B had read it as the 15 Pro
  Max, the 27B read it as the 16 or 17 Pro Max.
- Flag off: both Korean re-asks repeated the first answer; one English re-ask moved from the
  16 Pro to the 18 Pro on the fuller evidence, and one stood.

**What is left is the first search, not the follow-up.** The first Korean turn's evidence named the
iPhone 18 in 3 of 8 runs, against 7 of 8 in English. A Korean lookup searches the sentence as
spoken and one dated official-source query; the router's plain English query is not searched on its
own. When that evidence is thin, two models read it two ways. The move's line also tells the writer
that the first answer did not land, which suits an explanation and not a fact.

The flag is off.

## The first Korean search, and refinements (2026-10-04)

Nothing in production was changed for this. `python -m evals.lookup_evidence`,
`python -m evals.reask_refinement`; reports in `runtime/evals/lookup/s1/REPORT.md` and
`runtime/evals/reask/rf1/REPORT.md`.

**Is the answer missing from the Korean evidence, or there and misread? Mostly missing.**
Over the 26 recorded first turns of the iPhone question (13 Korean, 13 English; the 8B wrote all):

| | first turns | evidence names the iPhone 18 | ...and the draft does | evidence does not | ...and the draft does |
|---|---|---|---|---|---|
| English | 13 | 12 | 11 | 1 | 0 |
| Korean | 13 | 7 | 5 | 6 | 0 |

- **Missing, 6 of 13 Korean turns.** No result names the iPhone 18. The top result is a page that
  says the iPhone 15 Pro Max, with the A17 chip, is the newest, and the 8B repeats it.
- **There but thin, 2 of 13.** One result of five or ten names it; the 8B answered "iPhone 17".
- **There in several results, 5 of 13.** The 8B was right every time.

**Why it is missing: which engine answers.** `tools/web_search.py` calls the `ddgs` library with
its defaults: the region is `us-en` and the engines are tried in a shuffled order. In two probes
from this machine only Yahoo and Yandex returned anything; the other seven returned nothing.
- Yahoo: the Korean sentence brings current Korean pages and Apple's Korean page; the
  official-source query brings Apple's announcement.
- Yandex: the Korean sentence brings the stale 15 Pro Max page; the official-source query brings
  a fan wiki and unrelated Russian pages. A short English keyword query does better there.
- Matching each recorded search to the engine whose pages it holds: on Korean turns, 10 of 10
  Yahoo-like result sets name the iPhone 18 and 0 of 14 Yandex-like ones do. The six Korean turns
  with nothing are the six where both searches look like Yandex.
- A Korean turn searches the sentence as spoken and the official-source query, and both fail on
  Yandex. An English turn also searches the router's keyword query, which survives it.
- The same five queries made again, 5 times each as she searches: 15 of 25 name the iPhone 18.
  With the engines tried in a fixed order, Yahoo first and Yandex last, measured outside
  production: 14 of 15.

**The same evidence, read by each model** (the recorded prompts made whole again, at the
production context size, 2 drafts each; drafts only):

| prompt | evidence names the 18 | 8B names the 18 | 27B names the 18 |
|---|---|---|---|
| Korean turn, as recorded | yes (7 turns) | 10 of 14 | 14 of 14 |
| Korean turn, as recorded | no (6 turns) | 0 of 12 | 0 of 12 |
| Korean question, an English turn's evidence | yes (12) | 22 of 24 | 24 of 24 |
| English turn, as recorded | yes (12) | 20 of 24 | 22 of 24 |

- Without it in the evidence neither model produces it. The 8B states the 15 Pro Max 12 of 12
  times. The 27B says the results do not settle it in 8 of 12 and names an iPhone 17 in the other 4.
  That is the "correction" seen live: two models reading evidence that does not hold the answer.
- The 8B's four misses with the evidence present are the two thin turns.

**Refinements and the re-ask lookup rule.** 18 scripted pairs
(`evals/regressions/reask_lookup_cases.json`), each through the real backend twice, flag off.

| kind | conversations | as expected | the rule fired | the router wrote a query |
|---|---|---|---|---|
| exact re-ask | 4 | 3 | 0 | 4 |
| paraphrased re-ask | 6 | 4 | 3 | 3 |
| added constraint ("...in Korea?") | 8 | 8 | 0 | 8 |
| changed constraint (date, model, place) | 10 | 10 | 0 | 9 |
| ordinary follow-up | 4 | 4 | 0 | 4 |
| unrelated question | 4 | 4 | 0 | 4 |

- **The rule never took a refinement for a re-ask: 0 fires in 26 conversations that were not
  re-asks, and 3 of 3 fires on real ones.**
- The signal alone cannot tell them apart: 6 of the 9 refinement pairs are at or above 0.80 (0.83
  to 0.94), inside the range of the paraphrases (0.94 to 0.98). What kept the rule off was its
  other condition. The router wrote a query for the refinement in 17 of 18, and the other was not
  searched.
- The three re-asks not as expected are the other side of that condition: the router wrote a
  different query for the same question ("latest" for "newest", or with the date added), so the
  rule stood aside and a different search ran. That errs towards a fresh search, never a stale one.
- Not the rule's doing, and not changed: "접히는 걸로는 제일 최신 아이폰 모델이 뭐야?" was read
  by the router as "What is the latest iPhone model?" in both runs, and she answered the first
  question again.
- The turn-move shadow marked 10 of these 18 refinements `differently`. Under `deep` they would
  have gone to the 27B as questions asked again.

**A correction to the offline replays above.** `evals/turn_move_replay.py` called the models
without the context size production sends (6,144 tokens), so those drafts ran at Ollama's 4,096.
By the live token counts, 8 of the 14 prompts in the first replay and 5 of the 12 in the
their-words replay may not have fitted, and would have lost their beginning. Both conclusions were
later confirmed through the real backend (A/B 2, and the regression above), but the replay's own
rates should not be quoted. The tool sends the size now.

The flag is off.

## The engine order: shuffled against Yahoo first (2026-10-04)

`python -m evals.search_order`; reports in `runtime/evals/search_order/so1/REPORT.md`. The order was
switched, and every engine call written down, by an eval-only hook, so which engine a result came
from is measured. Flag off throughout.

**Was the shuffle chosen?** No. `tools/web_search.py` has called the library with its defaults
since its first commit, and nothing in the repo mentions a backend. The shuffle is the library's:
it calls itself a metasearch over "diverse web search services" and notes that each user is rate
limited by each engine. So a fixed order gives up spreading requests across engines. Every search
now asks Yahoo first. In these runs Yahoo answered as often when asked every time (57%) as when
asked now and then (56%).

**The searches alone** (10 per form per order, turn about):

| the iPhone question | shuffled names the 18 | Yahoo first |
|---|---|---|
| Korean sentence | 1 of 10 | 5 of 10 |
| official-source query | 1 of 10 | 4 of 10 |
| English keywords | 10 of 10 | 9 of 10 |
| English sentence, Korean keywords | 19 of 20 | 20 of 20 |
| all five | 31 of 50 | 38 of 50 |

- Result sets led by Yahoo name the iPhone 18 in 36 of 37; those led by Yandex in 33 of 62.
- Over all 174 searches per order, Yahoo led 29% shuffled and 49% Yahoo first.
- Ordinary searches (16 queries, 4 each): results about the query 301 of 315 shuffled, 307 of 320
  Yahoo first; no Russian-titled result in either.

**Whole first turns** (10 conversations per cell; the 8B):

| order | Korean: evidence names the 18 | reply names it | English: evidence | draft | reply | mean search | whole turn, median |
|---|---|---|---|---|---|---|---|
| shuffled | 6 | 6 | 6 | 6 | 5 | 5.6 s | 21.2 s |
| Yahoo first, DuckDuckGo second (eval hook) | 8 | 4 | 9 | 9 | 7 | 5.3 s | 22.5 s |
| the same, as production | 7 | 6 | 8 | 8 | 6 | 6.7 s | 23.0 s |
| Yahoo first, DuckDuckGo last (as production now) | 7 | 2 | 8 | 8 | 3 | 1.7 s | 13.4 s |

- **Yandex is behind every turn with nothing to go on:** all 21 turns whose evidence does not name
  the iPhone 18, in every order, had only Yandex-led searches.
- **The Korean answer follows one search.** It names the iPhone 18 in 17 of 17 Korean turns where
  the official-source search was led by Yahoo, and in 1 of 23 where it was not. The Korean sentence
  search does not carry it: led by Yahoo it names the 18 once among pages about the iPhone 17, and
  the 8B says 17.
- **Yahoo first raises the evidence and not, so far, the Korean answer:** over the three Yahoo-first
  runs, 22 of 30 Korean turns had the 18 in evidence (6 of 10 shuffled) and 12 of 30 replies named
  it (6 of 10). The shuffled ten drew Yahoo for the official-source search 6 times, against 25% for
  that query over all shuffled searches.
- **The ceiling is Yahoo itself.** Asked, it returned results on 57% of calls (50% for the
  official-source query). The misses are a results page the library cannot read, and are
  independent from call to call: both of a turn's searches answered in 28 of 90 turns, one in 46,
  neither in 16, against 29 / 44 / 17 by chance.
- **DuckDuckGo cost more than the order gained.** It returned nothing in 164 calls and took 10
  seconds each time. With it behind Yandex no search took 9 seconds (21 to 29 of 60 before), and
  the whole turn went from 21 to 23 seconds to 13.
- **English replies lose a right answer to a stage.** `named_recommendation` removed the sentence
  naming the phones from 10 of 31 English drafts that had them, in every order. Yahoo's evidence
  makes that draft more often.
- Ordinary English turns: the weather and the price were answered in 5 of 5 in every run.

**The change** (`tools/web_search.py`): `backend=ENGINE_ORDER`, the library's engines with Yahoo
ahead of Yandex and DuckDuckGo last. Two tests in `tests/test_search_switch.py`. Afterwards the
re-ask and follow-up cases passed every check (`off3`, `off4`), and the full suite passes.

The flag is off.

## Three follow-up tests (2026-10-04)

Tests only. Nothing in production was changed for any of them.

**1. The stage that removed right answers** (`python -m evals.named_stage_replay`;
`runtime/evals/named_stage/REPORT.md`). `named_recommendation` removes each sentence carrying a
proper name that is not, word for word, in the evidence or the request. Replayed over every
recorded turn: 1,231 turns, 421 that searched, 37 where the stage changed the reply.

| what it flagged | turns |
|---|---|
| "of October", which is "As of October 4, 2026" with its first word dropped | 23 |
| a name whose every word is in the evidence ("Pro and Pro Max", "GPUs and TPUs") | 14 |
| a name with a word that is nowhere in the evidence | 5 |

- 32 of the 37 are the first two kinds and nothing else. In those the removed sentence was the
  answer, or part of it, and "I couldn't get actual listing names out of that search" was put after.
- A narrower reading (a name does not begin with a connector; a name is found when each of its
  words is) leaves those 32 alone and still acts on the other 5. Swapped in in memory, the 22 test
  modules that touch the grounding guards pass as before: 362 tests.

**2. Asking Yahoo again.** Its empty answers are not chance and not throttling. The library gives
every new connection a random browser on a random system, 39% of them phones (234 of 600), and it
cannot read Yahoo's page for a phone: 0 of 26 connections posing as a phone got results, 34 of 34
posing as a desktop did. So the same connection asked again stays empty (0 of 12), and a new one
usually answers (11 of 12).

Tested as Yahoo alone on up to three new connections, then production's order (eval hook,
`python -m evals.search_order --arms once,retry`; `runtime/evals/search_order/so4/REPORT.md`):

| | production as it is | with the retry |
|---|---|---|
| searches led by Yahoo, whole turns | 35 of 60 | 59 of 60 |
| iPhone searches naming the 18, searches alone | 37 of 50 | 50 of 50 |
| Korean turns: evidence names the 18 / reply names it | 9 / 7 of 10 | 10 / 8 of 10 |
| English turns: evidence / reply | 9 / 8 of 10 | 10 / 10 of 10 |
| ordinary results about the query | 306 of 320 | 312 of 320 |
| mean search, whole turns | 1.75 s | 0.90 s |

- The two Korean misses with the retry: the one search of 60 that drew a phone four times, and a
  turn where the router wrote a whole sentence as its query.
- Over all four runs a Korean reply names the iPhone 18 in 32 of 33 turns where the
  official-source search was led by Yahoo, and in 1 of 27 where it was not.
- It costs requests: 91 Yahoo calls for 60 searches.

**3. What the Korean lookup hangs on** (`python -m evals.lookup_evidence --sets v1`, `--variants v1`;
`runtime/evals/lookup/v1/VARIANTS.md`). The recorded Korean prompt with the evidence each set of
searches would bring, by which engine answered each; the 8B, 2 drafts each.

| the searches | right when | Yahoo leads 57% | Yahoo leads 92% |
|---|---|---|---|
| as now: Korean sentence + official-source | the official-source search is Yahoo's | 57% | 92% |
| Korean sentence + the router's English query | the English search is Yahoo's | 57% | 92% |
| Korean sentence + router's query + official-source | either English search is Yahoo's | 82% | 99% |

- The Korean sentence search never decides it. Led by Yahoo it brings buying guides that list the
  iPhone 18 Pro beside the iPhone 17 and a page saying Apple "currently sells around the iPhone 17
  series"; the 8B answers 17. The English searches bring Apple's announcement.
- Three searches fit the context window with about 200 tokens to spare (5,481 for the prompt, 454
  for the answer, 6,144 in all).

The flag is off.

## The two changes, and the rerun (2026-10-07)

**The narrower name rule** (`grounded_values.names_an_unfound_thing`, which only the
`named_recommendation` stage asks). A name does not begin with a joiner, and a name is found when
each of its words is in the evidence or the request. Tests: `ADateAndAFoundNameAreNotInventionsTests`
in `tests/test_result_grounding.py`. The 22 modules that touch the grounding guards pass: the 362
tests there were, and 5 new.

**Yahoo on a new connection** (`tools/web_search.py`). Yahoo is asked alone, on a new `DDGS` each
time, at most `YAHOO_TRIES` = 3 times, and then the engine order is tried once: never more than
four requests to Yahoo for one search. A timeout is not asked again. Tests: `EngineOrderTests` in
`tests/test_search_switch.py`.

**The rerun** (`python -m evals.search_order --turns so5 --arms noretry,production`;
`runtime/evals/search_order/so5/REPORT.md`). The same first turns, 10 Korean and 10 English iPhone
questions and 5 each of the weather and the price, per arm, turn about, flag off. The baseline arm
is the same production code with its Yahoo-only tries answered "nothing" by the eval hook, at once
and without a request, so its searches are the engine order alone, as before the change. Both arms
have the new name rule.

| | baseline, same day | production now | earlier (so3 and so4 `once`, old rule) |
|---|---|---|---|
| Korean final answer names the iPhone 18 | 5 of 10 | 10 of 10 | 9 of 20 |
| English final answer names it | 10 of 10 | 10 of 10 | 11 of 20 (17 of 20 drafts) |
| official-source search led by Yahoo, iPhone turns | 13 of 20 | 19 of 20 | 18 of 40 |
| ...in the Korean turns | 5 of 10 | 10 of 10 | |
| searches led by Yahoo | 32 of 60 | 56 of 60 | 72 of 120 |
| Yahoo requests per search | 1.00 | 1.62 (most 4) | 1.00 |
| search time: mean / median / slowest | 1.87 / 1.02 / 3.7 s | 1.07 / 0.66 / 4.5 s | 1.72 / 0.93 / 3.5 s |
| whole turn, median | 12.9 s | 12.4 s | 13.1 s |
| the stage changed the reply | 0 of 30 | 0 of 30 | 7 of 60 |

- **No Korean failure is left in this run.** The five in the baseline arm are the five turns whose
  official-source search was led by Yandex; in production's arm it was led by Yahoo in all ten.
- **Yahoo's own rate has not moved:** 56 of 97 connections answered (57%). 39 searches took one
  request, 11 two, 4 three, 6 four. Four searches still ended at Yandex, each after four phones in
  a row; a search that goes that far takes about 3.5 seconds against 0.6.
- **The stage removed no right answer.** It changed none of the 60 replies. These drafts seldom
  held what used to set it off: the old rule would have flagged 1 of the 60. The stronger evidence
  is the earlier run: of its 7 changes, 6 of which removed the iPhone 18, the rule now flags none.
- The weather was answered 5 of 5 in both arms, the price 4 of 5 and 5 of 5.

The full suite passes. The flag is off.

### What the rerun shows, and what it does not

**The Korean 10 of 10 is the result of this one experiment: one question ("what is the newest
iPhone model"), ten turns per arm, one day, one network. It is not a reliability figure for Korean
lookups.** What it supports is narrower:

- the retry raised how often Yahoo led a search, from 32 of 60 to 56 of 60, on the same day and the
  same code otherwise;
- for this question the Korean answer has followed the official-source search in every run so far
  (right in 32 of 33 turns where Yahoo led it, 1 of 27 where it did not), so more Yahoo meant more
  right answers here;
- nothing got slower or less relevant in the ordinary searches measured.

It does not show that other Korean questions behave the same way, that Yahoo's pages will stay
readable to the library, or that 3% of searches ending at Yandex is the rate to expect: at 57% per
connection, four phones in a row is about 3 searches in 100, and this run had 4 in 60.

### Review before commit (2026-10-07)

- **The bound.** Against a stand-in for the library, in every combination of what each call can do
  (2,343): never more than `YAHOO_TRIES` = 3 calls to Yahoo alone and one for the engines after
  it, never a connection used twice, nothing but the library's own failures caught.
- **Timeouts: one thing found and fixed.** With Yahoo made unreachable, the library reported the
  timeout as "No results found." and not as a timeout: its own wait for an engine is exactly as long
  as the engine's wait for the network, and ran out first. Read by the exception alone, the tool
  asked Yahoo three times over. It now also reads the clock: an empty answer that used up the
  timeout is a timeout. Measured against the real library at a 2-second timeout: one call to Yahoo
  alone and then the engine order, 12.1 seconds, where it had been three and 16.1.
- **The second wait, fixed at the user's request.** The engine order contains Yahoo, so after a
  timeout a search waited for it again: twice, where before the retry it waited once. After a
  timeout the engines that follow are now the order without Yahoo. Measured against the real
  library with only Yahoo made unreachable, at a 2-second timeout: Yahoo asked once and 4.5
  seconds for the search, where it had been asked twice and 6.4. The answer still came, from
  the engines after it.
- **The other engines.** They are reached only when Yahoo has not answered: 6 of 60 searches in the
  rerun. Requests fell from 5.3 a search to 2.1 (Yahoo 60 to 97; every other engine down). One
  thing is given up: Wikipedia and Grokipedia were asked first on every search and are now asked
  only in the fallback. They had put a result into 2 of 114 result sets, and 0 of 60 on the day.
- **The name rule only narrows.** Over every recorded reply, as drafted and as said, against its
  own evidence, another turn's and none (8,100 checks), it flags a subset of what the rule before
  it flagged, in the same order. The names it no longer flags are "of October", "Pro and Pro Max",
  "Pro Max", "Apple's Pro" and "GPUs and TPUs", and no others.
- **What the narrowing costs.** A name the search did not find is now let through if each of its
  words happens to be somewhere in the evidence. None of the 39 recorded changes was of that kind.

## The "sources disagree" line (2026-10-07)

A later stage adding a false statement to a right answer. `python -m evals.disagreement_replay`;
`runtime/evals/disagreement/REPORT.md` and `d1/LIVE.md`.

**What was failing.** The `grounded_values` stage ends a reply with "Sources disagree on that one --
another says X" when `attribute_values.conflicting_claims` finds two values for something the reply
states. Over every recorded turn that searched (541), it had said so 33 times, never of a real
disagreement: 30 of them on 40 answers to "How much does the iPhone 18 Pro cost?". In 28 the value
named was in no source ("1GB", "2GB", "256TB", "12TB").

**Why.** Two readings in the check, both reproduced offline with no model:
- any two different numbers of one kind anywhere in the evidence counted as two accounts of one
  thing, so a page listing 256GB, 512GB, 1TB and 2TB disagreed with itself;
- the other value was written by putting its number into her wording, so a page's 2TB became
  "another says 2GB".

**The change.** A disagreement needs the same unit, and values one source lists together are
options. Tests: `OptionsAndOtherUnitsAreNotADisagreementTests` in `tests/test_attribute_values.py`
and `SaidOfADisagreementOnlyTests` in `tests/test_disclaimer_said_once.py`.

**Replay** (the stage itself, on each recorded turn's reply and evidence; with the rule as it was it
gives back the recorded reply in 541 of 541):

| | rule as it was | rule now |
|---|---|---|
| replies given the line | 33 | 1 |
| ...naming a value in no source | 28 | 0 |
| ...naming a value some source states | 5 | 1 |

- 32 replies change, and at this stage the only difference in each is the line.
- The one left is not a disagreement either: one page mentions a phone's "10MP 3x zoom camera" and
  another its "50 MP ultra wide lens". Two things of one kind, named by two sources.
- The tests that pin real disagreements passed with the rule swapped in before it was applied (30;
  367 across the grounding modules). 165Hz against 180Hz from two sources is still said.
- Given up: a disagreement stated in two units (1TB against 512GB), and one where a single source
  gives both values. Neither is in the recorded turns.

**Live** (the same question, 10 turns per rule, turn about, flag off, the rule swapped by an eval
hook): the line on 10 of 10 replies with the rule as it was and 0 of 10 with the candidate; a price
stated in 10 of 10 both ways.

**What else changes, and was accepted.** The line went on the end of the reply. `redundant_permission`,
which runs later and takes a closing offer off the end, could not reach an offer the line had been
put after. With the line gone it removed "Let me know if you'd like help finding deals or comparing
storage options." from 8 of the 10 live replies, and would from about 9 of the 32 recorded ones.

## The completion retry (2026-10-07)

A stage replacing a right first draft with an invented one. `python -m evals.completion_retry_replay`
(`--replay`, `--router`, `--tests`, `--live NAME`); `runtime/evals/completion_retry/REPORT.md`,
`l1/LIVE.md` and `l2/LIVE.md`.

**What was failing.** `completion_retry` asks the model again when a value question came back
without its value, and keeps the second draft if it holds a number the first did not. Over every
recorded turn (1,397) it had asked 20 times and kept 15 second drafts, and not one of the 20 was a
requested number missing and then supplied.
- Nine were price answers that had stated the price and ended on an offer. The second draft added
  amounts no source gave in six; `grounded_values` then took them out, with the verified $1,199
  once, and left "couldn't confirm the specific number" behind a verified price four times.
- Eleven were turns that asked for no number: "So it's just about the chance of it being a fluke,
  not how much it actually helps, right?" Three were answered "The total is $120. That includes $80
  for the item and $40 for shipping" or the like, and three had figures added to an explanation.

**Why.** The stage was built for the calculation route. On 2026-09-23 `asks_for_a_value` switched it
on for any turn with "how much / how many / 얼마 / 몇 ..." in it (19 of the 20). The rule looked for
a closing question before it looked for a value; the retry says "Perform the calculation now"; and a
second draft is kept only if it has a new number, which an invented one always has.

**The change.** `AnswerCompletionGuard.first_draft_needs_retry`, asked at the one place the first
draft is judged. The calculation route is exactly as it was. A turn that is only worded like a value
question is not sent back when the router read it as `explain` or `example`, nor for ending on a
question once it has stated a number (`needs_retry(closing_question_is_fine=True)`). The other three
callers of `needs_retry` are untouched. Tests: `tests/test_completion_retry.py` (15; the four that
pin the change fail under the old decision, the rest pass under both).

**Replay** (the decision re-run on each recorded first draft; with the rule as it was it asks on
exactly the 20 turns the traces recorded, 1,397 of 1,397):

| | rule as it was | rule now |
|---|---|---|
| second draft asked for | 20 | 2 |
| ...and kept on record | 15 | 0 |
| value-less drafts on turns not read as explain/example, still asked | 2 of 2 | 2 of 2 |

- The two still asked are a misrouted calculation and a `task_action` turn; neither second draft was
  kept.
- Production decides as the candidate that was tested on 1,397 of 1,397 recorded turns, 160 of 160
  live turns and 262 of 262 visits to the call site in the suite. The difference between them (the
  candidate let a calculation that states its result end on a question) is in none of those.
- The suite passed with the candidate at the call site before it was applied, and does not exercise
  the difference: the two rules agreed on all 262 visits.

**The router** (the risk in reading `answer_shape`: a real value question read as `explain` loses
the retry). The engine's own router on the 8B read 20 real value questions asked alone (five runs
each) and 10 asked in the middle of five recorded explanation threads each as `state` in 150 of 150.
Eleven of the first 20 and all of the second 10 were written for the check.

**Live** (flag off, speech off, a fresh backend per conversation, the rule swapped by an eval hook
and the two rules turn about; `l1`, 66 conversations, 160 turns):

| | rule as it was | candidate |
|---|---|---|
| invented calculation shown on the last turn of three recorded learner threads, twice each | 5 of 6 | 0 of 6 |
| "How much does the iPhone 18 Pro cost?": $1,199 shown | 20 of 20 | 20 of 20 |
| "How much does it cost?" as a follow-up: a verified price shown | 5 of 5 | 5 of 5 |
| knowledge questions of `scripts/live_basics_check.py` right | 20 of 22 | 20 of 22 |

- The price question never met the closing-question half: 0 of 40 first drafts ended on a question.
  The follow-up met it on 3 of 5 in each arm. With the candidate the length rewrite ran on those
  three, kept both prices and dropped the closing question.
- On production with nothing swapped (`l2`, 9 conversations): no second draft asked for in 35 turns,
  a price on 5 of 5 follow-ups, no invented calculation on 3 of 3 threads.

**What it does not show.** The length rewrite on a kept first draft rests on five live turns with
near-identical drafts. The case the stage exists for never came up live, in either arm. And the
knowledge miss is one question in every run, "물은 몇 도에서 끓어?" answered with the freezing
point: the router wrote the previous question as its request, which is a separate fault.

**The same wording match still reaches three other places.** `result_restored` puts back a sentence
`grounded_values` took out as unverified (4 recorded). Turns read as `explain` get the 45-word value
budget. And the length rewrite's output is kept only if it holds a new number, so on those turns it
never is (4 of 4 live).

## The last check and the grounding stage (2026-10-08)

One hard stage undoing another. `python -m evals.restoration_replay` (`--replay`, `--tests`);
`runtime/evals/restoration/REPORT.md`.

**What was failing.** `result_restored` is the last check on a value question: when the finished
reply states no number the draft worked out, the draft's result sentence replaces the reply. It was
built for a result a rewrite dropped. Over every recorded turn (1,592) it had put a sentence back 9
times, and 4 of those were sentences `grounded_values` had just taken out as unverified, with the
"couldn't confirm the specific number" line replaced along with everything else:

| Turn | Put back |
|---|---|
| `reask/rf1` "How much does it cost?" | "prices starting at $1,099 for the 128GB version and going up to $1,499 for the 1TB model" (wrong) |
| `tmr/off3`, `tmr/off4` "How much does it cost?" | "$1,199 and $1,299" (the second in no source) |
| a learner's dividend question | a worked example, in place of the explanation |

**Why.** It compares the finished reply with the draft and cannot tell a number lost from one
removed on purpose. The same thing had happened with the Celsius conversion, which was moved after
it. The 2026-09-23 wording match armed it for looked-up values as well as computed ones.

**The change.** The two grounding steps keep the numbers they took out of the reply
(`AnswerCompletionGuard.numbers_taken_out`), and `the_result_sentence` does not hand back a sentence
holding one of them. Tests: `tests/test_result_restored.py` (7; the two that pin the change fail with
the check as it was).

**Replay** (the check re-run on each recorded turn; as it was, it puts back the recorded sentence on
exactly the 9 recorded turns, 1,592 of 1,592):

| | as it was | now |
|---|---|---|
| a sentence put back | 9 | 5 |
| ...holding a number the grounding stage took out | 4 | 0 |

- No other turn changes. Production decides as the rule that was tested on 1,592 of 1,592.
- The 5 it keeps are the job it was built for, and none involves grounding: asked "What is the
  freezing point of water in Fahrenheit?", `final_check` removed "The freezing point of water is 32
  degrees Fahrenheit." as a restatement of the question on 5 runs of 5, and the check put it back.
  That removal is its own fault and is only hidden by this check.
- The suite passed with the rule swapped in before it was applied, and did not exercise the
  difference (the grounding stage had taken a number out on none of its visits to the check).

**The cost, accepted before the change.** `grounded_values` drops whole sentences. In "The iPhone 18
Pro and Pro Max start at $1,199 and $1,299 respectively" the $1,199 is in the evidence and goes with
the $1,299 that is not; on `off3` and `off4` the restoration was the only reason it was shown, and
those replies now end "couldn't confirm the specific number". Across the 541 recorded searched turns
the grounding stage dropped a verified amount of money with an unverified neighbour on 5.

## An answer given to the last question: a rule tested and not applied (2026-10-08)

`python -m evals.last_answer_repair`; `runtime/evals/last_answer/REPORT.md`.

**What fails.** "물은 몇 도에서 끓어?" straight after "What is the freezing point of water in
Fahrenheit?" is shown as "물의 동결점은 섭씨 0도입니다", on 5 live runs of 5. The router writes the
previous question as this turn's request ("freezing point of water in Fahrenheit"). The draft answers
that; `not_her_last_answer` notices and asks again with no history, but asks the router's request, by
design, and is given the freezing point again (kept 0 of 5); the unit stage then says it in Celsius.

**The rule tested.** When the router's request is the request it wrote for the turn before, the
repair asks the person's words instead. On the real 8B, from the recorded prompts:

| | result |
|---|---|
| the 5 recorded turns, asked in her words | "물은 일반적으로 100°C에서 끓습니다", 5 of 5, and kept by the stage's own test |
| 5 follow-ups that need the router's reading | the rule leaves all 5 alone; asked bare, all 5 came back wrong |
| 9 new questions after a related one | the router wrote the previous request for 1, this pair |
| 6 re-asks in other words | the rule's test met on 1; its bare words came back as nonsense the stage would keep |

**Why it was not applied.** A re-ask rightly has the request of the turn before, so the rule's test
is met on it too, and "다시 한 번, 몇 미터야?" asked with no history is exactly what the router's
reading is there to prevent. Nothing deterministic was found that separates a new question misread
as the last one from the last one asked again. The fault is one reading by the router; it stays as a
failing case in `scripts/live_basics_check.py`.

- The follow-ups, new questions and re-asks were written for the check.
- The trace keeps a system prompt by hash after its first use in a turn; replayed without it the
  model answers with no persona. Replayed whole, the recorded call gave the same answer in other
  words, so the control was read by what it said.

## The final check's restatement strip (2026-10-08)

A soft stage taking the answer for an echo. `python -m evals.echo_strip_replay` (`--replay`,
`--tests`, `--router`); `runtime/evals/echo_strip/REPORT.md`.

**What was failing.** `final_check` takes a restatement of the person's message off the front of a
reply. One reading is a first sentence at least 80% character-alike to what they said: their own
sentence with the ending changed ("회사에서 하루 종일 회의만 했어" -> "...했네요"). Over the 1,573
recorded turns the check ran on it had fired 12 times, always through that reading and never on
something told. Every one was a question, and what went was the answer:
- 6: "What is the freezing point of water in Fahrenheit?" lost "The freezing point of water is 32
  degrees Fahrenheit." The last check on a value question put it back, without the second sentence.
- 6: a yes/no verdict on an understanding check ("Okay, so it doesn't mean it's a big difference,
  right?" lost "No, it doesn't mean it's a big difference."), and nothing put it back.
- 27 more answers sat just under the line (0.70 to 0.80): "Jane Austen wrote Pride and Prejudice."
  at 0.77.

**Why.** The reading compares characters and does not know whether they asked or told. The only new
word in a verdict is "yes" or "no", which the guard counts as filler, so nothing about the sentence
itself separates the two.

**The change.** The reading applies only when they told her something: the style layer's act is
`react` and what they said does not end in a question mark (`_final_response_check` passes
`re_ended` to `strip_current_turn_echo`). An exact repeat and a restatement before a dash are
stripped as before. Tests: `tests/test_echo_after_a_question.py` (8; the seven that pin the change
fail with the reading always on). `tests/test_result_restored.py` no longer uses this strip as its
example of a stage losing an answer.

**Replay** (the strip re-run on each recorded reply; as it was, it strips exactly the 12 recorded,
1,573 of 1,573):

| | as it was | only when reacting | not after a question mark | both (applied) |
|---|---|---|---|---|
| replies stripped | 12 | 0 | 0 | 0 |

**Why both halves** (the engine's router on the 8B, 20 things told and 12 things asked, each read
alone and after one exchange; 20 of the 32 lines are the project's own, 12 written for the check):

| a first sentence that mirrors what was said | as it was | only when reacting | not after a question mark | both |
|---|---|---|---|---|
| told her something: still stripped | 40 of 40 | 38 of 40 | 40 of 40 | 38 of 40 |
| asked, with its question mark: left alone | 0 of 24 | 20 of 24 | 24 of 24 | 24 of 24 |
| asked, without it: left alone | 0 of 24 | 18 of 24 | 0 of 24 | 18 of 24 |

- Out of their thread, "..., right?" check questions were read as statements; in their recorded
  threads the same questions were read as answers 12 of 12 times.
- Two of the six questions spoken to the real app arrived without a question mark.
- The two statements no longer stripped are a correction ("아니 CPT 말고 OPT"), which the router does
  not file as a plain statement. Accepted: a restated opener, against a lost answer.

**What it does not show.** The record holds no strip of a real echo (10 turns read as `react`, none
echoed), so what is kept for statements is argued from the rule and the router check. No live run
was made for this one: it is a soft stage, and nothing about a model call changed.

**Consequence.** `result_restored` no longer fires on the freezing-point question, because the
answer is no longer taken out.

## The fresh pilot, and what it pointed at (2026-10-08)

`pilot7-current-s1`/`-s2` on the committed code (`eb3c159`, Ollama 0.40.0), against the 2026-10-03 off
arm (`ab2-off-s1`/`-s2`, Ollama 0.35.1); `runtime/evals/sl/pilot7_report.md`.

| | fresh | 2026-10-03 |
|---|---|---|
| verified understanding (the learner says so and the judge finds every idea) | 8 of 18 | 11 of 18 |
| replies pitched at this person | 51 of 100 | 52 of 105 |
| changed approach after confusion | 40 of 70 | 41 of 69 |
| replies that sound natural | 47 of 100 | 48 of 105 |
| completion retry asked / restatement strips | 0 / 0 | 3 / 1 |

Five stage fixes removed what they were aimed at and moved nothing the judge measures. The 8 against
11 is inside the spread of the old run's own two seeds. Verdict-first on understanding checks, by
hand: about 12 of 29, as in pilot 3. What the transcripts show: the 8B's own wrong or vague
explanations and the same explanation again (30 of 70 "repeated"); the thread dropped on 16 of 91
follow-ups; `grounded_values` deleting worked examples on 4 replies; "...거입니다" endings from the
voice stage on 4 of 55 Korean replies. `deep` as built reaches 11 of 100 replies and 6 of the 30
"repeated".

## A follow-up keeps the conversation it follows (2026-10-08)

**What was failing.** `_context_for_turn` starts a turn clean when the subject it is filed under
shares no word with the subject the conversation was filed under, and a clean start retires the
whole conversation for the turns after it too (`ConversationManager.start_new_subject`). A question
about a word she has just used is filed under a subject of its own: "적외선? 그거 뭐야?" was answered
with microwave ovens, and the next turn explained how microwave ovens warm the Earth.

**How often.** Of 1,135 recorded turns after the first of their conversation, the label comparison
started 225 clean. On 167 the router had called the turn a follow-up and not said the topic moved,
and every one was inside a single explanation. Of the other 58, 47 were the next unrelated question
("Who wrote Pride and Prejudice?" after "What's 15% of 80?"), which is what the rule is for, and 11
were term questions inside a thread that the router did not call follow-ups ("파장이 뭐야?").

**The change.** The label comparison decides only for a turn the router did not call a follow-up.
What the person says ("forget the mouse") and the router saying the topic moved start a turn clean
as before. Tests: `tests/test_followup_keeps_the_thread.py` (7; the four that pin the change fail on
the code as it was). The 11 term questions are not covered.

**The risk side, live** (`scripts/live_contamination_check.py`, 12 cases, a backend each, one run
before and one after): 11 of 12 before, 12 of 12 after, no old subject named in a new question.
"where is my school again?" after a correction went from "You haven't told me where your school is"
to "You're attending UW in Tacoma".

## An explanation's worked example is not a value she looked up (2026-10-08)

**What was failing.** `grounded_values` took a sentence out of 25 recorded replies. The 12 on turns
the router read as `state` were prices. The 13 on turns it read as `explain` were all worked
examples ("주당 1,000원의 배당금이 있고 100주를 보유한다면 100,000원"), 13 of the 1,032 explanation
turns it ran on and not one invented fact among them; ten were then told "couldn't confirm the
specific number". In the pilot it removed the corrected arithmetic ("if the integer is 32 and the
scaling factor is 0.01, the original float value is 0.32") straight after the learner had said the
math did not work.

**Why.** Two readings. With evidence anywhere in the thread, every number not in it is unsourced (7
on turns that searched, 4 on carried evidence). And when the person's own message holds a number,
any other number in the reply reads as theirs come back changed (2).

**The change, chosen by the user over a narrower one.** On a turn the router read as `explain` or
`example`, the numbers are left as written and the log says which
(`GroundedValueGuard.is_an_illustration`, `_enforce_grounded_values(illustrating=...)`). A price put
on a place she has named and a value the person has just disputed are still taken out there. A turn
read as asking for a fact is checked as before. Tests: `tests/test_worked_examples.py` (11; the five
that pin the change fail with every explanation's numbers checked).

**The exposure, accepted.** An invented statistic inside an explanation is no longer removed. The
router read real value questions as `state` in 150 of 150 (the completion-retry check), so a price or
a fact that was asked for keeps the full check.

## A/B 3: current against `deep`, after the two fixes (2026-10-08)

`ab3-off-s1/-s2` and `ab3-deep-s1/-s2`, interleaved, same learners and simulator, on the thread-drop
and worked-example fixes; `runtime/evals/sl/ab3_report.md`. The current arm against pilot 7 (same
arm, same seeds, the code before the two fixes) is their before and after.

| | pilot 7 (before) | current | deep |
|---|---|---|---|
| verified understanding | 8 of 18 | 11 of 18 | 9 of 18 |
| ideas the judge found conveyed | 40 of 52 | 41 of 52 | 37 of 49 |
| replies pitched at this person | 51 of 100 | 47 of 101 | 52 of 99 |
| changed approach after confusion | 40 of 70 | 40 of 66 | 40 of 64 |
| replies that sound natural | 47 of 100 | 45 of 101 | 48 of 99 |
| follow-ups answered without the thread | 13 | 3 | 1 |
| replies `grounded_values` took a sentence out of | 3 | 0 | 1 |
| turns flagged as re-asks / changed approach on them | 11 / 5 | 6 / 2 | 11 / 7 of 9 judged |
| reply seconds, median | 9.5 | 9.1 | 9.4 (19.4 on the 27B's turns) |

- One deep conversation could not be judged (three unreadable answers, and four more with a longer
  limit); its learner said it understood, so deep is 9 or 10 of 18.
- The two fixes did what they were for. The remaining clean starts are turns the router did not call
  follow-ups; the one sentence still removed was on "배당률이 뭐야?", which the router reads as
  `state`.
- `deep`: the 27B wrote all 11 flagged turns. By hand, 8 answer the question in a new way, 2 are her
  previous answer word for word (the 27B's own draft, on a re-ask routed as a search), and 2 are
  good answers with a pipeline line attached. Verified understanding did not rise, here or in A/B 2.
