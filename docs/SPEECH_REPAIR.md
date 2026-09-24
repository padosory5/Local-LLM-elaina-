# Hearing what was meant

Two requests from the person, handled as one problem:

> STT is not working well in Korean. If the STT misprints the input, add a
> guard so she can still work out what was meant.
>
> Correct the person's slip: I told her about CPT and accidentally said
> CBT. She should know what I'm interested in, ask "are you actually talking
> about CPT?", and look up the right thing.

A misheard name and a misspoken one look the same from where she sits: the
turn holds a word that is *almost* a term the conversation has been using,
and not the term itself. So the work is in two layers: hear better at the
source, then catch what still gets through.

## 1. Speech-to-text: context Whisper never had

`voice/stt.py` ran faster-whisper `small` with greedy decoding, automatic
language detection and no context at all. Every clip was heard cold.

**The instrument.** `scratchpad/stt_bench.py` (not in the tree): 14
utterances synthesised with the Windows Korean and English voices (Heami,
Zira), in the person's own topics -- CPT/OPT, UW, 시애틀, 베인브리지 -- and
ordinary turns ("그렇구나", "간단한 걸로"). Each is run clean and with white
noise at 10 dB and 5 dB SNR, through the app's own model, under four
settings. Scored on character error rate against the known text and whether
the key term came out exactly.

| setting | clean | noise 10 dB | noise 5 dB | key terms (clean / 10 / 5 dB) |
|---|---|---|---|---|
| A: before (auto language, beam 1, no context) | 0.048 | 0.643 | 0.619 | 8/8, 6/8, 2/8 |
| B: + retry in the conversation's language | 0.048 | 0.335 | 0.512 | 8/8, 6/8, 2/8 |
| C: + the conversation's terms as hotwords | 0.043 | 0.291 | 0.414 | 8/8, 7/8, 8/8 |
| D: + beam 5 | **0.037** | **0.256** | **0.352** | **8/8, 8/8, 8/8** |

On near-silence, setting A produced "Thank you." and setting C produced
nothing.

**A real bug the bench exposed.** When detection was uncertain or landed on
a language outside `allowed_languages`, the clip was re-transcribed in the
*first* allowed language -- English. "UW 국제학생 사무실 전화번호 알려줘" at
5 dB was detected as Chinese and came back "You don't believe this? Do you
think I'm a shithead or what?" An uncertain reading now retries in the
language the conversation is in (`transcription_policy`,
`preferred_language`). A confident detection is never overruled, so
switching language mid-conversation still works.

**What changed:**

- The voice loop (`main.py`) hands the transcriber the conversation's
  language and its names and acronyms before each listen
  (`SpeechToText.set_conversation_context`, `ChatEngine.listening_terms`).
- The terms go to Whisper as `hotwords`, at most eight and sixty
  characters. A long prompt is what Whisper reads back on an unclear clip,
  which is why the configured `initial_prompt` stays empty; a transcript
  that is nothing but the hints read back is dropped.
- `beam_size: 5` in `config.yaml`. It costs about 25 ms per clip on the GPU
  (79 -> 104 ms).

Synthesised speech is cleaner than a person talking into a room, which is
why the noise conditions are there. The live check of this half is the
person speaking Korean to her again.

## 2. A word that is almost what the conversation is about

`brain/near_miss.py`, called at the top of `ChatEngine._route_turn` right
after the existing ability-name repair, before anything reads the turn.

**What counts as a term the conversation holds** is narrow on purpose. The
last attempt at this ("keep the person's spelling of names",
KOREAN_SESSION_FINDINGS) treated every word they had typed as a name and
rewrote 시청자 into 시청한. Held means: an acronym; a capitalised name read
the way the grounding guards read one (a capital that only opens a sentence
is grammar); a Korean word of three or more syllables that came up in at
least two messages; or what the router has already recorded as the entity
or subject.

**What counts as a near-miss:**

- *Acronyms*: same length, exactly one letter different, and the two letters
  rhyme. CPT/CBT differ by P and B, both "-ee" -- the confusion a
  transcriber makes and the one a person reaching for an acronym makes.
  CPT/CPU differ by T and U, which do not rhyme: another acronym, left
  alone. OPT is left alone the same way.
