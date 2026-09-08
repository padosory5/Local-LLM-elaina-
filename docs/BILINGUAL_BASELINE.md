# Bilingual baseline (Milestone A, A2)

She replies in the language you spoke, switches when you switch, and is the
same person in both. Measured against `qwen3:8b`, the same way A1 was.

## Results

| | English | Korean + mixed |
|---|---|---|
| clean turns | **30/32 (94%)** | **15/20 (75%)** |
| findings | 2 | 5 |

**Exit criterion not met.** The target was Korean within 10 points of
English; it is 19 behind. What follows is why, and why the gap is not the
number it looks like.

### Language switching: 8/8, every run

| You said | She answered in | |
|---|---|---|
| "hey, can you check the weather in seoul today" | English | |
| "고마워" | English | under the switch threshold, by design |
| "그 monitor 어때? 50달러 이하로 찾아줘" | Korean | code-switching did not flip her |
| "ok" | Korean | stayed put |
| "speak english please" | English | pinned |
| "오늘 날씨 어때?" | English | the pin beat a full Korean sentence |
| "한국어로 말해줘" | Korean | repinned |
| "고마워" | Korean | |

Stable across five consecutive live runs. This half of A2 works.

### Register: 습니다체

| | Korean sentences drifting |
|---|---|
| before any Korean guard | 7/28 (25%) |
| register guard only | 13/28 (46%) *(the instrument got stricter)* |
| **+ deterministic converter** | **3/32 (9%)** |

`brain/korean_register.py` converts the endings it knows exactly --
이에요→입니다, 있어요→있습니다, ~ㄹ게요→~겠습니다, and the regular
받침 + 어요/아요 → 습니다 -- and leaves anything needing real verb
morphology alone. Prompt wording did not hold the register and a re-say
returned 해요체 again, once 반말; this is the project's usual answer to a
behaviour confirmed live.

### Mixed-language replies: 1/16 → 0/16

Guard-generated sentences now exist in both languages
(`brain/guard_lines.py`), and an offer whose subject is English is not made
on a Korean turn.

## Why the gap is not really 19 points

**The instrument measures register and shape, not sense.** From the final
run, scored *clean*:

> 콜드브루는 물을 9분간 끝에 설탕을 넣고, 15분간 우유를 넣어 섞은 뒤
> 20분간 끝에 우유를 넣고 냉각하면 됩니다.

Perfect 습니다체, correct length, no service phrasing, no repetition — and
complete nonsense about a drink that involves neither boiling nor milk.
A1's failure classes were built to catch *sounding like an interface*. The
Korean problem is now *being wrong*, which they do not measure and were
never meant to.

So the honest reading is: the parts A2 owns — switching, register, one
character, no mixed-language replies — work. The parts it does not own,
grounding (A6) and context (A3), are worse in Korean than in English, and
that is where the real gap is.

## The model

`qwen3:8b` is the limit for Korean *content*. Measured directly on the turns
that failed live:

| | qwen3:8b | qwen3.6:35b-a3b |
|---|---|---|
| cold brew method | "물을 끓여… 냉장 스티밍" (wrong) | "1대8 비율로 냉장고에서 12시간 이상" (correct) |
| film recommendation | invents titles every run | **'파묘'** — real, current, Korean |
| stray CJK mid-sentence | "비가 오는样子입니다" | not seen |
| routing accuracy | **131/134 (97.8%)** | 127/134 (94.8%) |

**Both models cannot be resident on this machine.** 23.9 GB against 16.3 GB
of VRAM with ~9.5 GB free once the 8b is loaded, so a two-model turn evicts
one of them every time:

```
alternating (router 8b -> reply 35b)      single model
 turn  router   reply    total             turn   total
    2    4.7s   18.6s    23.3s                2    0.2s
    5    4.7s   19.2s    23.9s                3    0.2s
```

A single larger model costs 1.8s -> 4.3s per turn, which is affordable, but
routes below the 95% gate. **Decision: stay on `qwen3:8b`**, and treat
Korean content quality as a documented model limit rather than a bug to
chase in the style layer.

## The guard audit

Every module that inspects or rewrites what the user hears now declares
`LANGUAGES`, enforced by `tests/test_guard_languages.py` — including a
check that a new `...Guard` class cannot be added without answering the
question.

| Bilingual | English only |
|---|---|
| `conversation_style` (unevenly, marked per rule) | `response_policy` |
| `guard_lines` | `response_quality` |
| `text_filter` | |
| `turn_language`, `korean_register` | |
| `grounded_values` (by accident: it compares numbers) | |
| `action_commitment` | |

`action_commitment` was the important one and is done: it is the honesty
guard, and it had exactly one Korean alternative which required 제가.
Korean drops the subject, so "확인해 보겠습니다" — straight out of her own
status bank — carried no pronoun and was never checked against whether
anything ran. Its endings were 볼게 / 드릴게 as well, which are 해요체 and
반말, so it was reading for a register A2 had already moved her out of.
Korean promises are now read off verb endings (stem + 겠습니다 / 중입니다 /
ㄹ게요), with the conversational verbs — 말씀드리다, 알려드리다 — excluded
for the same reason English excludes "tell" and "explain".

## Voice

TTS is ElevenLabs, because Piper voices are language-locked and a Korean
Piper voice would be a different woman answering. One voice, both
languages, delivery settings in `config.yaml`:

```yaml
stability: 0.75   similarity_boost: 0.75   style: 0.0
speed: 0.95       use_speaker_boost: false   volume: 0.85
```

Nothing was sent before, so every reply used the voice's raw defaults.

**A fault worth remembering**: every Korean reply came out of the speakers
as "The result is shown on screen." `AudioManager` read the response
language once at construction, and `for_configured_speech` strips Hangul on
an English turn — so she answered correctly in Korean and refused to say
it. The same fault ChatEngine had, in the one place that silences her.

## Known limitations

- Korean content quality is bounded by the model: invented film titles,
  wrong procedures, occasional stray CJK. Not fixable in the style layer.
- `response_policy` and `response_quality` are still English-only and
  report clean on Korean. Neither is an honesty guard: the first shapes
  advice and length, the second catches repeated answers.
- A question form in 해요체 ("어때요?", "지내세요?") is not converted --
  the converter skips questions rather than guess at their morphology.
- The style metric does not measure whether a reply is *true*. A6 does.
