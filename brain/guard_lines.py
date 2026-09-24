"""Sentences the guards say, in the language of the turn.

Most of what Elaina says is generated, and the language layer decides what
language that is. But a guard does not generate -- it *replaces*. When the
grounded-value guard removes an unverified price it puts its own sentence
there, and that sentence was written in English by whoever wrote the guard.

Measured across every bilingual live run: 4 of 62 Korean replies carried an
English sentence, and all four were the same one --

    1080p 해상도와 5ms 응답 시간으로 게임 및 일상 사용에 적합합니다.
    I looked and couldn't find that, so I'd rather not guess.

A reply in two languages is worse than a reply in the wrong one: the wrong
language is a bug you notice, and half a reply in the wrong language reads
as a fault in her rather than in the software.

Deliberately a table and not a translator. These lines carry consent
meaning -- an offer parked here is classified against its own words later --
so each one is written once, in both languages, by someone who read what it
is for. Anything missing falls back to English rather than to nothing.
"""

from __future__ import annotations


LANGUAGES = ("en", "ko")


ENGLISH = "en"
KOREAN = "ko"

# Keyed by an identifier, not by the English text, so the English wording
# can be improved without silently orphaning the Korean.
LINES: dict[str, dict[str, str]] = {
    # The grounded-value guard removed a value nothing had verified, and
    # browser control is available to go and check it properly.
    "unverified_offer_searched": {
        ENGLISH: "I looked and couldn't find that -- want me to open the "
                 "site and check properly?",
        KOREAN: "찾아봤지만 확인되지 않았습니다. 사이트를 열어서 직접 확인해 "
                "드릴까요?",
    },
    "unverified_offer_unsearched": {
        ENGLISH: "I haven't actually checked that -- want me to look it up?",
        KOREAN: "아직 확인해 보지 않았습니다. 지금 찾아볼까요?",
    },
    # Only part of the answer was dropped -- the sentence carrying a number
    # nothing verified -- and the rest still stands. The whole-answer lines
    # below read as a contradiction there. Measured in Korean: "...가장 많이
    # 시청된 드라마는 '죄와 사랑'입니다. 찾아봤지만 확인되지 않아서, 추측으로
    # 말씀드리지는 않겠습니다." -- a claim, then "I couldn't confirm it".
    "unverified_figure_searched": {
        ENGLISH: "I looked, but couldn't confirm the specific number, so "
                 "I've left it out.",
        KOREAN: "찾아봤지만 구체적인 숫자는 확인되지 않아서 빼고 "
                "말씀드렸습니다.",
    },
    "unverified_figure_unsearched": {
        ENGLISH: "I haven't checked the specific number, so I've left it out.",
        KOREAN: "구체적인 숫자는 아직 확인하지 않아서 빼고 말씀드렸습니다.",
    },
    # The same, with no way to check: she says so and stops.
    "unverified_searched": {
        ENGLISH: "I looked and couldn't find that, so I'd rather not guess.",
        KOREAN: "찾아봤지만 확인되지 않아서, 추측으로 말씀드리지는 않겠습니다.",
    },
    "unverified_unsearched": {
        ENGLISH: "I haven't actually checked that, so I'd rather not guess.",
        KOREAN: "아직 확인해 보지 않아서, 추측으로 말씀드리지는 않겠습니다.",
    },
    # Retired: "I couldn't verify a specific one from the sources I
    # checked." It was true and it was useless -- it told the person
    # nothing they could act on, and they said so. What replaced it says
    # which of the two things happened, because the guard knows: the search
    # came back empty, or it came back with pages that name nothing
    # ("no_listing_names", below, which also offers to go and read them).
    "found_nothing_usable": {
        ENGLISH: "That search came back empty -- nothing in it to name. "
                 "Want me to try different words?",
        KOREAN: "검색 결과가 비어 있어서 이름을 확인할 수 없었습니다. 다른 "
                "표현으로 다시 찾아볼까요?",
    },
    # The search ran but produced no usable names, and browser control
    # could go and read them off the page properly.
    "no_listing_names": {
        ENGLISH: "I couldn't get actual listing names out of that search -- "
                 "want me to open it in the browser and read them off?",
        KOREAN: "검색 결과에서 실제 이름을 확인하지 못했습니다. 브라우저로 "
                "직접 열어서 읽어 드릴까요?",
    },
    # She was about to name somewhere to go that nothing had verified.
    "unchecked_place_offer": {
        ENGLISH: "I don't want to send you somewhere I haven't checked -- "
                 "want me to look up real ones?",
        KOREAN: "확인하지 않은 곳을 알려 드리고 싶지는 않습니다. 실제로 "
                "찾아볼까요?",
    },
    # The same guard, when what she named was not a place to go -- a drama,
    # a film. "Send you somewhere" after a drama recommendation was the
    # place line said about the wrong kind of thing.
    "unchecked_name_offer": {
        ENGLISH: "I don't want to recommend something I haven't checked -- "
                 "want me to look up real ones?",
        KOREAN: "확인하지 않은 것을 추천해 드리고 싶지는 않습니다. 실제로 "
                "찾아볼까요?",
    },
    # She was about to say a thing does not exist, having checked nothing.
    # Measured live: asked about 육이오 전쟁 -- the Sino-Korean reading of
    # the date the Korean War began -- she said it never happened, and then
    # described the war correctly one turn later. The model's recall gap
    # was spoken as a fact about the world.
    "unchecked_denial": {
        ENGLISH: "I don't actually remember that one, and I'd rather not "
                 "tell you it never happened without checking. Want me to "
                 "look it up?",
        KOREAN: "그건 제가 확실히 알지 못합니다. 확인 없이 아니라고 "
                "말씀드리기는 어려우니, 한번 찾아볼까요?",
    },
    # They made a noise while thinking and have not asked anything yet.
    # Short on purpose: a filler usually has the real sentence right
    # behind it, and a long answer talks over it.
    "still_listening": {
        ENGLISH: "I'm listening.",
        KOREAN: "네, 듣고 있습니다.",
    },
    # A clip the transcriber itself could barely decode
    # (voice/transcription_policy.heard_unclearly). Answering it would be
    # answering words nobody said.
    "didnt_catch": {
        ENGLISH: "Sorry, I didn't catch that. Could you say it again?",
        KOREAN: "잘 못 들었습니다. 다시 한번 말씀해 주시겠습니까?",
    },
    # A transcript the transcriber was sure of whose words do not fit
    # together (brain/sense_check.py). She says what she heard, so the
    # person can tell the microphone misheard them -- not that she failed
    # to understand. Formatted with heard=.
    "heard_as_nonsense": {
        ENGLISH: "I heard \"{heard}\", and I don't think I caught it right. "
                 "Could you say it again?",
        KOREAN: "\"{heard}\"라고 들었는데, 제가 잘못 들은 것 같습니다. "
                "다시 한번 말씀해 주시겠습니까?",
    },
    # They asked about a detail of their own life that they never told her
    # (brain/memory_gate.asks_for_a_personal_detail). Fixed, because asked
    # to say so, the model said instead "제일 좋아하는 색깔은
    # 파랑이었습니다" -- a fact about someone that nobody said.
    # They told her something about their life and the focused
    # acknowledgement did not show it understood (brain/told_not_asked.py).
    "noted_fact": {
        ENGLISH: "Got it. I'll remember that.",
        KOREAN: "알겠습니다. 기억해 두겠습니다.",
    },
    # "Open it." with nothing yet said to point at.
    "refers_to_nothing": {
        ENGLISH: "Which one do you mean? I'm not sure what you're referring to yet.",
        KOREAN: "어떤 것을 말씀하시는지 아직 모르겠습니다. 무엇을 말씀하시는 건가요?",
    },
    "not_told_yet": {
        ENGLISH: "You haven't told me that yet. Tell me and I'll remember it.",
        KOREAN: "아직 말씀해 주신 적이 없습니다. 알려 주시면 기억하겠습니다.",
    },
    # Recording what the person does (brain/activity_commands.py,
    # tools/screen_control/activity_recorder.py). {name}, {count},
    # {steps}, {names}, {when}, {items}, {more} as named.
    "recording_started": {
        ENGLISH: "Recording. Go ahead and I'll remember what you do. Say "
                 "\"stop recording\" when you're done.",
        KOREAN: "녹화를 시작합니다. 하시는 대로 기억하겠습니다. 끝나면 "
                "\"녹화 그만\"이라고 말씀해 주십시오.",
    },
    "recording_started_named": {
        ENGLISH: "Recording \"{name}\". Go ahead, and say \"stop recording\" "
                 "when you're done.",
        KOREAN: "\"{name}\" 녹화를 시작합니다. 끝나면 \"녹화 그만\"이라고 "
                "말씀해 주십시오.",
    },
    "recording_already": {
        ENGLISH: "I'm already recording. Say \"stop recording\" when you're done.",
        KOREAN: "이미 녹화하고 있습니다. 끝나면 \"녹화 그만\"이라고 말씀해 주십시오.",
    },
    "recording_not_active": {
        ENGLISH: "I'm not recording anything right now.",
        KOREAN: "지금은 녹화하고 있지 않습니다.",
    },
    "recording_empty": {
        ENGLISH: "I didn't see you do anything while I was recording, so "
                 "there's nothing to save.",
        KOREAN: "녹화하는 동안 아무 동작도 보지 못해서 저장할 것이 없습니다.",
    },
    "recording_ask_name": {
        ENGLISH: "Got it, {count} steps. What should I call it?",
        KOREAN: "{count}단계를 기억했습니다. 어떤 이름으로 저장하면 되겠습니까?",
    },
    "recording_saved": {
        ENGLISH: "Saved \"{name}\", {count} steps: {steps}.",
        KOREAN: "\"{name}\" 녹화를 저장했습니다. 모두 {count}단계입니다: {steps}.",
    },
    "recording_replaced": {
        ENGLISH: "Saved \"{name}\" again, replacing the old one. {count} "
                 "steps: {steps}.",
        KOREAN: "\"{name}\" 녹화를 새 녹화로 바꿨습니다. 모두 {count}단계입니다: "
                "{steps}.",
    },
    "recording_cancelled": {
        ENGLISH: "Okay, I won't save that recording.",
        KOREAN: "알겠습니다. 이번 녹화는 저장하지 않겠습니다.",
    },
    "routine_list": {
        ENGLISH: "You have {count} recordings: {names}.",
        KOREAN: "저장된 녹화는 {count}개입니다: {names}.",
    },
    "routine_list_empty": {
        ENGLISH: "You haven't recorded anything yet. Say \"record this\" and "
                 "I'll remember what you do.",
        KOREAN: "아직 저장된 녹화가 없습니다. \"이거 녹화해 줘\"라고 말씀하시면 "
                "하시는 동작을 기억하겠습니다.",
    },
    "routine_describe": {
        ENGLISH: "\"{name}\" has {count} steps: {steps}.",
        KOREAN: "\"{name}\" 녹화는 모두 {count}단계입니다: {steps}.",
    },
    # Asked to do a recording before Milestone B gives her the hands for
    # it: she says what she remembers instead of pretending.
    "routine_replay_later": {
        ENGLISH: "I remember \"{name}\", {count} steps: {steps}. I can't do "
                 "it for you yet; repeating a recording comes with the next "
                 "milestone.",
        KOREAN: "\"{name}\" 녹화를 기억하고 있습니다. 모두 {count}단계입니다: "
                "{steps}. 아직은 제가 직접 따라 할 수 없고, 다음 마일스톤에서 "
                "가능해집니다.",
    },
    "routine_forgotten": {
        ENGLISH: "I've forgotten \"{name}\". Its file is in routines/forgotten "
                 "if you want it back.",
        KOREAN: "\"{name}\" 녹화를 지웠습니다. 되돌리려면 routines/forgotten "
                "폴더에 파일이 남아 있습니다.",
    },
    "activity_recall": {
        ENGLISH: "Here's what you did {when}: {items}.",
        KOREAN: "{when} 하신 일입니다: {items}.",
    },
    "activity_recall_more": {
        ENGLISH: " There are {more} more before that.",
        KOREAN: " 그 전에도 {more}건이 더 있습니다.",
    },
    "activity_recall_empty": {
        ENGLISH: "I don't have anything recorded {when}.",
        KOREAN: "{when} 기록된 활동이 없습니다.",
    },
    "activity_forgotten": {
        ENGLISH: "Done. I deleted {count} records of what you did {when}.",
        KOREAN: "{when} 활동 기록 {count}건을 삭제했습니다.",
    },
    "activity_forgotten_all": {
        ENGLISH: "Done. I deleted your whole activity log, {count} records.",
        KOREAN: "활동 기록 전체 {count}건을 삭제했습니다.",
    },
    "activity_paused": {
        ENGLISH: "Okay, I've stopped keeping track of what you do. Say "
                 "\"resume the activity log\" when you want me to start again.",
        KOREAN: "알겠습니다. 이제부터 하시는 일을 기록하지 않겠습니다. 다시 "
                "원하시면 \"활동 기록 다시 시작해\"라고 말씀해 주십시오.",
    },
    "activity_resumed": {
        ENGLISH: "I'm keeping track of what you do again.",
        KOREAN: "다시 하시는 일을 기록하겠습니다.",
    },
    "activity_unavailable": {
        ENGLISH: "I can't see what you do on the computer right now: activity "
                 "recording is switched off, or Windows didn't allow it.",
        KOREAN: "지금은 컴퓨터에서 하시는 일을 볼 수 없습니다. 활동 기록이 꺼져 "
                "있거나 Windows에서 허용되지 않았습니다.",
    },
    # Doing again what the person did (brain/replay_plan.py,
    # brain/replay_runner.py). Always listed first, done only on a yes.
    # {what} is "Your last 5 actions" / "최근 5개 동작".
    "replay_offer": {
        ENGLISH: "{what}: {steps}. Shall I do them now?",
        KOREAN: "{what}: {steps}. 지금 그대로 해 드릴까요?",
    },
    "replay_skipped": {
        ENGLISH: " I can't repeat these, though: {skipped}.",
        KOREAN: " 다만 이건 다시 할 수 없습니다: {skipped}.",
    },
    "replay_nothing": {
        ENGLISH: "I don't have anything you did lately that I could repeat.",
        KOREAN: "최근에 하신 일 중에 다시 해 드릴 수 있는 것이 없습니다.",
    },
    "replay_done": {
        ENGLISH: "Done: {steps}.",
        KOREAN: "다 했습니다: {steps}.",
    },
    "replay_failed": {
        ENGLISH: "I did {done} of {total} steps, then couldn't do this one: "
                 "{step}. {reason}",
        KOREAN: "{total}단계 중 {done}단계까지 했고, 이 단계에서 멈췄습니다: {step}.",
    },
    "replay_interrupted": {
        ENGLISH: "You took over, so I stopped after {done} of {total} steps.",
        KOREAN: "직접 조작하셔서 {total}단계 중 {done}단계까지 하고 멈췄습니다.",
    },
    "replay_declined": {
        ENGLISH: "Okay, I won't.",
        KOREAN: "알겠습니다. 하지 않겠습니다.",
    },
    "replay_unavailable": {
        ENGLISH: "I can't use the mouse and keyboard on this computer, so I "
                 "can't do those for you.",
        KOREAN: "이 컴퓨터에서는 마우스와 키보드를 쓸 수 없어서 대신 해 드릴 수 "
                "없습니다.",
    },
    "boot_actions": {
        ENGLISH: "Your PC started at {time}. The first things you did were: "
                 "{steps}. Want me to do them now?",
        KOREAN: "{time}에 컴퓨터를 켜신 뒤 처음 하신 일은 이렇습니다: {steps}. "
                "지금 그대로 해 드릴까요?",
    },
    "boot_actions_late": {
        ENGLISH: "I started keeping track at {time}, after your PC was already "
                 "on. The first things you did after that were: {steps}. Want "
                 "me to do them now?",
        KOREAN: "컴퓨터가 켜진 뒤 {time}부터 기록했습니다. 그 뒤 처음 하신 일은 "
                "이렇습니다: {steps}. 지금 그대로 해 드릴까요?",
    },
    "boot_actions_none": {
        ENGLISH: "I don't have anything recorded from after your PC started.",
        KOREAN: "컴퓨터를 켠 뒤의 기록이 없습니다.",
    },
    "startup_saved": {
        ENGLISH: "Got it. Whenever I start up, I'll offer to do these: {steps}.",
        KOREAN: "알겠습니다. 앞으로 제가 켜질 때마다 이렇게 해 드릴지 "
                "여쭤보겠습니다: {steps}.",
    },
    "startup_nothing": {
        ENGLISH: "Which actions? Ask me what you did after turning on your PC, "
                 "or to repeat your last few actions, and then tell me to do "
                 "those every time.",
        KOREAN: "어떤 동작인지 먼저 알려 주십시오. 컴퓨터를 켠 뒤 뭘 했는지 "
                "물어보시거나 최근 동작을 다시 해 달라고 하신 다음, 매번 그렇게 "
                "해 달라고 하시면 됩니다.",
    },
    "startup_forgotten": {
        ENGLISH: "Okay, I won't offer that when I start anymore.",
        KOREAN: "알겠습니다. 이제 켜질 때 그 제안은 하지 않겠습니다.",
    },
    "startup_offer": {
        ENGLISH: "Welcome back. Want me to do your usual start-up? {steps}.",
        KOREAN: "다시 오셨군요. 평소처럼 해 드릴까요? {steps}.",
    },
    # A word in the turn that is almost what the conversation is about
    # (brain/near_miss.py). Formatted with heard=, meant= and, in Korean,
    # quote= (라고 / 이라고).
    "slip_question": {
        ENGLISH: "You said {heard} -- did you mean {meant}?",
        KOREAN: "방금 {heard}{quote} 하셨는데, 혹시 {meant} 말씀이신가요?",
    },
    "slip_assumed": {
        ENGLISH: "I took that as {meant}.",
        KOREAN: "{meant} 말씀으로 이해했습니다.",
    },
    # "How's it going?" answered from state she holds. Recognised in both
    # languages, answered only in English until these.
    "progress_working": {
        ENGLISH: "Still on it -- give me a moment.",
        KOREAN: "아직 하고 있습니다. 잠시만 기다려 주십시오.",
    },
    "progress_waiting": {
        ENGLISH: "I haven't started; I was waiting for you to say go.",
        KOREAN: "아직 시작하지 않았습니다. 하라고 말씀해 주시길 기다리고 "
                "있었습니다.",
    },
    "progress_idle": {
        ENGLISH: "Nothing's running right now. Want me to start it?",
        KOREAN: "지금은 진행 중인 작업이 없습니다. 시작할까요?",
    },
    # A promise with no ability behind it was removed, and something has to
    # stand where it was. An English literal in chat_engine until a Korean
    # "그렇구나" was answered with it.
    "next_step_question": {
        ENGLISH: "What would you like me to do next?",
        KOREAN: "다음으로 무엇을 해 드릴까요?",
    },
    # The final check regenerated a reply that repeated the last answer, and
    # the regeneration repeated it too. This was an English literal inside
    # chat_engine, so a Korean conversation heard it in English.
    "answered_wrong_thing": {
        ENGLISH: "Sorry -- I answered the wrong thing there. Say it once "
                 "more and I'll take it properly?",
        KOREAN: "죄송합니다. 엉뚱한 답을 드렸습니다. 한 번만 더 말씀해 "
                "주시겠습니까?",
    },
    # A standing instruction, written down and acknowledged. These were
    # f-strings in English inside chat_engine, so a Korean "다음부턴 그렇게
    # 설명해라" -- once it was recognised at all -- would have been answered
    # "Alright -- I'll 그렇게 설명해라 from now on." Korean puts the verb
    # last and the note is already an imperative, so the Korean lines do not
    # quote it back; they say what she will do.
    "standing_repair": {
        ENGLISH: "Got it -- from now on {first} means {second}.",
        KOREAN: "알겠습니다. 앞으로 '{first}'는 '{second}'로 알아듣겠습니다.",
    },
    "standing_fact": {
        ENGLISH: "Noted -- I'll keep that.",
        KOREAN: "알겠습니다. 기억해 두겠습니다.",
    },
    "standing_note": {
        ENGLISH: "Alright -- I'll {first} from now on.",
        KOREAN: "알겠습니다. 앞으로 그렇게 하겠습니다.",
    },
    "standing_forget": {
        ENGLISH: "Done -- I've dropped what I had about {first}.",
        KOREAN: "알겠습니다. '{first}'에 대한 내용은 지웠습니다.",
    },
    # Nothing came back from the model at all.
    "no_response": {
        ENGLISH: "I couldn't generate a response. Please try again.",
        KOREAN: "응답을 만들지 못했습니다. 다시 한번 말씀해 주십시오.",
    },
    # Answers to "can you...?", built from the registry rather than by the
    # model -- which is the point of that path and also what hid the gap.
    # A deterministic answer is a *correct* answer in the wrong language,
    # every time, and nothing in a green suite says so. Measured live:
    #
    #     You said: 깃 커밋 할 수 있어?
    #     Elaina:   Yes. I can prepare a commit for this project.
    #               Want me to use it now?
    #
    # ``{ability}`` is Capability.spoken_summary_in(language);
    # ``{name}`` is Capability.name_in(language); ``{reason}`` and
    # ``{fix}`` come from the registry's requirement tables.
    "ability_yes": {
        ENGLISH: "Yes. I can {ability}. {offer}",
        KOREAN: "네, {ability}. {offer}",
    },
    "ability_offer": {
        ENGLISH: "Want me to use it now?",
        KOREAN: "지금 해 드릴까요?",
    },
    "ability_blocked": {
        ENGLISH: "Yes, I have {name} -- but {reason}.",
        KOREAN: "{name} 기능은 있습니다. 다만 {reason}.",
    },
    "ability_blocked_fix": {
        ENGLISH: "{fix} and I'll use it.",
        KOREAN: "{fix} 바로 사용하겠습니다.",
    },
    # A doubt about an ability is a question about it, not a task to run.
    "ability_doubted": {
        ENGLISH: "I do have {name} -- I can {ability}. "
                 "Give me something to try it on and we'll see.",
        KOREAN: "{name} 기능은 분명히 있습니다. {ability}. "
                "한번 해 볼 대상을 주시면 확인해 보겠습니다.",
    },
    # Forgetting, said out loud. Before A7 there was no way to delete a
    # memory at all, so "forget what I told you about my school" changed
    # nothing and she carried on knowing it. A forget that reports
    # nothing is indistinguishable from a forget that did nothing, which
    # is why {what} is named rather than counted.
    # One named thing removed: their own words are the confirmation, and
    # reading the stored row back adds nothing a person would say.
    "memory_forgotten_one": {
        ENGLISH: "Forgotten.",
        KOREAN: "지웠습니다.",
    },
    "memory_forgotten": {
        ENGLISH: "Forgotten -- {what}.",
        KOREAN: "지웠습니다 -- {what}.",
    },
    "memory_forgotten_all": {
        ENGLISH: "I've cleared everything I had about you.",
        KOREAN: "기억하고 있던 내용을 모두 지웠습니다.",
    },
    "memory_nothing_to_forget": {
        ENGLISH: "I don't have anything saved about that.",
        KOREAN: "그와 관련해 저장된 내용은 없습니다.",
    },
    # Two retrieved sources give different numbers for the same attribute.
    # Said only when the reply actually states that attribute -- a
    # disagreement about something she never mentioned is not worth a
    # sentence, and saying it anyway is how honesty becomes the disclaimer
    # footer A1 spent effort removing.
    # Asked for a calculation twice and given no number twice. The value
    # is not guessed here -- it is computed by the same sandboxed evaluator
    # the calculation planner uses -- so what is missing is only a sentence
    # to say it in.
    "calculated_result": {
        ENGLISH: "{expression} is {value}.",
        KOREAN: "{expression}은(는) {value}입니다.",
    },
    "sources_disagree": {
        ENGLISH: "Sources disagree on that one -- another says {other}.",
        KOREAN: "자료마다 다릅니다. 다른 곳에서는 {other}(으)로 나옵니다.",
    },
    # How a multi-step task reports itself when it did not succeed. The
    # planner used to speak the model's own final summary, so a run whose
    # last step said "Pressed play; nothing started." was reported to the
    # person as "Done." -- see brain/task_progress.py. ``{done}`` sits
    # after a dash in both languages so no particle has to agree with it.
    "task_incomplete": {
        ENGLISH: "I didn't get that finished.",
        KOREAN: "그 작업을 끝내지 못했습니다.",
    },
    "task_progress": {
        # The full stop belongs to the frame: the fragments are trimmed of
        # their own, so that a list of them does not read "Spotify is
        # open., Liked Songs is open."
        ENGLISH: "This much is done -- {done}.",
        KOREAN: "여기까지는 되어 있습니다 -- {done}.",
    },
    "task_cancelled": {
        ENGLISH: "You took control, so I stopped.",
        KOREAN: "직접 조작하셔서 멈췄습니다.",
    },
    "task_nothing_done": {
        ENGLISH: "Nothing changed.",
        KOREAN: "바뀐 것은 없습니다.",
    },
}


