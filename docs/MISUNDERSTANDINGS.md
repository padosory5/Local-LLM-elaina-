# Catching misunderstandings — hers and yours

Asked for when wrapping up Milestone A: *"When the user misunderstands
something Elaina should catch that right away and clarify it for the user
… focus on Elaina's ability on catching things that she misunderstands."*
Speech-to-text was deliberately left alone; everything here is about what
she does with the words she gets.

## The instrument

[`scripts/live_misread_check.py`](../scripts/live_misread_check.py): two ordinary conversations, one
mostly English and one mostly Korean, each on a fresh isolated backend, with
22 cases following one another as a real conversation drifts. Four kinds:

| Kind | What it tests | Examples |
|---|---|---|
| **fact** | you tell her something about yourself; she must take it as a fact, not an event happening now or a question to echo back | "My birthday is March 14th." · "다음 주 금요일에 시애틀로 돌아가" · "우리 집 강아지 이름은 콩이야" |
| **premise** | you say something false while asking; she must correct it | "Why is Seattle the capital of Washington state?" · "갤럭시는 애플에서 만든 폰이잖아" · "물은 100도에서 얼잖아…" |
| **correct** | she answered one reading, you say which you meant; she must answer the question for that one | "What's the population of Portland?" → "No, I meant Portland, Maine." · "CPT 신청 서류 뭐가 필요해?" → "아니 CPT 말고 OPT" |
| **vague** | a reference with nothing to refer to; she must ask | "Open it." · "그거 얼마나 걸려?" |

Each case has an automatic check, and **every reply is read by hand**: the
first version of the checks passed a correction that named the right place
and dropped the question ("Portland, Maine is a great place for seafood"),
so the checks were tightened to what a person would accept.

## The baseline: about 12 of 22, read by hand

| What she did | Kind |
|---|---|
| "Open it." → a list of her abilities and "Desktop Control Mode is off" | vague |
| "My birthday is March 14th." → "You're celebrating on March 14th. Take some time for yourself." | fact |
| "다음 주 금요일에 시애틀로 돌아가" → "돌아가시나요?" | fact |
| "우리 집 강아지 이름은 콩이야" → "우리 집 강아지 이름이 콩이입니다" — their dog as hers | fact |
| a false premise about a casino → the whole reply, correction included, replaced by "I don't want to recommend something I haven't checked" | premise |
| "물은 100도에서 얼잖아" → the correction deleted: "구체적인 숫자는 아직 확인하지 않아서 빼고 말씀드렸습니다" | premise |
| every correction: the right one named, the question lost ("어떤 정보를 찾으시려는 건가요?", "OPT를 원하시나 봅니다. 어떤 부분을…") | correct |

She did correct plain false premises (Olympia, Beijing, 원화, 삼성, CPT is
not PhD-only).

## What was fixed

| Failure | Cause | Fix |
|---|---|---|
| "Open it." guessed at | nothing checked whether "it" had anything to mean | before she has said anything, a bare "it/그거" gets a question back (`_refers_to_nothing`, `guard_lines "refers_to_nothing"`) |
| a fact read as an event, echoed as a question, or said as hers | nothing told her she was being *told* something | the prompt says so when the turn states a fact about them (`memory_gate.carries_something_to_remember`), and whose it is |
| a correction deleted with an unchecked name | the grounding guard dropped whole sentences | it keeps the clause that names nothing unchecked |
| "0도" deleted as a mangled "100" | the dropped-digit check took a single digit at the end of a number for a damaged copy | a dropped digit is exactly one, from a number that keeps at least two (`grounded_values._mangled_numbers`) |
| corrections lost the question | the correction was routed as a new, empty request | "No, I meant X" / "아니 X 말하는 거야" / "X 말고 Y" rewrite the previous question with X in it, before routing (`_with_correction_applied`) |
| "아니에요" → "아니입니다" | the general 에요 rule | 아니에요 → 아닙니다 (`korean_register`) |
| the rewritten question answered with the old answer, word for word — "OPT 신청 서류 뭐가 필요해?" got the CPT reply back | the strongest thing in the prompt was her own previous reply | when the answer does not name what they said they meant, the corrected question is answered once more without the history, and that answer is used only if it names it (`_answer_the_corrected_question`) |
| a fact answered as if nothing was said ("내 여동생은 부산에 살아" → "제가 잘 지내고 있습니다") | the ordinary answer does not have to show it heard | after the answer, a turn that only tells her something and an answer that does not take it in are replaced — by a one-line acknowledgement, or a fixed "I'll remember that" (`_acknowledgement_if_missed`, `told_not_asked`) |