- An acronym Whisper spelled out in Hangul is read by its letter names:
  "씨비티" is CBT, "씨피티" is CPT (and so is not a slip at all).
- *Korean names*: the same number of syllables, exactly one syllable
  different -- the shape of a misheard vowel (배인브리지 / 베인브리지). Three
  syllables only when the difference is a confusable vowel (ㅐ/ㅔ, ㅗ/ㅓ,
  ㅜ/ㅡ ...); that is what keeps 시청자/시청한 apart.
- *English names*: capitalised in the turn, same first letter, one or two
  letters off (Bainbrige / Bainbridge). A lower-case word is never read as a
  misspelled name, so "parks" is not "Paris".
- Never a word that is already part of the conversation, never a pair the
  person has already said are different, and never a guess between two
  equally close terms.

**What she does:**

| | what she says | then |
|---|---|---|
| acronym | "You said CBT -- did you mean CPT?" / "방금 CBT라고 하셨는데, 혹시 CPT 말씀이신가요?" | waits for the answer |
| longer name | "I took that as Bainbridge." / "베인브리지 말씀으로 이해했습니다." followed by the answer | answers the corrected turn |

An acronym is asked about because one letter is the whole difference
between two unrelated things, and only the person knows which they meant. A
longer name is taken as the held one and said out loud, so they can still
correct it. That follows the deliberation gate's rule: act, act and say the
assumption, or ask one question.

**The answer**, read on the next turn (`near_miss.read_answer`):

- "yes" / "응" / "네" / naming the held term -> the original turn is answered
  as if they had said CPT;
- "no" / "아니" / naming the heard term -> the turn is answered as said, and
  that pair is never asked about again this session;
- anything else is a new turn, and the question is dropped.

The question is the one outstanding question: asking it clears any pending
offer or clarification, and the other gates clear it the same way.

## The model: small -> large-v3-turbo

With the person's permission the larger model was downloaded (Hugging
Face, `mobiuslabsgmbh/faster-whisper-large-v3-turbo`, ~1.6 GB) and run on
the same 24 clips as small -- both benches, clean and at 10 / 5 / 3 dB,
the app's own settings (beam 5, uncertain language retried in the
conversation's language, no hints):

| mean character error | small | large-v3-turbo |
|---|---|---|
| clean | 0.052 | **0.012** |
| 10 dB | 0.310 | **0.121** |
| 5 dB | 0.485 | **0.296** |
| 3 dB | 0.599 | **0.288** |
| key terms / places exactly right (clean) | 23/27 | **26/27** |
| time per clip | 152 ms clean, up to 604 ms in noise | ~230 ms throughout |
| GPU memory | +0.9 GB | +2.6 GB |

The person's own sentence, clean: small "인천공항에서 미국 **시의틀**까지";
turbo "인천공항에서 미국 **시애틀**까지 가는 데 몇 시간 걸려" -- exactly
right. Also exact: "벨뷰에서 시애틀 다운타운까지", "뉴욕 JFK 공항에서
맨해튼까지". Under noise small produced long garbage, which is why it was
slower there.

**The one cost**: on near-silence turbo transcribed "감사합니다." -- a
known Whisper habit learned from subtitles -- where small returned nothing.
Silero's voice detection normally keeps silent clips from reaching Whisper,
but a noise burst can get through, so there is a guard
(`transcription_policy.reads_as_silence`). Probed on near-silence and on
real short utterances:

| clip | first read as | text |
|---|---|---|
| near-silence | Portuguese 0.19 / Spanish 0.19 / English 0.28 | "Obrigado." / "Gracias." / "Thank you." -- "감사합니다." once retried in Korean |
| real 감사합니다 (clean, 10, 5 dB) | Korean 0.99-1.00 | 감사합니다. |
| real "Thank you." | English 0.97-1.00 | Thank you. |
| real 고마워 | Korean **0.19** | 고마워. |

The no-speech probability is no help with this model -- 0.00 for silence
and speech alike -- so the app's existing silence rule no longer fires.
What separates them is both at once: one of the phrases Whisper ends videos
with *and* a first reading it could not place in a language (< 0.35). A
real 감사합니다 is read at 0.99; a real 고마워, though uncertain, is not one
of those phrases. Either alone would have dropped real speech.

`config.yaml` now says `model_size: "large-v3-turbo"`. The fallback to CPU
still exists but is much slower with this model.

