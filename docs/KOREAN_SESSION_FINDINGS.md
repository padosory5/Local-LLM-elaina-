# Korean dogfood sessions: findings

Two real sessions driven by the user, in Korean, on `qwen3:8b`:
`runtime/koreanSession.log` (findings 1-6) and `runtime/koreanSession2.log`
(findings 7-19, found in the log and in a turn-for-turn replay of it, then
checked against English and mixed-language sessions on fresh backends).

Every numbered finding is fixed and has a test. Left alone deliberately,
and named where they come up: a style contract that caps a report at three
sentences, and the half of the query-language problem that needs the router
to stop storing subjects in English. Found and *not* fixed -- mostly search
and content quality rather than language -- is listed near the end.

---

## 1. The query kept the subject of a task the conversation had left

**Reported as:** "it kept on adding previous conversations to its search
tool making like 6/25 전쟁 university of washington CS famous".

**What the log says.** A recommendation task opened on the Korean War and
was never retired. The conversation moved to the user's visa paperwork,
then to their university. The query builder leads with the open task's
subject, so:

```
[Query] source: active_task
  text: 6/25 war I-20
[Query] source: active_task
  text: 6/25 war Washington University Seattle
[Tool] Using cached web search for: 6/25 war Washington University Seattle
[Query] source: active_task
  text: 6/25 war Washington University Bill Gates Seattle
```

Two other layers had already noticed, on the same turns, in the same log:

```
[Context] Starting clean: the router says the topic moved
[Context] The held results are about the old subject; not naming them.
```

so this was specifically the query, which asked nobody. Worse, the
contaminated strings became **cache keys**, so a later correctly-built
query for the same subject would have been answered out of them.

**Why the existing test did not catch it.** `context_policy.subjects_agree`
exists and answers almost this question — but its content-word floor is
four characters. "war" is three. A Korean subject is two to four
syllables. So on this session it returned "agree" for every pair, in both
languages: `6/25 war` vs `SEVIS fee`, `6/25 war` vs `한국전쟁`. It is a
*permissive* test, tuned for a permissive question (may stored evidence be
shown), and building a query is the strict direction.

**Fix.** A second, strict test in the same module,
`context_policy.names_a_different_subject`, measured in each script's own
units — Latin words of three or more characters, Hangul runs of two or
more syllables, containment rather than equality so "hotels" matches
"hotel" and "전쟁이" matches "전쟁". `_resolved_search_query` drops the held
task when it disagrees with the turn's goal subject, and says so:

```
[Query] the open task is about '6/25 war' and this turn is about
        'Washington University in Seattle'; not building from it.
```