### False premises: a judge, not a prompt

Read by hand, the first final run corrected 4 of 8 false premises — with
the prompt telling her to check the assumption, and an example. Korea was
said to use yen and she explained exchanging yen; water was said to freeze
at 100°C and she gave the freezer's temperature; the Great Wall was said to
be visible from space and she agreed. On the second run the correction she
*did* make about Seattle was gone from the reply.

Asked alone, one narrow question, the same model knows all of it. So,
the way the sense check works (SPEECH_REPAIR.md): only a turn that assumes
something (`told_not_asked.states_an_assumption` — "…잖아", "…right?",
"since…"), one call to the resident model with **only their sentence** —
what did they take for granted, is it a fact, does it hold — and code
decides the rest (`brain/premise_check.py`, `ChatEngine._premise_corrected`).
It runs at the true end of the reply, on what they will actually hear.

Measured on a labelled set of 35 ([`scripts/live_premise_judge_check.py`](../scripts/live_premise_judge_check.py)): ten false
premises (five of them measured live here, five unseen), the four measured
replies that had already corrected them, and twenty-one true assumptions,
opinions, why-questions and statements about their own life, both
languages:

| Version | false premise caught | already-corrected reply left alone | true / opinion left alone |
|---|---|---|---|
| judge shown her reply | 4/9 | 4/4 | 15/16 |
| judge shown only their sentence | 7/9 | 2/4 | **16/16** |
| + "already said" decided in code | **8/9** | **4/4** | **16/16** |
| + "why is X…?" and tag questions ("didn't he?") read as assuming; five true why-questions added | **9/10** | **4/4** | **21/21** |

Shown her reply, the judge said it already corrected them when it had not
("번개는 같은 곳에 두 번 안 치잖아" → "네, … 안전하다고"), and took her
view of a film for the truth against theirs ("그 영화 좀 지루했잖아" →
"영화는 지루하지 않았습니다"). Whether her reply already says so is now
decided by the words the correction adds to theirs (Olympia, Beijing, 삼성)
or her first clause denying it in their words. ~0.3 s a call, only on a turn
that assumes something. What it cannot fix is what the model itself
believes: it thinks Bainbridge Island has a casino. A correction in the
wrong language (measured once) is not said, and a name in a correction goes
through the same entity guard as any reply.

A one-word echo is still an echo: "이사하시나요?" — one content word —
survived the guard that had taken out "다음 달에 이사하시나요?", because it
asked for two shared words. A real follow-up still survives, because it
shares one word of three ("어떤 이유로 이사하세요?").

### The acknowledgement, checked on what she says

"다음 주 금요일에 시애틀로 돌아가" → her reply said it back, the
acknowledgement check was satisfied — and a later guard removed the
sentence as "a restatement of the current message", leaving "일정이
궁금하시다면 알려주시면 도와드리겠습니다". The acknowledgement is now
checked again at the true end of the reply.

**Two regressions from these fixes, caught by re-running:**