**It does not slow the rest of a turn.** The GPU is shared with another
program (55-96% busy with no backend running), so there is no quiet-GPU
timing; instead the same one-turn demo ran back to back with each model
loaded, under the same background load. Typed turns do not use Whisper at
all, so any difference would be memory pressure on qwen3 -- which stayed
`100% GPU` in `ollama ps`.

| run | whole turn | router model | web search |
|---|---|---|---|
| turbo | 10.3 s | 3.6 s | 4.0 s |
| small | 17.9 s | 7.9 s | 7.4 s |
| turbo | 13.3 s | 5.9 s | 4.9 s |

The spread is the router's model call and the search, both of which move
with whatever else is using the GPU; an earlier run at 96% GPU load took
30 s with the router alone at 12.7 s. On a voice turn turbo itself adds
about 80 ms per clip over small (~230 ms against ~150 ms).

## 3. Names the conversation never mentioned

Measured live by the person, asked cold:

    said:   인천공항에서 미국 시애틀까지 가는데 몇 시간 걸려?
    heard:  빈천공항에서 미국 CLT까지 가는데 몇 시간 걸려?

She searched for "Binche Airport". The guard above compares a word with
what the conversation holds, and this conversation held neither place.

**Real mishearings, not invented ones.** Ten travel questions, spoken by
the Windows Korean voice, run through the app's own model (small, beam 5)
clean and at 10 / 5 / 3 dB noise. Even *clean* audio came back 시애틀 ->
"시의틀" / "시에틀", 밴쿠버 -> "뱅코버", 신칸센 -> "싱칸센", 맨해튼 ->
"메네튼"; noise produced 시리틀, 지지도 (제주도), 로스앤젤리스, and whole
sentences no guard can repair ("인산부한테 인산비날 가는 것이 있죠").

**What was added:**

- `brain/known_names.py`: well-known places paired in Korean and English
  (airports, Korean cities, the Seattle area, the cities people fly
  between) and real airport codes.
- A Korean word the sentence uses *as a place* -- a locative particle
  (에서, 까지, 으로, 의...) or a place word after it (공항, 다운타운, 날씨)
  -- that is one or two sounds from a known place, with the same number of
  syllables, is taken as that place and said: "인천공항 말씀으로
  이해했습니다." "빈천 공항", heard as two words, is joined first. The
  place-use rule is what keeps 지지도 as approval rating in "대통령 지지도가
  올랐어", and the two-sound limit is what keeps 시리즈 (series) and
  다운타운 -- three sounds from 한인타운 -- as they are.
- Every name in the sentence is corrected before anything is asked, and
  each is said.
- **A real code that sounds like their place.** CLT is Charlotte; read
  aloud in Korean it is 씨엘티, which is what 시애틀 turned into. When a
  place the person has been talking about -- or has told her about before
  -- sounds like the code, she asks: "방금 CLT라고 하셨는데, 혹시 시애틀
  말씀이신가요?". With no such place, CLT stays Charlotte.
- **What she knows about them.** Place names and acronyms from their
  memories (an English "studies at UW in Seattle" gives 시애틀) are read
  once every ten minutes and join what she listens for -- in the near-miss
  guard and in the transcriber's hints. A known place the person names once
  counts, where an ordinary Korean word needs two mentions.

**Hints must be the right words.** Giving Whisper the whole place list as
hints made it *worse*: noisy error 0.479 -> 0.525, because on a noisy clip
it read the list back as speech ("인천공항, 시애틀, 김포공항, 제주도, ...").
Given the right words it fixed the source: with 시애틀 among the hints the
person's own sentence came back exactly right at 5 dB. So the hints are the
conversation's and their memories' terms only, and a transcript that is
the list read back -- even with a word of its own in front -- is dropped.

## 4. Speech too garbled to answer

A clip that decodes into words nobody said -- "인산부한테 인산비날 가는 것이
있죠" -- was answered as if it made sense ("인산비날은 인산부한테 가는 것이
있습니다"). No name repair can fix a sentence like that. The transcriber
knows when it is guessing; a person would say they did not catch it.

**The instrument.** large-v3-turbo on all 112 clips (the two sentence
benches and four short replies, clean and at 10 / 5 / 3 dB), each word
with its own probability, beside each clip's character error:

