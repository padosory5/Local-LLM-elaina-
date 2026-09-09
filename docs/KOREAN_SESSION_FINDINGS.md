# Korean dogfood session: five findings

Source: `runtime/koreanSession.log`, a real session driven by the user, in
Korean, on `qwen3:8b`. Four things were reported. Three had a cause that
could be found in the log and fixed; the fourth turned out to be three
problems wearing one symptom, only one of which is fixed and one of which
is now measured and deliberately not. A fifth was found while reading and
is fixed.

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

**Not fixed, and worth knowing:** the task session itself still said
`decision: CONTINUE` with `previous_thing: war` and `current_thing: fee`
on the same turn. It prints both nouns and does not compare them —
`about_the_same_thing` reaches its head-noun check only when the turn
carries a preference constraint, which a Korean sentence rarely produces,
so `follow_up and not topic_shift` decided it. That is a task-boundary
change, it affects candidates and evidence as well as queries, and it
should be done deliberately rather than folded into a query fix.

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

Three causes, and they are not equally tractable.

**(a) The query was contaminated.** All three UW searches ran as
`6/25 war Washington University ...`. Fixed above.

**(b) The query named a different university.** The user said "미국
시애틀에 위치한 워싱턴 대학교". The router wrote *Washington University in
Seattle*. "University of Washington" is in Seattle; "Washington
University" is in St. Louis. The query names a real university, and not
the one the user goes to.

**(c) The reply is capped.** `[Style] act=report sentences<=3`. "그런
뻔한거 말고" is a request for something *more specific*, and the contract
allows three sentences to deliver it.

### What was done about (b): the instrument, not the fix

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

This is **not fixed**. Making the query follow the turn's language changes
what every Korean search does, the subject stored on the task is itself
written in English by the router, and 80% on five searches is one session.
Rule 1 says the instrument comes first; this is the number that would have
to move.

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
