"""The turn where the subject stopped being a thing.

One live session, three turns, and a failure that compounded at every step:

    You said: I'm thinking about getting a new monitor.
    Elaina:   That sounds fun! Are you looking for a specific size or type
              of monitor, like a gaming one or something for work?

    You said: like a gaming one but I do work with coding sometimes so can
              you find me a good one for it?
    [Active Task] Subject: good
    [Query]       source: active_task  text: good
    [Tool]        Searching web for: good
    [Recommendation Reasoning] Candidates: 4 (0 fit, 3 unchecked)
    Elaina:   The one I actually found is GOOD Definition & Meaning,
              Merriam-Webster.

    You said: I was talking about getting me a new monitor. Where is
              Merriam-West Webster?
    [Conversation State] Correction applied: getting me a new monitor.
                         Where is Merriam-West Webster
    Elaina:   Merriam-Webster is based in Springfield, Massachusetts.

The router read every turn correctly -- turn two arrived as "find a good
monitor for gaming and coding". Four separate layers then took it apart:

* a preference reader that accepted "a good one" as the thing being
  discussed, so an adjective replaced a monitor as the subject;
* a query builder that put the classifier's filing label
  ("monitor purchase consideration") into a search box;
* a result classifier that let a dictionary entry rank as a candidate;
* a correction reader whose subject ran past the end of its own sentence,
  so "I was talking about getting me a new monitor" could not put any of
  it right.

Each of those is a property here. None of them needs a model.
"""

from __future__ import annotations

import unittest

from brain import acquisition, conversation_focus
from brain import recommendation_state as rs
from brain.task_session import TaskSessionStore
from tests.turn_harness import build_engine


MUSING = "I'm thinking about getting a new monitor."
ANSWER = (
    "like a gaming one but I do work with coding sometimes so can you "
    "find me a good one for it?"
)
TOPIC = "monitor purchase consideration"


def _problem_after_both_turns():
    store = TaskSessionStore()
    store.note_recommendation_turn(MUSING, subject=TOPIC)
    return store.note_recommendation_turn(ANSWER, subject=TOPIC)