| signal | garbled caught (of 19) | good flagged (of 64) |
|---|---|---|
| sentence average log-probability < -0.83 | 5 | 0 |
| average word probability < 0.41 | 1 | 0 |
| least-sure word < 0.35 | 12 | 0 |

Word probabilities cost nothing measurable (~425 ms per clip without,
~430 ms with).

**The rule** (`transcription_policy.heard_unclearly`): the least-sure word
below 0.30 **and** the average below 0.65, for transcripts of two words or
more.

- Both halves, because one doubtful word in a sound sentence is a misheard
  name -- "빈천공항에서 미국 CLT까지" -- which the name repair handles
  better than asking would. A whole sentence of doubtful words is noise.
- Never a one-word reply: a real "네" scored 0.41, and "잘 못 들었습니다" in
  answer to a yes is worse than any garbled word.
- Stricter than the data allows (0.35 / 0.70 caught 12 of 19), because a
  real "오늘 좀 힘들었어" sat exactly on that line and the person's voice is
  not the synthesised one. At 0.30 / 0.65 it catches 7 of 19 garbled clips,
  flags none of the 64 good ones and none of the partly-wrong sentences,
  with a 0.05 margin to the closest good sentence.
- Both numbers are in `config.yaml` (`unclear_lowest_word`,
  `unclear_average_word`), and every voice turn logs `[STT] Word
  confidence: lowest .., average ..` so they can be set from real use.

**What she does** (`ChatEngine._asks_to_hear_it_again`, the first thing
routing does, before the name repair): "잘 못 들었습니다. 다시 한번 말씀해
주시겠습니까?" / "Sorry, I didn't catch that. Could you say it again?".
Never twice in a row -- asked once and still unclear, the best reading is
answered, because a loop of "I didn't catch that" is worse than a guess. A
question she was already waiting on stays open, so the repeated answer
still reaches it. Typed turns are never judged.

**Checked with the real model**, through the app's own `SpeechToText` (no
microphone opened), on freshly noised clips:

| clip | least-sure / average | |
|---|---|---|
| the person's sentence, clean | 0.57 / 0.93 | answered |
| 오늘 좀 힘들었어, 5 dB | 0.35 / 0.64 | answered -- on the line |
| 간단한 걸로, 3 dB | 0.45 / 0.53 | answered |
| 네 / 감사합니다 | 0.74 / 0.97 | answered |
| "뉴욕 주일의 스포츠함에서 매네틴까지 11 얼마야?" (garbled) | 0.10 / 0.49 | **asked to hear it again** |
| "치즈 주문 좋고, 제일 좋은데" (garbled) | 0.26 / 0.66 | answered |
| "해수에서 슬프 당선까지 버스있어" (garbled) | 0.46 / 0.53 | answered |
| "개인들이 그 산에 파지는 있어" (garbled) | 0.54 / 0.67 | answered |

**The ceiling of this signal**: "해수에서 슬프 당선까지 버스있어" (garbled)
and "간단한 걸로" (correct) score almost exactly the same. No threshold on
word confidence separates them -- the check catches speech Whisper knows it
is guessing at, not speech it is confidently wrong about. Catching those
would need a reading of whether the sentence makes sense, which is a
different check -- section 5.

## 5. Speech the transcriber was sure of, and wrong

A person hearing "해수에서 슬프 당선까지 버스있어" knows at once that it means
nothing, whatever the transcriber's confidence says. That reading is the
check.

**Design** (`brain/sense_check.py`, `ChatEngine._does_not_make_sense`). The
resident model answers one narrow question -- could a person really have
said this? -- and code decides everything else:

- Only a spoken turn of two words or more whose average word probability
  is below 0.80 (`sense_check_below` in `config.yaml`). Clear speech
  averages 0.85-0.99 and is never second-guessed; a typed turn never
  reaches it.
- After the name repair: a slip it can settle ("빈천공항", "CLT") is settled
  rather than asked about from scratch, and a turn it has just corrected,
  or a question it is waiting on, is not judged.
- The judge is shown her last line (a bare fragment only makes sense as a
  reply to it) and never the person's own earlier turns -- see below.
- Only a clean `{"reads_as":"garbled"}` counts. A failed or odd reply
  answers the turn as heard: the check can add a question, never lose a
  turn.