def _template_pattern(template: str):
    import re

    parts = re.split(r"\{[a-z_]+\}", template)
    return re.compile(
        "^" + "(.+?)".join(re.escape(part) for part in parts) + "$", re.DOTALL,
    )


_FIXED: list = []


def is_fixed_line(text: str) -> bool:
    """Whether a reply is one of these lines, filled in or not.

    Such a line is exactly what the guard decided to say. The style layer
    must never hand it to the model to be said "in her own voice": measured
    live, "Sorry, I didn't catch that. Could you say it again?", said a
    second time, was flagged as repetition and re-said as "Sure. Stop
    recording." -- a sentence claiming something she had not done.
    """
    said = " ".join(str(text or "").split())
    if not said:
        return False
    if not _FIXED:
        for entry in LINES.values():
            for template in entry.values():
                _FIXED.append(_template_pattern(" ".join(template.split())))
    return any(pattern.match(said) for pattern in _FIXED)


def say(name: str, language: str = ENGLISH) -> str:
    """The guard line for this identifier, in this language.

    Falls back to English rather than to an empty string: a guard that
    goes silent removes a value and says nothing about it, which is the
    one outcome none of these may produce.
    """
    line = LINES.get(str(name or ""))
    if not line:
        return ""
    wanted = str(language or "").strip().lower()
    for key in (wanted, wanted[:2], ENGLISH):
        if key in line:
            return line[key]
    return ""
