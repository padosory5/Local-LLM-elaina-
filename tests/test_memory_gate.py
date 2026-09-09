"""A7: memory is a property of the sentence, not of the route.

The class that matters is :class:`TheIntentGateCouldNotSeeMostOfThemTests`
-- it is the measurement the phase exists because of. Both ends of a real,
working memory subsystem sat behind ``route.intent == "conversation"``, and
``conversation`` is one of twenty-four intents.
"""

import json
import re
import unittest
from pathlib import Path

from brain import guard_lines, memory_gate

MATRIX_PATH = Path(__file__).with_name("recall_matrix.json")


class TheMatrixTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

    def test_every_case_lands_where_the_matrix_says(self):
        expected = {
            "store":      ("carries_something_to_remember", True),
            "recall":     ("needs_what_we_know", True),
            "transient":  ("carries_something_to_remember", False),
            "machine":    ("needs_what_we_know", False),
            "forget":     ("asks_to_forget", True),
            "not_forget": ("asks_to_forget", False),
            "asking":     ("carries_something_to_remember", False),
        }
        for case in self.matrix["cases"]:
            name, want = expected[case["kind"]]
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    getattr(memory_gate, name)(case["text"]), want,
                    f"{case['id']}: {case['text']!r}",
                )

    def test_both_languages_are_covered(self):
        """Rule 4. A gate that only fires in English forgets in Korean."""
        korean = [
            case for case in self.matrix["cases"]
            if re.search(r"[가-힣]", case["text"])
        ]
        self.assertGreaterEqual(len(korean), 5)
        kinds = {case["kind"] for case in korean}
        self.assertTrue({"store", "recall", "transient", "forget"} <= kinds)