- She repeats what she heard, so the person can tell the microphone
  misheard them rather than that she failed to understand:
  "\"해수에서 슬프 당선까지 버스있어\"라고 들었는데, 제가 잘못 들은 것
  같습니다. 다시 한번 말씀해 주시겠습니까?" / "I heard \"...\", and I don't
  think I caught it right. Could you say it again?" The once-in-a-row
  limit is shared with section 4's check: between them she never asks
  twice running.

**The instrument.** 36 real garbled transcripts (the turbo probe, the live
check, the small model's harvest), 27 borderline ones (a sound frame with
one or two odd words -- reported, not scored), and 205 that must never be
flagged: the probes' sound transcripts including the misheard-name
sentences, 38 casual fragments with the line they answer ("음 별로네",
"그러게 말이야", "the other one", "yeah no"), and all 122 dogfood arc turns in
both languages with the turns before them.

| judge (qwen3:8b, temperature 0) | garbled caught (of 36) | sound flagged (of 205) |
|---|---|---|
| "could a person have said it here", her line + their earlier turns | 22 | 4 |
| "do the words fit together", her line only | 16 | 0 |
| **"could a person have said it here", her line only** | **23** | **1** |
| a 0-10 score, any threshold | -- sound and garbled both cluster at 5 | |

- The four false flags were changes of subject read against the person's
  earlier turns ("오늘 날씨 어때?", "그 monitor 어때? 50달러 이하로 찾아줘").
  Not showing those turns fixed it.
- The one left is "인천공항에서 미국 시의틀까지 가는 내며 시간 걸려" -- in the
  app the name repair settles 시의틀 first, and the check does not run
  after a repair.
- The stricter wording caught nothing the chosen one missed, so asking
  when either says garbled adds a call and no catches.

**Unseen sentences.** Twenty new sentences, ten per language (English at
harsher noise: it came through 3 dB untouched), through the app's pipeline:

| | garbled asked about | sound asked about |
|---|---|---|
| Korean, section 4's word check | 7 of 12 | 1 of 17 |
| Korean, **with this check** | **10 of 12** | 1 of 17 (the same one) |
| English, section 4's word check | 1 of 2 | 0 of 42 |
| English, with this check | 1 of 2 | 0 of 42 |

Every unseen garble averaged below 0.80, so the gate lost none; only 4 of
the 59 sound transcripts reached the judge at all, and it flagged none of
them. The one sound sentence asked about was section 4's -- see Limits.

**Live, real models** -- clip -> the app's `SpeechToText` on turbo -> the
engine's routing with the resident qwen3:8b judging:

| said (noise) | heard | Elaina |
|---|---|---|
| 벨뷰에서 시애틀 다운타운까지 버스 있어? (3 dB) | 해수에서 슬프 당선까지 버스있어 (0.46 / 0.53) | **"해수에서 슬프 당선까지 버스있어"라고 들었는데, 제가 잘못 들은 것 같습니다. 다시 한번 말씀해 주시겠습니까?** |
| 베인브리지 섬에 카지노 있어? (3 dB) | 개인들이 그 산에 파지는 있어 (0.54 / 0.67) | **asked, the same way** |
| 시애틀 스타벅스 1호점 몇 시에 열어? (5 dB, unseen) | 시리프 타벅스 위로 쩌며 뒤에 열어 | **asked** |
| What time does the first Starbucks in Seattle open? (-5 dB, unseen) | What time does the first sub up since Seattle over? | **I heard "What time does the first sub up since...", and I don't think I caught it right. Could you say it again?** |
| 뉴욕 JFK 공항에서 맨해튼까지 택시비 얼마야? (3 dB) | 뉴욕 주일의 스포츠함에서 매네틴까지 11 얼마야? (0.10 / 0.49) | 잘 못 들었습니다. ... *(section 4, before this check)* |
| 오늘 좀 힘들었어 (5 dB) / 간단한 걸로 (3 dB) | the same (0.64 / 0.53 average) | answered -- judged, sense |
| 주말에 친구랑 등산 가기로 했어 (3 dB, unseen) | ...가기로 했죠. | answered -- judged, sense |
| the person's sentence, clean / 10 dB | 0.93 / 0.89 average | answered -- never judged |
| 헤드폰은 됐고, 내일 비 온대? (3 dB) | 치즈 주문 좋고, 제일 좋은데 | answered -- **missed** |
| 환율 지금 얼마야? (3 dB, unseen) | 한유 지금 너희 뭐야? | answered -- **missed** |