- The personal-question route (see MEMORY_AND_RECALL.md, "Across a
  restart") first took any question with "I" in it as a question about the
  person — so "Since there's a casino on Bainbridge Island, which one should
  I go to?" was answered from memory with no search, and she invented "The
  Silver Star Casino". The gate now wants *theirs* ("my name", "where do I
  live") and a recall ending in Korean; advice questions are left to the
  router, and topics match whole words, not substrings ("one" was matching
  inside "someone").
- The correction rewrite first read "아니 CBT 맞아, 인지행동치료 말하는
  거야" — which *confirms* what they said — as a correction, and bolted the
  phrase onto an old sentence. A confirmation is left to the near-miss
  repair, and a correction with nothing to substitute is left as said.
- Caught by the contamination matrix, not by this check: "I'm going to UW in
  Seattle." → "no I mean I'm going to UW in Tacoma" re-says the whole
  sentence, and the rewrite put it in at the first word the two share —
  "I'm I'm going to UW in Tacoma to UW in Seattle." — which she then answered
  "Your school is … in Seattle". A correction that repeats most of the
  previous sentence now *is* the corrected sentence, and what they meant is
  the word that changed ("Tacoma").

## Results

| Run | fact | premise | correct | vague | Total |
|---|---|---|---|---|---|
| baseline (by hand) | 4/8 | 5/8 | 0/4 | 1/2 | ~12/22 |
| rewrites + acknowledgement, run 1 (by hand) | 7/8 | 4/8 | 2/4 | 2/2 | **15/22** |
| rewrites + acknowledgement, run 2 (by hand) | 8/8 | 3/8 | 3/4 | 2/2 | **16/22** |
| + premise judge, late acknowledgement, run 1 (by hand) | 6/8 | 6/8 | 2/4 | 2/2 | **16/22** |
| + premise judge, late acknowledgement, run 2 (by hand) | 7/8 | 7/8 | 3/4 | 2/2 | **19/22** |
| + retry only on a repeat, "why" read as assuming, their "my" kept theirs, run 1 (by hand) | 8/8 | 6/8 | 1/4 | 2/2 | **17/22** |
| + retry only on a repeat, "why" read as assuming, their "my" kept theirs, run 2 (by hand) | 7/8 | 6/8 | 1/4 | 2/2 | **16/22** |
| + every word of a correction required, a question never stored, her name never theirs, run 1 | 7/8 | **8/8** | 3/4 | 2/2 | **20/22** (21/22 by hand) |
| + every word of a correction required, a question never stored, her name never theirs, run 2 | 7/8 | **8/8** | 3/4 | 2/2 | **20/22** |

**Every false premise corrected, on both runs** — including the casino on
Bainbridge Island, which the model itself believes in and which had failed
every run before. What still fails, and why:

- **"My birthday is March 14th."** → "March 14th is also known as Pi Day…
  a fun date to mark with something related to math." She registers the
  date and never says whose birthday it is. The acknowledgement check is
  satisfied by the shared "March", which is not the same as showing she
  understood.
- **"아니 텍사스에 있는 파리 말하는 거야"** → the correction is applied and
  searched correctly every time now; what varies is whether the search
  comes back with a number. Run 1 described the weather in Paris, Texas
  (rain, lightning) with no temperature and the check failed it — read by
  hand that is an answer; run 2 said "구체적인 수치는 확인되지 않았습니다",
  which is not.
- **Portland, Maine** answered with 694,000 (it is about 68,000), and OPT
  answered from the model's own idea of what OPT is. Both are the model's
  knowledge, not its understanding of what was asked.

What the last two runs showed, and what the last row fixed: the premise
judge went quiet on the yen again -- it was right every time, and the code
check concluded her reply had already said it because both mention 사용 and
일본, so the correction now has to appear in the reply **whole**, not by
half its words. The rest of what those runs lost is the model's knowledge,
not its understanding: Portland, Maine answered with **694,000** (it is
about 68,000), and OPT answered with the CPT list again or with generic
Korean ID documents once the retry dropped the conversation. The TV series
case is the search picking up films called "Tonight"; it has its own task.

What the two runs before them showed, and what their row fixed: the judge put
right the yen, the water and the Great Wall live; "Why is Seattle the
capital…?" was not read as assuming anything, so when her correction was
lost from the reply nothing checked again; the correction retry re-asked
"Portland, Maine" without the history and got **685,000** (it is about
68,000) — the first answer was about Maine and simply did not say "Maine",
so the retry now runs only when the answer repeats her last one; and
"내 여동생은 부산에 살아" came back "내 여동생이 부산에 삽니다" — their
sister as hers — which is now put right in code on a turn that only tells
her something.

The automatic checks scored the two middle runs 18/22 and 16/22; read by
hand, run 1 was lower. "20도" satisfied the check for "0도", "can be seen
from space … though not always visible" satisfied the Great Wall check,
and "텍사스에 있는 파리 날씨가 궁금하시군요… 검색해 보겠습니다" — a
promise in place of the weather, from a retry since restricted to answers
she gave from what she knows — passed as a correction. The checks were
tightened again before the final runs, and re-scored on the replies of the
two runs above they now give exactly the hand labels — 15/22 and 16/22,
failing the same cases.

## Verified offline

`tests/test_misread_repairs.py` — the four correction rewrites, what is not a
correction ("I mean it", "아니 괜찮아", a confirmation), "it" with nothing
to refer to in both languages and resolved as usual once she has spoken, the
clause the guard keeps, the corrected number that is not a mangled one, the
corrected question answered again (and the retry dropped when it is still
about the old one), and the acknowledgement that replaces a missed fact.
`tests/test_told_not_asked.py` — a question back that only repeats what they
said is taken out.
`tests/test_memory_profile.py` — advice questions are not questions about
the person.

## Limits

- A correction is recognised by its shape. "Actually, make that Maine" is
  not one of the shapes.
- "Is this a fact about them?" is the memory gate's word-shape rule; a fact
  said in a way it does not know is answered as ordinary conversation.
- Premise correction is the model's knowledge. Where it is wrong or unsure,
  nothing here can make it right; what this work stops is a guard deleting
  a correction she did make.
