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
    # The same, with no way to check: she says so and stops.
    "unverified_searched": {
        ENGLISH: "I looked and couldn't find that, so I'd rather not guess.",
        KOREAN: "찾아봤지만 확인되지 않아서, 추측으로 말씀드리지는 않겠습니다.",
    },
    "unverified_unsearched": {
        ENGLISH: "I haven't actually checked that, so I'd rather not guess.",
        KOREAN: "아직 확인해 보지 않아서, 추측으로 말씀드리지는 않겠습니다.",
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