**Cost.** One short call, only on a doubtful spoken turn: 0.22 s median
over the 290-item run; 0.3-2.1 s in the live run, which shared the GPU with
the test suite and another program.

**Its ceiling.** A wrong sentence that reads right -- "치즈 주문 좋고, 제일
좋은데", "한유 지금 너희 뭐야?", "And I worked hard time on a student's
replay." Nothing that reads only the transcript can know those are wrong;
that would take the audio itself.

## Verified

- `tests/test_near_miss.py` (20): the slips, and what must not fire --
  including **every turn of every paired dogfood arc, in both languages,
  checked against the turns before it: none is read as a slip.**
- `tests/test_slip_question.py` (10): the whole exchange through the engine,
  both languages, the corrected question being looked up, a remembered
  slip, "no" undoing an assumption, and the misheard-name path.
- `tests/test_stt_listening_context.py` (9): the retry rule, the hint
  list's size, the read-back check, and what reaches the model (a fake
  stands in for it).
- `tests/test_permission_keeps_the_answer.py` (2): see below.
- `tests/test_heard_unclearly.py` (7), `tests/test_unclear_transcript.py`:
  section 4's rule, the transcriber setting it, and the engine asking once.
- `tests/test_sense_check.py` (19): section 5 -- the gate (typed, clear,
  one-word turns never judged), only a clean verdict counting, what the
  judge is shown, what she repeats back, both languages, never twice in a
  row (including right after section 4's check asked), a failed judge
  answering the turn, a repaired slip not judged, and the transcriber's
  average reaching the engine.

## Live demos, final code

Real `qwen3:8b`, real web search, each conversation on its own fresh
backend with an isolated runtime, typed through the WebSocket text channel.

**Korean slip:**

| You | Elaina |
|---|---|
| UW 유학생인데 CPT 신청하려고 해 | CPT 신청 관련 정보를 확인해 드릴 수 있습니다. ... |
| CBT 신청하려면 서류 뭐가 필요해? | **방금 CBT라고 하셨는데, 혹시 CPT 말씀이신가요?** (0.4 s) |
| 응 | *(searched)* CPT 신청 시 필요한 서류로는 학업 관련 직무 제안서, 회사 서명용 보고서, 학생 신분 증명서, 그리고 학교에서 요구하는 추가 서류가 있습니다. ... |
| 씨비티는 얼마나 걸려? | **CPT 말씀으로 이해했습니다.** *(searched)* CPT 신청 처리 시간은 ... 최소 15일 정도 소요됩니다. ... |
| 아니 CBT 맞아, 인지행동치료 말하는 거야 | 인지행동치료는 효과적인 치료 방법입니다. *(the CPT reading undone)* |

**English slip:**

| You | Elaina |
|---|---|
| I'm an international student at UW and I want to apply for CPT. | You'll need to complete the CPT application form and get your academic advisor's approval. ... |
| what documents do I need for CBT? | **You said CBT, did you mean CPT?** (0.4 s) |
| yes | *(looked up)* For CPT, you'll need your I-20 form, a signed offer letter from your employer, and a learning objective sheet. ... |

**A misheard Korean name** -- the second turn is what a transcriber gives
for 베인브리지 said aloud:

| You | Elaina |
|---|---|
| 베인브리지 섬에 카지노 있어? | *(searched)* Bainbridge Island에 카지노는 없습니다. ... |
| 배인브리지에서 시애틀까지 페리로 얼마나 걸려? | **베인브리지 말씀으로 이해했습니다.** *(searched)* ... 페리 여행은 약 34분 정도 걸립니다. ... |

(Her own transliteration of Seattle in that reply, "시에일", is the
separate reply-side problem already recorded in
KOREAN_SESSION_FINDINGS, not the transcriber.)

**Misheard inputs** -- real output of the app's transcriber, typed in as
if heard, each on a fresh isolated backend:

| Heard | Elaina |
|---|---|
| 빈천공항에서 미국 CLT까지 가는데 몇 시간 걸려? *(cold)* | **인천공항 말씀으로 이해했습니다.** Incheon 공항에서 CLT까지 비행 시간은 약 16시간 17분입니다. ... *(CLT kept as Charlotte)* |
| *(after "나 시애틀에 있는 UW 다니는 유학생이야")* the same | **인천공항 말씀으로 이해했습니다. 방금 CLT라고 하셨는데, 혹시 시애틀 말씀이신가요?** (0.5 s) |
| 응 | *(searched "travel time from Incheon International Airport to Seattle")* ... 약 10시간입니다. |
| 시리틀에서 뱅코버까지 차로 몇 시간이야 | **시애틀 말씀으로 이해했습니다. 밴쿠버 말씀으로 이해했습니다.** *(searched Seattle -> Vancouver)* |
| 김포공항에서 지지도까지 비행기로 얼마나 걸려. | **제주도 말씀으로 이해했습니다.** ... |
| 벨뷰에서 시에틀 다운타운까지 버스 있어. | **시애틀 말씀으로 이해했습니다.** ... |
| 로스앤젤리스의 공항에서 한인타운까지 어떻게 가? | **로스앤젤레스 말씀으로 이해했습니다.** 한인타운까지는 메트로로 이동할 수 있습니다. ... |
| 대통령 지지도가 요즘 어때? | *(left alone -- approval rating, not 제주도)* |
| 인산부한테 인산비날 가는 것이 있죠 | *(nothing to repair; she echoed it -- see below)* |

What these runs show that is *not* the transcriber, recorded so it is not
mistaken for success:

- Understanding the words is not answering well. Seattle -> Vancouver was
  searched correctly and answered "서연에서 보스턴까지 약 10시간"; for Gimpo
  -> Jeju the router's own paraphrase said "Incheon International
  Airport", and the answer followed it; the Bellevue bus answer lost its
  numbers to the value guard and said only the disclaimer.
- Her own spelling of places from English evidence is still wrong
  ("시에일로" for 시애틀) -- the reply-side problem in
  KOREAN_SESSION_FINDINGS.
- A transcript garbled past any single name was answered as if it made
  sense. Built since: sections 4 (the transcriber's own confidence) and 5
  (whether the sentence says anything).

## Found by the live demos

Each demo ran against its own fresh backend with an isolated runtime
(`ELAINA_RUNTIME_ROOT`), through the WebSocket text channel. Three things
only a live run could show, all fixed:

1. **A slip already in the conversation was never caught again.** After
   "CBT? -- 응", the same slip spelled in Hangul ("씨비티는 얼마나 걸려?")
   was skipped, because CBT was now part of the conversation. A confirmed
   slip is now remembered, keyed by its letters so cbt / CBT / 씨비티 are
   one slip, and applied with the spoken assumption; "아니 CBT 맞아" right
   after undoes it. The Hangul spelling also needed its particle taken off
   first (씨비티**는**).
2. **The corrected question was treated as a request for options.** The
   escalation reused was the one for "show me some", which declares a
   recommendation -- so the open recommendation took the search results as
   candidates, and "yes" was answered "That's done. CPT Application
   Process is the one I'd start with." It now escalates as evidence, not
   options.
3. **An older guard deleted the answer.** `_refuse_redundant_permission`
   read a one-sentence reply -- the CPT document list with an offer clause
   on the end -- as nothing but a permission question, and replaced all of
   it with "완료했습니다." Only a short reply is treated that way now; an
   answer with an offer attached is kept. This one predates today and
   could reach any searched answer.

## Limits

- Only terms the conversation holds are repaired. A misheard ordinary word
  ("회의" heard as "해외") is left to the router's own reading.
- A deliberate switch to a new acronym one rhyming letter from the held one
  is asked about once; answering "no" settles it for the session.
- Once the right term is settled, whether she searches is the router's
  decision, as for any other question.
- Held Korean names need to have come up twice (usually once from the
  person and once in her answer); a name said only once in passing is not
  held.
- Section 4's word check asked about one sound sentence it had never
  seen: "그 드라마 마지막에 봤어" at 5 dB (one word under 0.30, average 0.64).
  Rare, and she then answers the repeat, but it is the reason both
  thresholds should be set from the person's own `[STT] Word confidence`
  lines rather than the synthesised voice.
- Garbled speech that still reads as a sentence is answered (section 5's
  ceiling).