class AProFormIsNotAThingTests(unittest.TestCase):
    """"A good one" points back at the monitor. It is not a new subject."""

    def test_a_bare_quality_never_becomes_the_preference(self):
        found = rs.read_constraints("can you find me a good one for it?")

        self.assertEqual(
            [slot.name for slot in found if slot.name == rs.PREFERENCE],
            [],
            "an adjective was accepted as the thing being asked for",
        )

    def test_a_real_thing_still_becomes_the_preference(self):
        found = rs.read_constraints("I want a mechanical keyboard")

        self.assertIn(
            "mechanical keyboard",
            [slot.value for slot in found if slot.name == rs.PREFERENCE],
        )

    def test_what_counts_as_naming_a_thing(self):
        for phrase, expected in (
            ("a good one", False),
            ("one", False),
            ("some", False),
            ("the best ones", False),
            ("new monitor", True),
            ("mechanical keyboard", True),
            ("Korean BBQ", True),
            ("", False),
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(rs.names_a_thing(phrase), expected)

    def test_the_subject_survives_the_follow_up(self):
        problem = _problem_after_both_turns()

        self.assertNotEqual(problem.subject.casefold(), "good")
        self.assertEqual(problem._thing(), "monitor")

    def test_the_search_is_about_the_monitor(self):
        problem = _problem_after_both_turns()

        query = problem.search_query("find a good monitor for gaming and coding")

        self.assertIn("monitor", query.casefold())
        self.assertNotEqual(query.casefold().strip(), "good")


class TheAnswerToHerQuestionReachesTheQueryTests(unittest.TestCase):
    """She asked "gaming or work?" and the answer has to count for something."""

    def test_a_qualified_pro_form_is_a_quality_of_the_thing(self):
        found = rs.read_constraints("like a gaming one")

        self.assertIn(
            "gaming",
            [slot.value for slot in found if slot.name == rs.ATTRIBUTE],
        )

    def test_a_position_is_not_a_quality(self):
        # "the last one" picks a member out of a set. Searching for a "last
        # monitor" finds nothing, and resolving it belongs elsewhere.
        for said in (
            "the last one", "give me the second one", "open the other one",
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    [
                        slot.value for slot in rs.read_constraints(said)
                        if slot.name == rs.ATTRIBUTE
                    ],
                    [],
                )

    def test_the_query_carries_what_they_said_they_wanted(self):
        problem = _problem_after_both_turns()

        query = problem.search_query("find a good monitor for gaming and coding")

        self.assertIn("gaming", query.casefold())


class AFilingLabelIsNotASearchTermTests(unittest.TestCase):
    """The router names a topic like a folder. Nobody searches like that."""

    def test_the_label_words_come_out(self):
        self.assertEqual(
            rs.without_filing_words("monitor purchase consideration"),
            "monitor",
        )

    def test_a_real_subject_is_untouched(self):
        self.assertEqual(
            rs.without_filing_words("hotels in Seoul"), "hotels in Seoul",
        )

    def test_nothing_surviving_returns_nothing_rather_than_guessing(self):
        self.assertEqual(rs.without_filing_words("recommendation"), "")

    def test_the_thing_is_the_noun_not_the_label(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(MUSING, subject=TOPIC)

        self.assertEqual(problem._thing(), "monitor")


class ADictionaryEntryIsNotACandidateTests(unittest.TestCase):
    """A reference work publishes writing about words. It is never a thing."""

    def test_a_dictionary_page_is_off_target(self):
        for url in (
            "https://www.merriam-webster.com/dictionary/good",
            "https://www.dictionary.com/browse/good",
            "https://www.thesaurus.com/browse/good",
            "https://en.wiktionary.org/wiki/good",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    acquisition.classify(url), acquisition.OFF_TARGET,
                )

    def test_a_definition_path_is_off_target_on_any_host(self):
        self.assertEqual(
            acquisition.classify("https://example.com/dictionary/monitor"),
            acquisition.OFF_TARGET,
        )

    def test_a_real_product_page_is_still_a_candidate(self):
        for url in (
            "https://www.bestbuy.com/site/lg-ultragear/6467884.p",
            "https://example.com/product/monitor-27",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    acquisition.classify(url), acquisition.CANDIDATE,
                )


class ACorrectionEndsWithItsSentenceTests(unittest.TestCase):
    """Two sentences, and only the first one is putting something right."""

    def test_the_correction_stops_at_the_full_stop(self):
        self.assertEqual(
            conversation_focus.read_correction(
                "I was talking about getting me a new monitor. "
                "Where is Merriam-West Webster?"
            ),
            "getting me a new monitor",
        )

    def test_an_ordinary_correction_still_reads_whole(self):
        self.assertEqual(
            conversation_focus.read_correction("No, I meant the 27-inch one"),
            "27-inch one",
        )

    def test_a_correction_with_no_second_sentence_keeps_all_of_itself(self):
        # The leading article is scaffolding the focus layer already
        # strips; what matters is that nothing after it is lost.
        self.assertEqual(
            conversation_focus.read_correction(
                "I'm talking about the hotel in Seoul",
            ),
            "hotel in Seoul",
        )


class OneQuestionPerReplyTests(unittest.TestCase):
    """A question of her own is a question. It does not need a second."""

    ROUTE = {
        "intent": "conversation", "confidence": 0.95,
        "normalized_request": "thinking about getting a new monitor",
        "topic": TOPIC, "speech_act": "statement",
        "request_explicitness": "statement",
        "reason": "The user is musing about a monitor.",
    }

    def test_no_offer_is_appended_under_a_question(self):
        engine = build_engine(routes={MUSING.casefold(): self.ROUTE})
        engine._web_search_enabled = True
        engine.research_agent._search = lambda q, m=5: "No results."
        engine.client.reply = (
            "That sounds fun! Are you looking for a gaming one or "
            "something for work?"
        )

        reply = engine.chat(MUSING)

        self.assertEqual(reply.count("?"), 1, reply)
        self.assertNotIn("want me to", reply.casefold())


class NamingAResultCountsAsNamingItTests(unittest.TestCase):
    """A stored title is longer than the way anyone refers to it."""

    STORED = "LG UltraGear 27GP850-B 27in QHD Gaming Monitor"

    def test_the_head_of_the_name_is_enough(self):
        engine = build_engine()

        unchanged = engine._report_what_was_found(
            "The LG UltraGear 27GP850-B is a solid pick for both.",
            candidates=(self.STORED, "Dell S2722DGM Curved Gaming Monitor"),
            searched=True,
        )

        self.assertEqual(
            unchanged, "The LG UltraGear 27GP850-B is a solid pick for both.",
        )

    def test_one_shared_word_is_not_naming_it(self):
        engine = build_engine()

        reported = engine._report_what_was_found(
            "A monitor really does make a difference.",
            candidates=(self.STORED,),
            searched=True,
        )

        self.assertIn("LG UltraGear 27GP850-B", reported)


class AQualityIsNotAPrepositionTests(unittest.TestCase):
    """"About it" describes nothing any candidate could be checked against.

    Measured live. "Do you have anything about it" left the constraint
    ``attribute=about it`` on an open monitor problem, and constraints
    outlive the turn that made them. Two turns later:

        You said: Do you have like any like Samsung monitors for gaming?
        [Active Task] Constraints: attribute=about it [utterance]
        [Query]       text: about it monitor
        [Recommendation Reasoning]
          Decision: judged 'about it' semantically
          Selected: What is IT Monitoring?
          Why: fits about it

    The query found IT monitoring, and the fit layer dutifully checked six
    results against a preposition.
    """

    def test_a_prepositional_phrase_is_not_a_quality(self):
        self.assertEqual(
            rs.read_constraints("do you have anything about it"), (),
        )

    def test_a_vague_adjective_is_still_a_quality(self):
        # "Anything cheaper?" is a real refinement and has to survive.
        self.assertIn(
            "cheaper",
            [
                slot.value for slot in rs.read_constraints("anything cheaper?")
                if slot.name == rs.ATTRIBUTE
            ],
        )

    def test_an_ordinary_quality_is_untouched(self):
        self.assertIn(
            "soft",
            [
                slot.value
                for slot in rs.read_constraints(
                    "I have a sore throat, something soft.",
                )
                if slot.name == rs.ATTRIBUTE
            ],
        )


class ABrandTheyNamedReachesTheSearchTests(unittest.TestCase):
    """One word, and it was the only word that mattered.

    The router read "Do you have like any like Samsung monitors for
    gaming?" correctly as "recommend Samsung monitors for gaming". The held
    problem then built the query on its own subject, and single capitalised
    words were excluded from the carry-forward on the grounds that they are
    usually already in the subject. Samsung was not.
    """

    def _monitors(self):
        store = TaskSessionStore()
        store.note_recommendation_turn(MUSING, subject=TOPIC)
        return store.note_recommendation_turn(
            "Do you have like any like Samsung monitors for gaming?",
            subject=TOPIC,
        )

    def test_the_brand_is_in_the_query(self):
        query = self._monitors().search_query(
            "recommend Samsung monitors for gaming",
        )

        self.assertIn("samsung", query.casefold())

    def test_a_word_that_only_opens_a_sentence_is_not_a_name(self):
        self.assertEqual(
            rs._with_named_entities("monitor", "Do you have a monitor"),
            "monitor",
        )

    def test_a_request_verb_is_not_a_name(self):
        self.assertEqual(
            rs._with_named_entities("monitor", "please Find a monitor"),
            "monitor",
        )

    def test_a_retired_name_is_never_carried_back(self):
        # The router still sees the whole history, so the request that
        # carries "actually, something else" still contains the thing being
        # taken back. Word by word: a retired "Korean BBQ" has to stop a
        # lone "BBQ" too.
        self.assertEqual(
            rs._with_named_entities(
                "soft restaurants", "Korean BBQ places",
                retired=("Korean BBQ",),
            ),
            "soft restaurants",
        )


SESSION = (
    ("I'm thinking about getting a new monitor.",
     "monitor purchase consideration"),
    ("I'm not really sure, I'm just thinking about a gaming monitor or "
     "sometimes for working for coding and stuff.",
     "monitor purchase consideration"),
    ("since I'm going to quit gaming now so let's say coding.",
     "monitor purchase consideration"),
    ("Can you give me some recommendations?",
     "monitor purchase consideration"),
    ("Now I'm talking about monitors.", "monitors"),
    ("Just a good curved monitor I guess.", "monitor purchase consideration"),
    ("Can you just give me like any recommendation for coding?", "coding"),
)


def _play(turns=SESSION):
    store = TaskSessionStore()
    seen = []
    for said, subject in turns:
        seen.append(store.note_recommendation_turn(said, subject=subject))
    return seen


class SheStaysOnTheSubjectTests(unittest.TestCase):
    """Seven turns about one monitor, and four different problems.

    Measured live. Turns one to four built up a monitor purchase; turns
    five, six and seven each threw it away and started again:

        [Active Task] id: 3e1a0605a000  Constraints: preference=new
                      monitor, preference=gaming monitor
        You said: Now I'm talking about monitors.
        [Active Task] id: 44c074d9a824  Constraints: (none)
        You said: Just a good curved monitor I guess.
        [Active Task] id: 27e9ca640905  Constraints: (none)
        You said: Can you just give me like any recommendation for coding?
        [Active Task] id: 1a010a953aeb  Subject: coding

    So she asked for the size and resolution three separate times, and the
    last turn had nothing left that said "monitor" at all.
    """

    def test_the_whole_conversation_is_one_problem(self):
        ids = {problem.id for problem in _play()}

        self.assertEqual(
            len(ids), 1, "she started over instead of staying on the subject",
        )

    def test_a_plural_is_the_same_word_as_its_singular(self):
        # "Now I'm talking about monitors" against a problem known by
        # "monitor". The sets did not intersect, so it read as a new topic.
        problems = _play(SESSION[:5])

        self.assertEqual(problems[-1].id, problems[0].id)

    def test_asking_for_options_continues_what_is_open(self):
        # This used to require the problem to have been asked for options
        # once already, so the first such request could start a fresh one.
        problems = _play(SESSION[:1] + SESSION[6:])

        self.assertEqual(problems[-1].id, problems[0].id)
        self.assertIn("monitor", problems[-1].search_query("").casefold())

    def test_naming_a_different_thing_still_starts_a_new_problem(self):
        problems = _play((
            SESSION[0],
            ("Actually I want a guitar instead.", "guitar"),
        ))

        self.assertNotEqual(problems[0].id, problems[1].id)


class GivingSomethingUpRetiresItTests(unittest.TestCase):
    """"I'm going to quit gaming" is not a revision of the whole request."""

    def test_the_dropped_preference_goes(self):
        problem = _play(SESSION[:3])[-1]

        self.assertNotIn(
            "gaming",
            " ".join(slot.value for slot in problem.constraints).casefold(),
        )

    def test_what_they_still_want_stays(self):
        problem = _play(SESSION[:3])[-1]

        self.assertIn("monitor", problem.search_query("").casefold())

    def test_the_query_stops_asking_for_it(self):
        problem = _play(SESSION[:4])[-1]

        self.assertNotIn("gaming", problem.search_query("").casefold())

    def test_settling_on_something_records_it(self):
        # "let's say coding" is how a choice the conversation was weighing
        # actually gets made out loud.
        problem = _play(SESSION[:3])[-1]

        self.assertIn("coding", problem.search_query("").casefold())


class ABareNounPhraseIsStillAnAnswerTests(unittest.TestCase):
    """"Just a good curved monitor I guess." parses as no request at all."""

    def test_the_modifiers_reach_the_problem(self):
        problem = _play(SESSION[:6])[-1]

        self.assertIn("curved", problem.search_query("").casefold())

    def test_the_determiner_bounds_what_is_taken(self):
        # Without the bound this took "i'm talking" out of "Now I'm talking
        # about monitors" and put it in the search box.
        self.assertEqual(
            rs.qualities_named_with("Now I'm talking about monitors.", "monitor"),
            (),
        )

    def test_a_position_is_not_a_quality(self):
        self.assertEqual(
            rs.qualities_named_with("open the second monitor", "monitor"), (),
        )

    def test_a_question_about_the_thing_states_nothing(self):
        self.assertEqual(
            rs.qualities_named_with("What should I eat for dinner?", "dinner"),
            (),
        )


class ANameSaidAloudMustBeAboutTheSubjectTests(unittest.TestCase):
    """The fit layer keeps what contradicts nothing. That is not the same.

    Measured live, after four turns about monitors:

        [Grounding Guard] The search found 2 and the answer named nothing;
                          naming '2027 Car Prices In South Korea'.
        Elaina: ... 2027 Car Prices In South Korea is the one I'd start
                with.

    Nothing had contradicted it, because nothing in a conversation about
    monitors says anything about cars.
    """

    def test_an_unrelated_result_is_not_named(self):
        engine = build_engine()

        self.assertFalse(
            engine._candidate_is_about(
                "2027 Car Prices In South Korea", "new monitor",
            ),
        )

    def test_a_related_result_is(self):
        engine = build_engine()

        self.assertTrue(
            engine._candidate_is_about(
                "LG UltraGear 27GP850-B 27in QHD Gaming Monitor",
                "new monitor",
            ),
        )

    def test_silence_beats_naming_the_wrong_thing(self):
        engine = build_engine()
        answer = "Check its specs to make sure it fits your setup!"

        self.assertEqual(
            engine._report_what_was_found(
                answer,
                candidates=("2027 Car Prices In South Korea",),
                searched=True,
                about="new monitor",
            ),
            answer,
        )

    def test_no_subject_means_no_opinion(self):
        engine = build_engine()

        self.assertTrue(engine._candidate_is_about("Anything at all", ""))


class AQualityIsNotANewSubjectTests(unittest.TestCase):
    """"Mechanical" describes a keyboard. It is not a different thing.

    Measured live, three turns into a conversation about keyboards:

        You said: I'm thinking about mechanical
        [Active Task] id: 0c5cccd108e4  Subject: keyboards
                      Constraints: (none)

    "mechanical" was read as a new named thing, so the problem restarted
    and ``preference=keyboards`` went with it. Every turn after that
    carried no constraints at all -- which is why the conversation
    accumulated nothing, no search was ever warranted, and six turns of
    keyboard talk produced no options.
    """

    def test_a_bare_quality_is_not_a_thing(self):
        for word in ("mechanical", "tactile", "wireless", "curved", "gaming"):
            with self.subTest(word=word):
                self.assertFalse(rs.names_a_thing(word))

    def test_a_noun_still_is(self):
        for word in (
            "keyboard", "monitor", "laptop", "guitar",
            "mechanical keyboard", "electric guitar",
        ):
            with self.subTest(word=word):
                self.assertTrue(rs.names_a_thing(word))

    def test_the_quality_is_filed_as_one(self):
        found = rs.read_constraints("I'm thinking about mechanical")

        self.assertEqual(
            [(slot.name, slot.value) for slot in found],
            [(rs.ATTRIBUTE, "mechanical")],
        )

    def test_the_conversation_keeps_what_it_learns(self):
        store = TaskSessionStore()
        for said in (
            "Let's look at keyboards instead",
            "I'm thinking about mechanical",
            "tactile",
            "gaming",
        ):
            problem = store.note_recommendation_turn(said, subject="keyboards")

        query = problem.search_query("what do you think").casefold()

        for word in ("keyboard", "mechanical", "tactile", "gaming"):
            with self.subTest(word=word):
                self.assertIn(word, query)


if __name__ == "__main__":
    unittest.main()