Conservative in one direction on purpose: either subject being unknown is
*not* a disagreement, so a subjectless follow-up ("which one?", "pull up
some spots") still inherits everything the task established — which is the
entire reason that branch exists.

`tests/test_query_contamination.py`. Reverting the guard makes the two
contamination tests fail with the log's own string and leaves the two
inheritance tests passing.

**Then fixed separately — see finding 6.** The query no longer inherits a
stale subject, but the task itself was still saying `CONTINUE` across it.

---

## 2. A Japanese character in the middle of a Korean reply

**Reported as:** "Elaina was talking and randomly printed a japanese
letter".

**Found at line 256:**

```
안녕하세요. 도움이 되었ようで 다행입니다. 궁금한 점이 또 있으시면 언제든지 말씀해 주십시오.
```

`ようで` is Japanese, glued onto a Korean verb stem. The log above the line
matters: that draft had already been regenerated once and rewritten twice.
This is CJK bleed from a small multilingual model, and no amount of asking
it again was going to fix it.

**Fix.** `TextFilter.without_foreign_script`. By sentence, not by
character: deleting the run leaves "도움이 되었 다행입니다" — a broken verb —
and guessing the connective is generation, not repair. The other two
sentences are said. Applied in `for_voice_response` (the funnel every draft
passes) and again after the voice pass in `chat_engine`, because `_resay`
produces a new sentence that can leak the same way. If nothing survives,
`guard_lines.say("no_response")`.

Kana only. 한자 is real Korean and "한자로 어떻게 써?" is a question she is
allowed to answer; kana has no such reading.

`tests/test_foreign_script.py`.

---

## 3. She said 육이오 전쟁 never happened

**Reported as:** "when I asked about 육이오 전쟁 it replied that it never
happened but after I told her to search it up she figured it out."

**What happened.** 육이오 is the Sino-Korean reading of 6·25, the date the
Korean War began. The router routed it `direct_answer` with
`freshness_required: false` — reasonable for a settled historical question
— so nothing was looked up, and the model's failure to connect the reading
to the event was spoken as a fact about the world. One turn later, told
"한국전쟁이 육이오 전쟁이야", she answered correctly and completely. She had
the fact the whole time.

**The gap this exposed.** Every grounding guard written for Milestone A
checks what a reply asserts is *there*: a price, a spec, a name, a
completed action. None looked at a reply asserting something is **not**.
The two are not equal in cost. An invented price is a value the user can
check; "that never happened" ends the conversation, and it sounds more
certain than the honest answer would have.

**Fix.** `brain/existence_claims.py` and
`ChatEngine._enforce_existence_claims`, the mirror of the value guard.
Without a search or held evidence behind it, a denial of existence is
replaced by `guard_lines.say("unchecked_denial")` — in the language of the
turn:

> 그건 제가 확실히 알지 못합니다. 확인 없이 아니라고 말씀드리기는 어려우니,
> 한번 찾아볼까요?

Two things the patterns deliberately do not catch:

* **A hedge is not a denial.** "제가 아는 한 없습니다", "I'm not aware of any
  such thing" are claims about *her*, they are true, and they are what she
  should say.
* **Disagreement is not an existence claim.** "아니요, 그건 맞지 않습니다"
  and "No, that's not right" correct the user and are fine.

Korean says 없다 constantly and almost none of it denies existence, so the
Korean patterns anchor it on 그런/그러한/라는/역사적으로 or use 존재하지 않다
outright. Measured on 24 sentences — 13 denials, 11 ordinary replies
including "문제 없습니다", "재고가 없습니다" and the correct answer she gave
one turn later — with no misses and no false positives.

`tests/test_existence_claims.py`.

---

## 4. Shallow answers about the university

**Reported as:** "when I asked whats famous about UW it gave me results
that were obvious when I was expecting more like CS is famous because of
its donations from microsoft".

Three causes, two now fixed.

**(a) The query was contaminated.** All three UW searches ran as
`6/25 war Washington University ...`. Fixed above.

**(b) The query named a different university — now fixed.** The user said "미국
시애틀에 위치한 워싱턴 대학교". The router wrote *Washington University in
Seattle*. "University of Washington" is in Seattle; "Washington
University" is in St. Louis. The query names a real university, and not
the one the user goes to.

**(c) The reply is capped.** `[Style] act=report sentences<=3`. "그런
뻔한거 말고" is a request for something *more specific*, and the contract
allows three sentences to deliver it.

### What was done about (b): measured first, then fixed

`scripts/query_language_check.py` reads any session log and reports whether
each search ran in the language its turn was spoken in.

```
$ python scripts/query_language_check.py runtime/koreanSession.log
searches: 5
in a language the turn was not: 4 (80%)
```

The one Korean-language query in the session — `한국전쟁 발발 원인 남침 북침`
— is also the only search whose answer the user did not complain about.

The router writes in English, always, and the query is built from what the
router wrote. For a Korean speaker with a South Korean locale asking a
Korean question, that is wrong twice: the results are for the wrong
audience, and a translated proper noun is a different thing.

**Then the cheap fix was tried, and measured.** The router prompt gained a
rule: write `search_query` in the language the user just spoke, and never
translate a proper noun. Pooled over three runs against `qwen3:8b` on ten
Korean turns:

| | in the turn's language |
|---|---|
| router alone | 9/30 (30%) |
| router + the prompt rule | 9/30 (30%) |

It moved nothing, which is this project's standing result for prompt
wording against a confirmed behaviour. The rule was reverted rather than
left in place looking like a guarantee — it costs prompt tokens on every
turn (Rule 3) and buys nothing.

**With the structural fix below, on the same ten turns, three runs:**

| | in the turn's language |
|---|---|
| router alone | 9/30 (30%) |
| router + `search_language` | **24/30 (80%)** |
| English turns, either way | 6/6 (100%) |

Two-proportion z-test: z = 3.89, p < 0.0001. The six that stayed English
are the same two turns in all three runs — the anaphoric ones the rule
declines by design, below — so the gap between 80% and 100% is exactly the
declared boundary and nothing else.

**The structural fix: use the person's own words.** `brain/search_language.py`.
The turn's own text is the one string guaranteed to be in the right
language *and* to carry the proper nouns in the form the person used —
워싱턴 대학교 rather than a school in St. Louis. Applied last in
`_resolved_search_query`, so it sees whatever the layers above chose.

Only the Korean request grammar comes off the end (`알려줘`, `찾아줘`,
`검색해줘` — the closed class `intent_router` already uses). Nothing else
is touched, and no noun phrase is extracted: Korean needs a morphological
analyser for that, there is not one here, and a question asked whole is a
perfectly good search.

### What it refuses to do, and why that half matters

A turn that leans on the one before it — `그런 뻔한거 말고 뭐가 유명한지
알려줘` — does not carry its subject, and the subject the conversation
holds is stored **in English** by the router. There is nothing in Korean to
put back, so searching those words alone would be worse than the English
query rather than better. Those turns keep the router's wording, and the
log says so:

```
[Query] kept the router's wording: the turn leans on the one before it,
        so its own words are not a query.
```

The test for "leans on the one before it" is a closed grammatical class of
Korean discourse anaphora (`그런`, `그렇구나`, `아까`, `말고`, …), the same
technique as the Korean request endings in `intent_router` and the Korean
deictics in `task_session`. It is used only in the conservative direction —
a match means *leave this turn alone* — so a word missing from it costs
nothing worse than the behaviour that was already there.

`tests/query_language_matrix.json` scores both outcomes in both languages
(13/13), and `scripts/query_language_check.py --matrix` runs it without a
model or a network. The log mode stays, because the matrix can only score
turns someone thought to write down.

**Still open:** carrying a *Korean* name for the subject across turns, so
the anaphoric turns can be searched in Korean too. That needs the subject
stored in the language it was said in, which is a change to the router
contract rather than to the query builder.

Nothing was changed about (c) either. A1 already made the mistake of
tightening a style contract on thin evidence and got worse replies for it.

---

## 5. A filler became the previous question, asked again

Not in the report, found while reading the file. Three turns into the
university conversation:

```
said : 엄
read : Confirm if computer engineering at Washington University in Seattle
       is associated with Bill Gates
said : 그냥 어이가 없어서 한 표현이야
```

One syllable of hesitation came back from the router as a full repeat of
the previous question, and the user spent their next turn explaining that
they had not asked anything.

The router cannot do better than this unaided. A filler names no subject,
so the only thing in the prompt a subjectless turn can attach to is the
previous subject.

**Fix.** `_HESITATION`, a closed class in the same family as the greeting,
acknowledgement and cancellation fast paths: nothing to classify, answered
without a model call, guarded so that anything outstanding — a pending
offer, a consent question, a clarification — makes even a grunt meaningful
and takes the normal path.

Deliberately **not** folded into `_BARE_ACKNOWLEDGEMENT`, because they are
different acts. "ok" agrees with something and is answered with closure;
"um" agrees with nothing and is answered by getting out of the way —
`guard_lines.say("still_listening")`, one clause, in the language of the
turn.

`tests/test_hesitation.py`.

---

## 6. The task itself continued across an unrelated subject

Found while fixing 1, fixed after it, because it changes what the task
holds and not only what the query says.

```
[Task Continuity]
  previous_thing: war
  current_thing:  fee
  follow_up: True
  decision: CONTINUE
  reason: the turn refines or refers to what is open
```

Three lines naming the disagreement and one ignoring it. Nothing compared
them: `about_the_same_thing` reaches its head-noun check only through
`read_constraints`, which finds preference slots in **English request
grammar** and finds almost nothing in a Korean sentence. So every Korean
turn arrived with `named` empty and `follow_up and not topic_shift`
decided everything — which is exactly what the check above it says must
not happen ("a follow-up that names something else still starts a new
problem"). The comment was true of one branch and false of the next.

### The instrument, first

`scripts/task_boundary_report.py` + `tests/boundary_matrix.json`. Eighteen
cases through the real `TaskSessionStore`, one held problem and one turn
each, **both directions in both languages**. The continue half is the half
that matters: splitting too eagerly loses three turns of established
context, so a matrix of only splits would score 100% on a function that
always said "different".

| | before | after |
|---|---|---|
| must continue | 10/10 (100%) | 10/10 (100%) |
| must split | 3/8 (38%) | 8/8 (100%) |
| overall | 13/18 (72%) | **18/18 (100%)** |

Every Korean split failed at baseline. So did the English case whose query
became "studio apartments September 13th 206-221 in South Korea".

### The fix

The `follow_up` shortcut now consults `names_a_different_subject` — the
same strict, bilingual comparison finding 1 added — against the subject
the goal layer resolved. That subject is the *other* place a turn says
what it is about, and it is filled in on turns where no constraint is.

Placed deliberately **after** `revises`: "actually my throat hurts,
something soft" names a different subject and is still about the problem
it revises.

The comparison also had its documented contract enforced rather than
merely stated. When the router names no topic the subject falls back to
the whole utterance, so a side longer than six words is now read as
*unknown* — the safe answer, and the behaviour the code had before.

### The log was lying, so the log changed too

`previous_thing`/`current_thing` are gone; the block now prints
`previous_subject`, `current_subject` and `subjects_differ`, which are the
values that actually decide. A log showing a comparison the code does not
make is worse than one showing nothing — it read as a considered decision
for eleven turns.

`tests/test_boundary_matrix.py`, which also asserts the matrix keeps
measuring both directions in both languages.

---

# Session 2 (`runtime/koreanSession2.log`)

## Reading the log at all

The log was written by `python main.py 2>&1 | Tee-Object`, in Windows
PowerShell 5.1. Python writes UTF-8; PowerShell decoded the pipe as cp949
and saved UTF-16, so every Korean line arrived as mojibake and about half
of it was unrecoverable (508 characters lost after reversing the
decoding). The exact turns were recovered from `runtime/surface.log`, which
the app writes itself in UTF-8, and the session was then **replayed** turn
for turn against a test backend to see the replies cleanly.

To keep the next log readable, run this once in the same PowerShell window
before starting her:

```
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

## 7. Spoken Korean questions were read as remarks

`_REQUEST_SHAPE` decides whether a turn the model wanted to look up was
actually a request; when it says "remark", the lookup is withheld and she
answers from memory. Every English line in it looks at the *front* of the
sentence -- a fronted wh-word, an inverted auxiliary. Korean does neither:
the question word stays where the answer goes, and speech-to-text drops the
question mark. So `몇시간 걸려`, `얼마나 많은 사람들이 봤는데` and `어떤게
있어` all went down the remark path. In the replay, the first came back as
"대략 15시간 이상" and the second as "1997년 방영 시 20.7% 시청률" -- a drama
that aired in 2012, a figure nobody checked.

Fix: `_KOREAN_QUESTION` beside it, and `reads_as_request()` over both --
question words in situ, the verb endings that ask (-나요, -냐, -까, -는지,
-ㄴ가, -니), and the polite request forms. Each question word carries the
exclusions where it is not asking: 언제나 is "always", 왜냐하면 is
"because", 뭐 before 그냥 is a filler, 몇 in a concessive counts nothing.

`scripts/request_shape_check.py`, over `tests/request_shape_matrix.json`:

| | before | after |
|---|---|---|
| Korean questions read as questions | 2/16 | 16/16 |
| Korean remarks kept as remarks | 9/9 | 9/9 |
| English, both directions | 7/7 | 7/7 |

## 8. Rankings were answered from memory

`한국에서 가장 많이 보는 드라마는 뭐야` was classified *stable* knowledge.
Session 2 answered 응답하라 1997; the replay answered 도깨비. Two runs, two
different wrong dramas -- recall noise, not a fact she has -- and the
following turns were spent defending the answer. A ranking is a count
somebody measured, so `_ASKS_FOR_A_RECORD` keeps popularity, sales,
ratings and box-office questions out of local knowledge. Deliberately not
every superlative: "the tallest mountain in the world" still answers
directly. `tests/test_record_questions.py`.

## 9. "Am I right?" was read as "you are wrong"

The dispute detector had a bare `맞아?`. "베인브리지 섬이 였던것 같은데
맞아?" asks her to confirm the person's own guess; it was read as a dispute
of *her* claim, and on the next turn the fact-check prompt ("reconcile the
user's correction...") had her apologise for an earlier statement she never
made. Now only a 맞아 aimed at her words (`그거/이게/진짜 맞아`) is a
dispute, and the correction framing is used only when the turn actually
disputes something.

The opposite gap was closed at the same time: `데이터로 알려줘` -- back it up
with data -- was read as a question about data in general. Asking for the
evidence behind an answer (`근거`, `출처`, `데이터로`, "source?", "how do you
know") now escalates like "are you sure?": she goes and checks the claim.
`tests/test_korean_disputes.py`.

## 10. The query rule rewrote turns that point back

Session 2 ran with the search-language rule from finding 4, and it rewrote
`그 섬에 카지노가 있는지 알려줘` into a search for `그 섬에 카지노가
있는지` -- which island? Korean points back from the middle of a sentence as
easily as from the start, and more often it simply leaves the subject out:
`얼마나 많은 사람들이 봤는데` never names the drama. "Doesn't open with
그런" is not evidence that a Korean turn stands alone.

A turn is now searched on its own words only if nothing in it points back
(anywhere, not just the opening) **and** something in it names a thing --
detected by the particle after it, the closed class that marks a Korean
noun (`워싱턴 대학교는`, `김치찌개를`, `시애틀에서`), plus Latin-script names
and the bare object in front of a request verb (`삼성전자 주가 알려줘`).
`tests/query_language_matrix.json`: 23/23, including all five turns from
session 2 that point back.

## 11. Answering in the language she was spoken to

The rule, from the person directly: when I speak Korean she answers in
Korean, when I speak English she answers in English. The language layer
held any turn under three English words or four Korean syllables in the
previous language, so on a mixed session `서울은?`, `thanks`, `고마워` and
`ok cool` were all answered in the wrong one -- **10/14**. A turn written
wholly in one script now decides the language at any length; mixed turns
are decided by which script carries the sentence; `ok`/`lol` are the same
word in both languages and never move her. **14/14**, with code-switching
("그 monitor 어때?" / "let's eat 삼겹살 tonight") still read by its spine.

One risk this takes on, named rather than hidden: Whisper sometimes
hallucinates a short English phrase ("Thank you.") on near-silence. The old
floor happened to absorb that; now such a turn would be answered in English
unless Whisper's own language detection disagrees with confidence. The
transcription policy only drops a segment when silence and poor decoding
agree.

## 12. A lone word continued an unrelated task

`quit` after a kimchi-stew recipe printed `subjects_differ: true` and then
`decision: CONTINUE`: one bare word is read as a quality refining the open
task, and that branch never consulted the subject. It does now, and a lone
answer to her own question ("Electric.") still continues.
`tests/boundary_matrix.json`: 22/22.

## 13. A Korean hello was answered as a request

In the replay, `안녕` came back as "네, 어디부터 할까요?" -- "yes, where shall
we start?", the reply to an instruction. Every English greeting line opens
with a greeting word ("Hello.", "Good morning.", "Still up?"); two Korean
lines opened with "네,". Both now open with 안녕하세요, and
`tests/test_greeting_openers.py` holds every line in both banks to the same
rule: a reply to a hello opens with a hello, never by agreeing to something.

## 14. The Korean web answered a different question

With the search running on the person's own words, `시애틀에서 인천공항까지
가는데 몇시간 걸려` came back as airport transit **from Seoul** -- the Korean
web's most common reading of `인천공항까지 가는데 몇시간` -- and she answered
"서울에서 인천공항까지 약 1시간 30분". The second search was the same
Korean sentence again with "official source" in front; the router's English
query, "travel time from Seattle to Incheon Airport", was never run.

`ResearchAgent.research` now takes an `alternate_query`, and when it is in
the other language the second search uses it instead of repeating the
first. No extra search when verifying, which already ran two; one extra
when not. A Korean question now reaches Korean pages *and* the English web.
`tests/test_research_alternate_query.py`.

What it did and did not fix, on the final run: the wrong city is gone --
the second search is now "official source travel time from Seattle to
Incheon Airport". But neither search returned a flight time (the Korean
results were Daegu-to-Seoul and Busan-to-Fukuoka threads, the English ones
cheap-flight listings), so the value guard removed the number she supplied
and she said so: "찾아봤지만 구체적인 숫자는 확인되지 않아서 빼고
말씀드렸습니다." Honest, and thin. The limit on that question is now the
search results, not the language of the query.

A hypothesis that turned out wrong, recorded so it is not tried again:
that `_mangled_numbers` was stripping a rounded Korean duration
("11시간 50분") against English evidence ("11 hours 53 minutes"). Re-running
both searches offline and checking four plausible replies against the real
evidence flagged none of them. The guard's log line now names the value it
removed, so the next case can be read from the log instead.

## 15. A standing instruction in Korean was not an instruction

`그래 다음부턴 그렇게 설명해라` -- "ok, explain it like that from now on" --
was answered by repeating the previous answer. Standing orders knew
`always / from now on / whenever / every time`, anchored on the first word,
so both the Korean form and an English one with "ok" in front were
invisible. Korean markers (`앞으로 / 다음부터 / 다음부턴 / 이제부터`) now count
when the sentence ends in a request form -- `앞으로 비가 온대` also opens
with 앞으로 and is a weather report -- and a leading acknowledgement is
allowed in both languages.

The replies were English f-strings, so a recognised Korean instruction
would have been answered "Alright -- I'll 그렇게 설명해라 from now on." All
four now live in `guard_lines`, and the Korean note line says what she will
do rather than quoting an imperative back.
`tests/test_standing_orders_korean.py`.

## 16. A claim, then "I couldn't confirm it"

`2026년 한국에서 가장 많이 시청된 드라마는 '죄와 사랑'입니다. 찾아봤지만
확인되지 않아서, 추측으로 말씀드리지는 않겠습니다.` The value guard had
dropped only the sentence carrying an unverified number and kept the rest,
then appended the line written for when the *whole* answer goes. The same
shape appeared in English ("...around 14 hours... I looked and couldn't find
that"). When some of the answer survives she now says the number was left
out -- `찾아봤지만 구체적인 숫자는 확인되지 않아서 빼고 말씀드렸습니다.`
`tests/test_partial_disclaimer.py`.

## 17. A right guess was overruled

In the English session, "was it Bainbridge Island?" came back "The island
near Seattle is Whidbey Island." Finding 9's neutral fact-check wording
passed only the router's paraphrase, which asks *which* island -- the guess
was no longer in the question, so the model answered the paraphrase. The
instruction now carries the person's own words and asks whether the results
support their guess.

## 18. Dates were read aloud as ranges

"As of 2026 to 09 to 10". The speech filter turns a dash between two numbers
into "to", and its own comment said dates were exempt; the pattern disagreed.
ISO dates and phone numbers now keep their dashes, and "$100-$200" is still
"$100 to $200". `tests/test_dash_dates.py`.

## 19. The reply itself came back in the other language

The language *decision* follows the person's rule (finding 11), but it only
names the language in the prompt; nothing checked what came back. On the
final mixed run, "can you make it shorter?" after a Korean recipe was
answered with the Korean recipe, shortened -- the model kept the language
of the text it was editing. One run earlier the same turn came back in
English, so it is the model's choice, not the decision.

`ChatEngine._answered_in_the_turns_language`, after the voice pass: the
reply's script is compared with the turn's language, and on a mismatch one
call asks for the same content in the right language. A reply already in
the right language costs no call.

The first version was checked live and failed the one time it fired.
Given a rewrite brief with a list of instructions, the model answered one
of them back -- "can you make it shorter?" got **"Do not mention the draft
or the language."** -- and because that is English, a check on the script
alone spoke it. That is worse than the Korean answer it replaced. The
request is now a short plain translation, and a result is accepted only if
it is in the right language, repeats no four-word run of the instruction,
and does not talk about translating; otherwise the original reply stands.
A Korean result also goes through the same 습니다체 pass as the rest of
her Korean, which it would otherwise have skipped.
`tests/test_reply_language.py` pins the measured leak and its neighbours.

Re-checked live on eight turns built for this -- a recipe, then "make it
shorter" in the other language, four times in both directions: 8/8 in the
turn's language. The guard fired once and this time said the answer
("Doenjang jjigae is made by boiling doenjang, gochujang, water, and
kimchi..."); the other three the model got right on its own.

## Live verification

Each session against its own fresh backend, turns sent over the WebSocket
text channel with computer control off. "Session 2" is the person's own
fifteen turns recovered from `runtime/surface.log`, minus the final "quit";
English is the same arc in English; mixed alternates languages turn by
turn, including one-word turns in each.

| session | before these fixes | after | final run |
|---|---|---|---|
| Korean (session 2) | 13/14, one turn hung by the harness | 14/14 | 14/14 |
| English | -- | 14/14 | 14/14 |
| mixed | 10/14 decided correctly (offline) | 14/14 | 13/14 (finding 19) |

Replies in the language of the turn. The before column for mixed is the
language *decision* measured offline, since the floor that caused those
misses decides before the model is called.

What the numbers do not show is content, and that is where the remaining
problems are -- see the list below.

**The verification wrote into the person's real state**, and that was
undone. The test backends share `runtime/`, so the replayed Korean turn
`그래 다음부턴 그렇게 설명해라` was recorded as a real standing instruction in
`runtime/data/directives.yaml`, and every searched turn saved a
research-recall record to `runtime/database/memory.db`. The file held only
that note and was removed; the 49 records written after the person's own
session ended were soft-deleted with the same `is_active` flag the
product's own forget uses (the person's six records from their session,
and everything before, were left alone). Next time: a separate `runtime/`
for verification backends.

## 20. Page titles were recommended as things

"...Serve with rice for a rich, spicy meal. **We Really Cooking Now
Butterbean'S Cafe GIF is the one I'd start with.**" -- and on the next run,
a food blog's headline in the same slot. Four layers let it through, and
the logs showed it was never only the GIF: across every session under
`runtime/`, "Gaming Mice Under" was *selected* four times, "Best Gaming Mice
Under $50" four times, and "Today's Weather in Seoul - Hourly Forecast" once.

1. A *method* question -- "search how to make it really tasty, not the
   obvious stuff" -- was run as a recommendation of *things*.
2. The query was "really Cooking": an intensifier and the router's filing
   label, without the dish in it.
3. The candidacy check (`candidate_fit.off_target`) had no rule for any of
   these titles. The card layer has several, but only for cards -- what
   gets *recommended* never consulted them.
4. The reply had named nothing, correctly, and a guard appended a candidate
   anyway: "The search found 3 and the answer named nothing; naming ...".

**The instrument.** `tests/candidate_title_matrix.json`, scored by
`scripts/candidate_title_check.py`: 18 page titles taken from the logs, and
28 real names -- products, places, and films and dramas whose names are
sentences. Before: **0/18 page titles refused**, 28/28 real names kept.
After: **18/18 and 28/28**.

**Name rules, narrow on purpose.** `candidate_fit.reads_as_a_page`: a title
cut off before its price ("...Under"), a list bounded by one ("Under $50"),
a media or stream page (GIF, "Full Movie", "Watch Online/Free/Now", a GIF
address), a forecast page, a coupon field, writing *about* things
("recommendations", "summary"). The card layer's round-up rules were not
reused wholesale: "starts with a superlative" refuses Best Western and Best
Buy, and "reads as an article" refuses My Mister and Our Beloved Summer.

**What the words cannot decide.** "Realize Cooking Doesn't Really Burn Off"
is a headline shaped exactly like *Don't Look Up*; any name rule that caught
one would catch the other. What decided it was the question: a method has
no candidates. `candidate_fit.asks_for_a_method` (bilingual: "how to", "how
do I", "recipe", 만드는 법, 어떻게 만들어, 레시피) makes both places that turn
results into a pick stand aside -- candidate ranking hands the pages to the
ordinary research path as evidence, and the guard no longer appends a name.
`tests/test_candidate_titles.py`.

**Live, on a fresh backend:** "search how to make it really tasty, not the
obvious stuff" was answered with a method -- well-fermented kimchi, anchovy
stock, a spoonful of doenjang -- and no pick, in English and in Korean,
with both gates logging. "Recommend a good wireless gaming mouse under $50"
still named a real product ("The Logitech G305 Lightspeed is a solid choice
under $50"), and all six round-up and page titles in its results were
dropped.

**Not fixed here:** the "really Cooking" query. An intensifier read as a
constraint, plus a filing label for a subject, still builds junk queries for
recommendation turns; with the gate above it no longer reaches a method
question, but it is a query-builder defect in its own right.

## 21. The same talk, in two languages

"Keep fixing until English and Korean are at the same percentage" needs a
number that measures the language and nothing else. The earlier one did
not: Korean 73% was `korean_day` (dramas, dinner) and English 80% was
`evening` (sleep, headphones) -- two conversations. Two arcs were added to
`scripts/live_dogfood_conversation.py` as turn-for-turn translations with
the same acts, `english_day` (of `korean_day`) and `korean_evening` (of
`evening`), and `scripts/language_parity_report.py --tag <tag>` pools the
turns by language and runs a two-proportion z-test, so a gap is called a
gap only when noise cannot explain it.

Each measurement is 4 arcs x 3 runs = 144 turns, run from a frozen copy
of the tree (edits made while it runs cannot mix versions) against its own
runtime via `ELAINA_RUNTIME_ROOT` (nothing reaches the person's memory).

**Baseline** (`parity0`, before the fixes below):

| same content | Korean | English |
|---|---|---|
| day | 29/36 (81%) | 29/36 (81%) |
| evening | 28/36 (78%) | 31/36 (86%) |
| pooled | 57/72 (79%) | 60/72 (83%) |

Gap -4 points, p=0.52: not distinguishable from noise -- but the failures
were different in kind, which is what the fixes followed:

| class | Korean | English |
|---|---|---|
| register_drift | 6 | 0 |
| too_verbose | 2 | 6 |
| self_repetition | 4 | 5 |
| request_restated | 2 | 0 |
| duplicate_offer | 0 | 1 |
| service_phrasing | 1 | 0 |

**Round 1** (measured as `parity1`):

- A 해요체 question converted where it is mechanical: 보셨어요? ->
  보셨나요?, 어때요? -> 어떠신가요? (`korean_register._formal_question`).
- The evidential "했나 보세요" is no longer made an order ("했나 보십시오").
- The person's sentence handed back with a new ending is an echo
  ("회의만 했네요", "회의만 했군요"): compared as characters, both languages
  (`response_quality._says_it_back`).
- A value disclaimer and "sources disagree" are said once, not on every
  turn after.
- An unanswered clarification is not asked twice in a row -- English
  "Over-ear or in-ear?" three times running, in three of three runs.

**Round 2** (measured as `parity2`):

- The clarification is *written* in Korean. It existed only in English,
  the ask act is locked against rewording, and the reply-language guard's
  translation came back as "이어폰은 이어폰이냐, 이어폰이냐?" -- both options
  the same word, in 반말 -- in two of three Korean evening runs. Now
  "오버이어와 인이어 중 어느 쪽이 좋으신가요?", and "인이어로" is read as an
  answer (`recommendation_state._VARIANTS_KO`).
- A reaction or receipt still too long after the re-say is cut to its
  act's length, filler first (`conversation_style.cut_to_length`); when
  length is the only fault it is cut without the re-say at all. English
  too_verbose was the re-say coming back just as long, six times.
- Three endings: 힘드셨나 보죠 -> 힘드셨겠습니다 (the re-say returned it
  still drifting three times), 마시시는 거죠 -> 마시시는군요, and
  걱정 없으세요 -> 걱정 없으십니다 -- the general 세요 rule had been making
  it 없으십시오, an order.

**Round 3**, found in `parity1` (measured with round 4 as `parity3`):

- "Беспроводные/проводные наушники Nothing Headphone (a) черный... is the
  one I'd start with." -- a Russian storefront listing, cut off by the
  search page, appended in English to a Korean answer about battery life.
  A title ending in an ellipsis now reads as a page (`candidate_fit`), a
  name in a script neither language uses is never put forward
  (`TextFilter.OTHER_SCRIPT_PATTERN`), and the sentence is said in the
  turn's language.

**Round 4**, found in the first `parity2` runs:

- The act classifier's closed set had English receipts and only bare
  Korean words. "ok i'll do that" was a receipt in three of three English
  runs; "오케이 그렇게 할게", the same sentence, was an *answer* in three of
  three Korean ones -- held to an answer's four sentences, so the length
  cut never applied. "흠" likewise, where "hm" was a receipt. The Korean
  counterparts of the English entries were added
  (`conversation_style._RECEIPT_WORDS`); "그러게 말이야" was left out
  because "tell me about it" is not a receipt in either language. Its only
  caller is the style act, so routing is unchanged.
- "결과가 중요하겠죠" -> "결과가 중요하겠습니다": 겠죠 is the presumptive
  with a confirming 죠, and the swap needs nothing about the stem.

**Deliberately not changed:** the other act mismatches. "already seen that
one" came out an *answer* in both English `parity2` runs where "그건 봤어" was
a react in all three Korean ones, and "hmm not really" / "음 별로네" was an
answer in both languages. Unlike the receipt set, these are not a rule in
the code: `speech_act` is the router model's own field
(`intent_router.py`, `payload.get("speech_act")`), and "what about movies
then?" came out react in one run and answer in the next. Adjusting the
router until it agrees with the scorer's declared acts would move the
number without making her better.

**Round 5**, found in the full `parity2` (measured as `parity4`):

- Hearsay: "내일 비가 온대요" -> "내일 비가 온다고 합니다". 대요 attaches
  after the same syllable 다고 does, so nothing about the stem is needed.
- The dispute detector disagreed with itself across the pair: "확실해?"
  and "are you sure?" were disputes, while "you sure about that?" -- the
  English arc's translation of the same turn -- and "확실한 거야?" were
  not. Both added, as whole turns only ("you sure know a lot" is a
  compliment, "확실한 방법 알려줘" asks for a reliable method).

**Looked at and left:** "확실해?" was answered by restating the previous
line in 5 of 9 Korean runs against 1 of 9 English (Fisher p ~ 0.13, not
distinguishable). The dispute path was not involved -- the claim held no
figure to re-check -- and the re-say restated it too. Asked "are you
sure?", restating the claim is the honest reply, and across the three
measurements self_repetition is even: 11 Korean, 10 English.

**Round 6**, found in `parity3`:

- "힘들었겠지." (반말) -> "힘들었겠습니다", and a checking question,
  "영화를 말씀하시는 거죠?" -> "말씀하시는 건가요?".
- Chinese glued into a Korean word: "원하시면详细介绍해드리겠습니다." --
  the family of "청양椒" from the person's own session. The kana guard
  leaves Han alone on purpose, since 한자 is Korean; the rule catches only
  a character inside a Hangul word or in front of the syllable that makes
  it a verb, so "愛라고 씁니다" and "한자(漢字)" survive
  (`TextFilter.GLUED_HAN_PATTERN`).
- The pick sentence named a page: "Sennheiser, Headphones, Microphones,
  Wireless Systems부터 보시는 걸 추천합니다." The card layer had already
  dropped that title as "several of them, not one"; the guard now asks the
  same question (`response_surface.names_a_specific_thing`). Either
  language could produce this.

**Round 7**, found in `parity4`:

- "지금은 조금 힘들었겠어요." went out in 해요체 although 겠어요 is in the
  converter's table. The log line before it was "[Response Guard] The
  final text repeated the previous answer ...; regenerating once" -- the
  final check regenerates *after* the voice pass, so its text never met
  the conversion. It does now.
- The same check's give-up line was an English literal ("Sorry -- I
  answered the wrong thing there..."), said as English in a Korean
  conversation. It is a `guard_lines` entry with both languages now.

**Round 8**, found when Korean moved *ahead* in `parity4` and `parity5`:

- **Part of Korean's lead was the guard not reading Korean.**
  `grounded_values.unverified_entities` finds a name by its capital
  letters and wakes up on "recommend", "try", "visit"; its Korean trigger
  was 매장/가게/지점 and nothing else. So an English drama recommendation
  was retracted and ended with an offer to look, while Korean replies
  recommended '더 블랙 블레이드' (a Canadian drama), '패미의 선택', '아보스',
  '서울 콩고리' (a restaurant) with no search behind them -- 30 of 432
  Korean replies across the measurements quoted a name, none was checked.
  Korean writes names in quotation marks where English capitalises them;
  quoted Hangul spans are now names (quoted speech excluded), checked
  against the evidence as strings, with the Korean counterparts of the
  trigger words and of the landform exemption ('한라산').
  Offline, with no evidence (an upper bound), this would have fired on
  3-11 of 72 Korean turns per measurement against 1-3 English. **Korean's
  number is expected to drop**: the reply loses the invented title and
  gains an offer to look, which is what English was already paying.
- The offer said "I don't want to send you somewhere I haven't checked"
  after a drama. A reply that names no place now gets a line about
  recommending *something* (`unchecked_name_offer`); the place line stays
  for shops and restaurants.
- Said once: two drama turns in a row each ended with the same offer, and
  the second counted as her repeating herself. The offer is still parked;
  it is not said again while something else in the reply survives.
- "The one I actually found is ..." was English-only; it is said in the
  turn's language now.

**Round 9**, found in `parity6b`, and not a percentage question:

- "그렇구나" was answered "What would you like me to do next?" -- the only
  reply of 576 Korean turns across every measurement with no Korean in
  it. The commitment guard removes a promise with no ability behind it
  and puts a question in its place; the question was an English literal,
  and the guard runs after the reply-language check. It is a
  `guard_lines` entry now (`next_step_question`). Too rare to show in a
  percentage, and exactly the rule the person set for mixed sessions.
- Sweeping chat_engine for other sentences returned after that check
  found one more in conversation: "how's it going?" is recognised in
  Korean ("아직이야?", "어떻게 돼 가?") and all three answers -- still
  working, waiting for a go-ahead, nothing running -- were English. They
  are `guard_lines` entries now; the English wording is unchanged.
- Left for Milestone B, with computer control: "I don't have a
  {name}." (an ability she was asked about by a name she does not have),
  the open-windows list and the browser click statuses are English-only
  too.

**About the measurements themselves:** every isolated runtime started with
an empty `memory.db` -- the tables are created only by
`scripts/setup_database.py`, never at startup -- so each searched turn
logged "no such table: memories" and nothing was recalled, in both
languages, from `parity0` on. The comparison is fair; it is not the
person's own setup, which has the tables. Flagged separately as a
fresh-install problem rather than changed mid-series.

**Revisited:** "확실해?" restating the previous line is 8 of 12 Korean runs
across `parity0`-`parity3`, against 2 of 12 English (Fisher p ~ 0.04 for
one turn picked after the fact, so suggestive rather than settled).
`parity4` is the first run in which "you sure about that?" takes the same
dispute path, which will show whether that path is what repeats.

**Results**, 144 turns each, pooled by language:

| measurement | code | Korean | English | gap | p |
|---|---|---|---|---|---|
| `parity0` | baseline | 57/72 (79%) | 60/72 (83%) | -4 | 0.52 |
| `parity1` | round 1 | 58/72 (81%) | 60/72 (83%) | -3 | 0.66 |
| `parity2` | rounds 1-2 | 60/72 (83%) | 62/72 (86%) | -3 | 0.64 |
| `parity3` | rounds 1-4 | 62/72 (86%) | 64/72 (89%) | -3 | 0.61 |
| `parity4` | rounds 1-5 | 67/72 (93%) | 64/72 (89%) | +4 | 0.38 |
| `parity5` | rounds 1-7 | 68/72 (94%) | 63/72 (88%) | +7 | 0.15 |
| `parity6` | rounds 1-8 | 65/72 (90%) | 65/72 (90%) | 0 | 1.00 |
| `parity6b` | the same snapshot, run again | 65/72 (90%) | 69/72 (96%) | -6 | 0.19 |

**Where it ended.** The same snapshot was measured twice. `parity6` came
out level, 65/72 each; `parity6b`, identical code, had English at 69/72 --
English moved four turns between two runs of the same code, Korean did
not move. Pooled over both, 144 turns per language: Korean 130/144
(90.3%), English 134/144 (93.1%), a 2.8-point gap the test cannot separate
from noise (p=0.39); day 64 against 65, evening 66 against 69. No turn
failed in more than three of six runs in either language. At this sample
size a real gap smaller than about seven points cannot be resolved either
way, so the claim is not "the percentages are equal". It is that no
measurement in the series could tell the languages apart, that both are
well above their baselines (Korean 79% -> 90%, English 83% -> 93%), and
that the *failures stopped being different in kind*. At the baseline Korean failed
on register and English on length. In `parity6` the guards fire alike --
unverified names retracted 4 Korean / 4 English, where it had been 0
Korean; length cuts 7 / 10; re-says taken 9 / 6 -- and what is left in
both languages is mostly repetition and the router's own act choices.
The drop from `parity5` (Korean 94% -> 90%) is round 8 working: the part
of Korean's lead that came from not being checked is gone.

**Left for later**, seen in `parity6` and not fixed, because they apply to
both languages and fixing one side now would reopen the gap:

- "그러게 말이야" was answered with the give-up line ("죄송합니다. 엉뚱한
  답을 드렸습니다..."): the final check read a similar sympathetic reply as
  a repeat, regenerated, read that as a repeat too, and apologised on a
  turn where nothing was wrong. Receipts are exempt from that check;
  reactions like this one are not.
- Two guard lines stacked in one reply -- the value note ("구체적인 숫자는
  ...빼고 말씀드렸습니다") followed by the unchecked-name offer.
- "어제보다는 낫겠죠?" -- 겠죠? as a question is not converted; 겠습니까?
  would be the mechanical form.
- English titles are split at lowercase words ("The Power of the Dog" ->
  "The Power", "Dog"), so real films can be retracted in pieces.
- The measurements ran without memory tables (below), in both languages.
- A YouTube upload's title was recommended as a drama: "'The Mafia Don
  Hired Me as His... (FULL DRAMA 2026)'입니다. 유튜브에서 바로 시청할 수
  있습니다." The page filter catches "full movie" and a title ending in
  an ellipsis, not "FULL DRAMA" or an ellipsis mid-title -- and this was
  the model quoting the evidence, not a picked candidate, so the filter
  never saw it. Either language.

In `parity3` Korean led on the day pair (31/36 against 30/36); the gap
that remains is the evening pair (31/36 against 34/36).

## Found, not fixed in this pass

These showed up in the English verification session and are not about
language. They are recorded so they are not lost:

* "Is there a casino on that island?" was answered, in both languages on
  different runs, that Bainbridge Island *has* a casino -- the Suquamish
  Clearwater Casino Resort, which is off the island, across Agate Pass.
  The search found the right casino and the reply misread where it is. No
  guard checks a place relation like "on the island", so nothing caught it.
* "What's the most watched drama in Korea" came back vague after searching
  ("romantic series... on iQIYI and TikTok"): the search layer found
  nothing ranked, and she said so badly rather than saying so.
* A Chinese character inside a Korean word: "청양椒" for 청양고추, in the
  title-fix live run. The same bleed as finding 2's ようで, but finding 2's
  guard is kana-only on purpose, because 한자 is real Korean. The shape that
  separates them is the order: hanja takes a Korean particle *after* it
  (大韓民國이라고), while bleed puts the ideograph straight after a Hangul
  syllable (청양椒). Not built; recorded so it has a starting point.
* Place names transliterated from English evidence came out wrong in
  Korean -- "시에이트" for Seattle three times in the final Korean run, and
  배인브리지 / 바인브리지 for the person's 베인브리지, in a conversation where
  they had written 시애틀 and 베인브리지 themselves.

  The obvious fix was built and measured, and it is not in the tree. "Keep
  the person's own spelling of a name": a reply word that is a near-variant
  of one the person wrote (same opening consonant, close letter by letter,
  within a syllable of the length, not a shorter or longer form of it) is
  replaced by their spelling. On the real replies it fixed every target --
  and rewrote ordinary words across 17 replies: 시청자 -> 시청한, 고추장 ->
  가장, 맛을 -> 많은, 있습니다 -> 있는지, 알려져 -> 알려줘. The failure is
  structural, not a threshold: Korean has no capitalisation, so "the names
  the person wrote" collected every word they typed, and ordinary words in
  her reply were corrected toward them. A real fix needs to know which words
  are names -- the router's entity field is the obvious source, but it holds
  the English spelling -- and that is a design question, not a patch.

## Not a product bug: the turn that never answered

In the first replay, turn 3 never finished. The cause was the test harness:
a waiter from an earlier attempt was watching a block-buffered backend log
for its "ready" line, woke once enough output had been flushed, and started
a second replay into the same backend mid-turn. Its dropped connection
interrupted turn 3. The runner now uses an unbuffered log and one fresh
backend per session.