class TheIntentGateCouldNotSeeMostOfThemTests(unittest.TestCase):
    """The measurement this phase exists because of.

    Storing required ``route.intent == "conversation" and
    route.memory_candidate``; retrieval required the same intent and
    ``memory_relevant``. The turns most likely to carry a durable fact are
    the turns where someone is asking for something -- and a request is
    never routed as conversation.
    """

    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))["cases"]

    def test_most_turns_needing_memory_never_reached_it(self):
        needing = [
            case for case in self.cases if case["kind"] in ("store", "recall")
        ]
        reachable = [
            case for case in needing
            if case.get("likely_intent") == "conversation"
        ]
        # Three of sixteen, before the model's boolean narrowed it further.
        self.assertLessEqual(len(reachable), len(needing) // 4)

    def test_the_new_gate_reaches_all_of_them(self):
        for case in self.cases:
            if case["kind"] == "store":
                with self.subTest(case=case["id"]):
                    self.assertTrue(
                        memory_gate.carries_something_to_remember(case["text"])
                    )
            elif case["kind"] == "recall":
                with self.subTest(case=case["id"]):
                    self.assertTrue(
                        memory_gate.needs_what_we_know(case["text"])
                    )


class AMoodIsNotAProfileTests(unittest.TestCase):
    """Storing "I'm tired" as a fact about someone is how a memory
    becomes a caricature. The social dogfood arc is made of these."""

    def test_transient_states_are_never_stored(self):
        for said in (
            "i had a rough night",
            "kind of tired honestly",
            "I'm busy today",
            "yeah it was a long one",
            "I'm good thanks",
            "오늘 좀 피곤합니다",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    memory_gate.carries_something_to_remember(said), said,
                )

    def test_being_asked_outranks_the_mood_rule(self):
        """Being asked is different from being guessed."""
        self.assertTrue(
            memory_gate.carries_something_to_remember(
                "remember that I'm tired every Monday"
            )
        )

    def test_a_durable_fact_in_the_same_breath_still_counts(self):
        self.assertTrue(
            memory_gate.carries_something_to_remember(
                "I'm tired, and I'm allergic to shellfish"
            )
        )


class ContractionsAreNotUpToThePersonTests(unittest.TestCase):
    """Which of "I'm" and "I am" arrives is the transcriber's choice.

    Writing only the contraction lost "I am allergic to shellfish" --
    which is the capability registry's own example of what memory is for.
    """

    def test_both_spellings_are_the_same_sentence(self):
        for said in (
            "I'm allergic to shellfish",
            "I am allergic to shellfish",
            "i am allergic to shellfish",
        ):
            with self.subTest(said=said):
                self.assertTrue(memory_gate.carries_something_to_remember(said))


class TheirMachineIsNotTheirWorldTests(unittest.TestCase):
    """"my screen" is a screen-vision request. "my school" is a person."""

    def test_machine_possessives_do_not_load_memory(self):
        for said in (
            "what's on my screen",
            "can you control my browser?",
            "close my windows",
            "what does the router do in my project",
            "내 화면 좀 봐줘",
        ):
            with self.subTest(said=said):
                self.assertFalse(memory_gate.needs_what_we_know(said), said)

    def test_personal_possessives_do(self):
        for said in (
            "what's a good restaurant near my school?",
            "book something for my wife and me",
            "제 학교 근처 맛집 알려줘",
        ):
            with self.subTest(said=said):
                self.assertTrue(memory_gate.needs_what_we_know(said), said)


class ForgettingIsAnOperationTests(unittest.TestCase):

    def test_it_recognises_a_real_instruction(self):
        for said in (
            "forget what I told you about my school",
            "delete everything you know about me",
            "stop remembering that",
            "저에 대해 기억하는 거 잊어주세요",
        ):
            with self.subTest(said=said):
                self.assertTrue(memory_gate.asks_to_forget(said), said)

    def test_a_topic_change_is_not_an_instruction_to_the_memory(self):
        """A1 found a bare "forget X" could silently eat a turn."""
        for said in (
            "forget the mouse, what's a good film tonight?",
            "forget it",
            "never mind, forget that",
        ):
            with self.subTest(said=said):
                self.assertFalse(memory_gate.asks_to_forget(said), said)

    def test_the_sentences_exist_in_both_languages(self):
        for name in (
            "memory_forgotten", "memory_forgotten_all",
            "memory_nothing_to_forget",
        ):
            for language in ("en", "ko"):
                with self.subTest(line=name, language=language):
                    self.assertTrue(guard_lines.say(name, language).strip())

    def test_what_was_forgotten_is_named_not_counted(self):
        """"Forgotten -- 2 things" says nothing about whether the right
        two went, which is the whole difference between a visible
        operation and a silent one."""
        said = guard_lines.say("memory_forgotten", "en")
        self.assertIn("{what}", said)


class ThePatternsAreWhatTheyLookLikeTests(unittest.TestCase):

    def test_the_source_carries_no_control_characters(self):
        source = (
            Path(__file__).resolve().parents[1] / "brain" / "memory_gate.py"
        ).read_text(encoding="utf-8")
        stray = [
            (index, repr(char)) for index, char in enumerate(source)
            if ord(char) < 32 and char not in "\n\t"
        ]
        self.assertEqual(stray, [])

    def test_the_module_declares_its_languages(self):
        self.assertEqual(memory_gate.LANGUAGES, ("en", "ko"))


if __name__ == "__main__":
    unittest.main()


class NothingLeavesTheMachineTests(unittest.TestCase):
    """A7's privacy criterion, asserted rather than assumed.

    "Local-first storage -- privacy is a design constraint, not a
    setting." It is true today by construction: SQLite on disk, a FAISS
    index on disk, and sentence-transformers running locally. This test
    exists so that stays true -- a cloud sync added for Milestone D's
    shared memory would fail here rather than ship quietly.
    """

    PACKAGE = Path(__file__).resolve().parents[1] / "memory"

    NETWORK = re.compile(
        r"\brequests\.\w+|\burllib\b|\burlopen\b|\bhttpx\b|\baiohttp\b"
        r"|\bsocket\.\w+|\bboto3\b|\bopenai\b|\bapi_key\b"
        r"|https?://(?!schemas\.|www\.w3\.)",
    )

    def test_the_memory_package_makes_no_network_calls(self):
        offenders = []
        for path in sorted(self.PACKAGE.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            # Comments and docstrings may mention a URL; code may not.
            code = "\n".join(
                line for line in source.splitlines()
                if not line.lstrip().startswith("#")
            )
            found = self.NETWORK.search(code)
            if found:
                offenders.append(f"{path.name}: {found.group(0)}")
        self.assertEqual(
            offenders, [],
            "memory must stay local-first; nothing leaves the machine "
            "without the user asking",
        )

    def test_memory_is_stored_on_this_machine(self):
        from core.paths import FAISS_INDEX_PATH, MEMORY_DATABASE_PATH
        from memory.database import DATABASE_URL

        self.assertTrue(DATABASE_URL.startswith("sqlite:///"))
        self.assertFalse(str(MEMORY_DATABASE_PATH).startswith("http"))
        self.assertFalse(str(FAISS_INDEX_PATH).startswith("http"))


class DroppingANamedSubjectIsDeterministicTests(unittest.TestCase):
    """Found by re-running A3's own case four times: three passed, one
    failed with the abandoned recommendation leaking into the answer.

        actually forget the mouse, what's a good film for tonight?
        Enjoy the ride! The one I actually found is Best Wireless
        Gaming Mouse under $50.

    The clean start depended on the model setting ``topic_shift``, and a
    signal that is right two times in three is not a gate. The person
    named the subject they were dropping; that part is theirs and it is
    deterministic.
    """

    def test_it_reads_the_subject_being_dropped(self):
        from brain.deliberation import supersession

        for said, expected in (
            ("actually forget the mouse, what's a good film for tonight?", "mouse"),
            ("forget the mouse, what else is there", "mouse"),
            ("never mind the hotel, how is the weather", "hotel"),
            ("ok forget that laptop, show me phones", "laptop"),
        ):
            with self.subTest(said=said):
                self.assertEqual(supersession.drops_a_named_subject(said), expected)

    def test_a_bare_cancellation_is_not_this(self):
        """"forget it" is a cancellation and _CALLS_IT_OFF owns it."""
        from brain.deliberation import supersession

        for said in ("forget it", "never mind", "forget about that",
                     "forget the mouse."):
            with self.subTest(said=said):
                self.assertEqual(supersession.drops_a_named_subject(said), "")

    def test_a_memory_instruction_is_not_a_subject_drop(self):
        """The two "forget" shapes go to different places."""
        from brain.deliberation import supersession

        said = "forget what I told you about my school"
        self.assertEqual(supersession.drops_a_named_subject(said), "")
        self.assertTrue(memory_gate.asks_to_forget(said))

    def test_a_subject_drop_is_not_a_memory_instruction(self):
        said = "actually forget the mouse, what's a good film tonight?"
        self.assertFalse(memory_gate.asks_to_forget(said))
        self.assertTrue(
            __import__(
                "brain.deliberation.supersession", fromlist=["x"],
            ).drops_a_named_subject(said)
        )
