# What you did on the computer — and doing it again

Asked for before Milestone B: *"record everything I do and then later when I
say 'repeat the previous 5 actions that I did' she will do it, or 'Do you
remember what I did as soon as I turned on my PC? I want you to do those
actions or open those apps every time'."*

It is always on. There is nothing to start, stop or name.

| You say | She does |
|---|---|
| "repeat my last 5 actions" · "마지막 5개 다시 해줘" | lists the five, asks, and does them on your yes |
| "repeat what I just did" · "방금 한 거 다시 해줘" | the same, for the steps since your last two-minute pause |
| "what did I do after I turned on my PC?" · "컴퓨터 켜자마자 뭐 했지?" | lists the first things you did after the PC started, and offers to do them |
| "do those every time I turn on my PC" · "켤 때마다 이렇게 해줘" | keeps them as your start-up routine: **each time she starts, she offers them** ("평소처럼 해 드릴까요? …") and waits for a yes |
| "stop doing that at startup" · "부팅할 때 그거 하지 마" | forgets the routine |
| "what did I do this morning?" · "아까 본 사이트 뭐였지?" | reads the log back |
| "forget what I did today" · "오늘 한 거 지워줘" | deletes it |
| "pause the activity log" · "활동 기록 꺼 줘" | stops recording until you resume |

**Nothing is ever done on the turn that asks.** She lists the steps and asks;
only a yes on the next turn ("yes", "yeah, can you do that?", "응 해줘", "그래")
does anything. Anything else lets the offer lapse and the conversation goes
on. A step that cannot be undone — a click on 삭제/Delete/Send/Buy, or Enter in
a messenger — is marked "(되돌릴 수 없음)" / "(can't be undone)" in the list
before you answer.

## Why it changed: the first version, as you used it

The first version had you say "record this", "stop recording" and then name
the recording. Your live session showed everything wrong with that:

| What happened | Why | Now |
|---|---|---|
| "Record this" and "Stop Recording", heard exactly right, were each answered "Sorry, I didn't catch that" | the transcriber's word confidence was low (0.59, 0.40), and the unclear check did not know a short command when it saw one | a transcript that parses as one of her own commands, or as a yes/no to her question, was understood (`_reads_as_her_own_command`); speech-to-text itself was not touched |
| the second "didn't catch that" became **"Sure. Stop recording."** | the style layer saw a repeated line and had the model re-say it — and the model invented an action | a guard's fixed line is never reworded (`guard_lines.is_fixed_line`) |
| "Opening setting" (the name you gave) was asked about again as garbled | a name is not a sentence | there is no naming step |
| the steps read "switched to SearchHost — 검색", "turned “시작” on in File Explorer", "clicked in StartMenuExperienceHost" | the Windows shell was recorded as if it were the thing you did, and the Start button exposes a toggle | the shell is named plainly (Windows 검색, 시작 메뉴, 작업 표시줄) and compressed away in a repeat; "turned on" is said only of real switches and checkboxes |
| "Yeah, can you do that?" went to the unsupported-computer-action path | there was nothing to say yes to | she asks, and your yes is read as the answer |

## How a repeat works

1. **What to repeat** (`brain/replay_plan.py`). The log's rows become what a
   person would say they did. Opening Settings through Start and Search was
   seven rows; it is three steps — *설정 열기, “시스템” 클릭, “디스플레이” 클릭*.
   Pages become their address; a click is the control's **name**, never a
   screen position; typing is repeated only if its text is known.
2. **Doing it** (`brain/replay_runner.py`). Each step is found again on the live
   screen: an app by its process (store apps like Settings by their window
   title), a control by its name inside that window, a page by opening its
   address. An app that is not running is opened. Your "yes" turns Desktop
   Control Mode on for this run only, and it is put back afterwards.
3. **Stopping.** The moment you touch the mouse or keyboard she stops and says
   how far she got; at the first step she cannot do, she stops and says which.

What she keeps for this, and where:

- **The log on disk** (`runtime/database/activity.db`, 14 days): clicks, pages,
  app switches, settings, shortcuts — and "typed in “검색”" without the text.
- **In memory only, a few hours**: the same steps *with* what you typed, so
  "repeat what I just did" can type it again. Gone when she closes.
- **Your start-up routine** (`runtime/data/routines/startup.yaml`): yours to
  read or edit. Forgetting it moves the file into `forgotten/`.
- **Never**: a password field (it is never read), anything inside a private
  window beyond that one was used, Elaina's own window, anything inside a
  full-screen app (a game or a video is one row: "used … until 22:10").

All of it is covered by `.gitignore` and none of it leaves the machine.

## Verified

- `tests/test_replay.py` — the live Settings run compresses to three steps and
  reads the same in both languages; pages, typing and shortcuts; what cannot
  be repeated is said; risky steps marked; passing through windows is not
  going to them; the runner opens what is not open, brings forward what is,
  stops at a step it cannot do, stops when you touch the mouse, and only
  sends a risky click as confirmed.
- `tests/test_activity_turns.py` — through the engine: she lists and asks
  first and nothing touches the machine; a yes does it, in both languages; a
  no and a change of subject do nothing; where it stopped is said; Desktop
  Control Mode is on only for the run; the boot question, saving it as the
  start-up routine, the start-up offer waiting for a yes, and forgetting it;
  your own command heard at low confidence is not asked about again.
- `tests/test_activity_commands.py` — every form in both languages, the yes/no
  reader, and what must not be a command: "can you repeat that?", "say that
  again", "다시 해줘", "do it again", "record this", "컴퓨터 켜는 법 알려줘" —
  plus every dogfood arc turn.
- `tests/test_activity_recorder.py`, `tests/test_activity_store.py` — the
  recorder on a fake screen (typed text in memory, never on disk, never at
  all with `typed_text: never`) and the stores.

## Limits

- **I could not click for you to test it end to end.** Her own input is
  filtered out as "not the person" by design, so the recorder cannot be fed
  real clicks from here; the checks above use a fake screen. The first real
  run is yours: do something, then say "방금 한 거 다시 해줘".
- A click on something with no accessible name (inside a game, some Electron
  apps) is recorded, but cannot be repeated — she says so.
- Typing from more than a few hours ago, or from before she was restarted,
  cannot be repeated: the text was never written to disk.
- "What did I do after I turned on my PC?" only knows what happened while she
  was running; if she started later, she says when she started watching.
- A protected game refuses UI Automation entirely (measured: ZenlessZoneZero
  answers "access denied"). Full-screen apps are never looked into anyway.
