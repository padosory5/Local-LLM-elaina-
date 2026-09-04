"""What the person just said outranks everything held from before.

Not a bug report -- a principle, written down because it has been broken
in four different layers across two dogfooding sessions, each time by a
different mechanism, and each time the symptom was that an explicit
request quietly became something else.

    B-33  a pending web-search offer replaced "use my browser control,
          go to Zelo.com" with its own stored goal
    B-35  the same, so a promised browser action was never dispatched
    B-52  the same, so "can you find the place of that name" re-ran the
          yes/no question the user was contradicting
    B-36  "one of those websites" was taken literally instead of being
          resolved, and B-03's "no Zillow" was read as an exclusion
    B-28  an anchor set by one correction rode into every later query
    B-42  the same anchor, in an unrelated internship search

Three things may be carried into a turn -- a generic phrase's referent,
task state, a standing offer -- and none of them may overrule what the
turn itself says. This asserts that at every layer that has broken it.
"""

import unittest

from brain import browser_progress
from brain import conversation_focus
from brain import recommendation_state as state
from brain.capabilities import CapabilityRegistry
from brain.recommendation import reads_as_clear_acceptance


class AStandingOfferNeverReplacesARequestTests(unittest.TestCase):

    def test_an_instruction_naming_its_own_errand_is_not_consent(self):
        for said in (
            "So use my browser control, go to Zelo.com, search up apartments "
            "near University of Washington.",
            "Use browser control and then show me a sturdy box.",
            "But I did go to a casino there with my friends. Can you find "
            "the place of that name?",
            "open Spotify for me",
            "search for the one on the island instead",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_clear_acceptance(said), said)

    def test_bare_assent_still_accepts(self):
        # The other direction matters just as much: an offer she made must
        # remain acceptable, or every question she asks is unanswerable.
        for said in (
            "yeah", "go ahead", "sure, do that", "yes please",
            "search for some", "I'm ready to start", "ok do it",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_clear_acceptance(said), said)


class AGenericPhraseNeverOverridesANameTests(unittest.TestCase):

    LISTED = "You could try Karrot, Bunjang, Joonggonara, or Hello Market."

    def test_a_turn_that_names_its_target_keeps_it(self):
        self.assertEqual(
            browser_progress.resolve_named_choice(
                "open Bunjang for me", said_before=self.LISTED,
            ),
            "",
        )

    def test_only_a_pointer_is_resolved(self):
        self.assertTrue(
            browser_progress.resolve_named_choice(
                "open one of those", said_before=self.LISTED,
            )
        )

    def test_a_named_app_is_not_re_resolved_by_role(self):
        from tools.computer_control.windows_app_catalog import WindowsAppCatalog

        self.assertEqual(
            WindowsAppCatalog().resolve_running(
                "Whale", running=("Whale", "Chrome"),
            ),
            "",
        )


class StaleStateNeverSteersANewSubjectTests(unittest.TestCase):

    def test_an_anchor_stops_when_the_subject_moves_on(self):
        import time

        focus = conversation_focus.start(now=time.monotonic())
        for said, subject in (
            ("I mean look at Zillow for rental options near University of "
             "Washington", "rental options"),
            ("Also, I'm trying to get some internships in 2027 summer",
             "Internship Preparation"),
        ):
            focus = conversation_focus.update(
                focus, said, subject=subject, now=time.monotonic(),
            )

        self.assertNotIn(
            "zillow", " ".join(focus.query_context()).casefold(),
        )

    def test_an_unrelated_sentence_does_not_join_an_open_task(self):
        problem = state.update(
            state.start("apartments", domain="apartments"),
            "just like a studio, $1000 to $1500",
        )

        for said in (
            "Okay, I searched it up and the phone number is 206-221-7857.",
            "I want to get an internship in summer 2027.",
            "Where can I get an international driving permit?",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    state.about_the_same_thing(problem, said), said,
                )

    def test_a_genuine_refinement_still_joins_it(self):
        problem = state.update(
            state.start("apartments", domain="apartments"),
            "I want to rent a place near UW",
        )

        for said in ("just like a studio", "from $1000 to $1500"):
            with self.subTest(said=said):
                self.assertTrue(
                    state.about_the_same_thing(problem, said), said,
                )


class ADisputeIsCheckedNotRepeatedTests(unittest.TestCase):

    def test_the_query_that_produced_the_claim_is_not_re_run(self):
        from brain.intent_router import IntentDecision
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._router_history.extend([
            {"role": "assistant", "content": (
                "No, there are no casinos in Bainbridge Island."
            )},
        ])
        stale = "Are there any casinos in Bainbridge Island near Seattle?"

        escalated = engine._escalate_disputed_claim(
            IntentDecision(
                intent="web_search", confidence=1.0,
                normalized_request=stale, search_query=stale, reason="t",
            ),
            "But I did go to a casino there with my friends.",
        )

        self.assertNotEqual(escalated.search_query, stale)



class WhatTheTurnSaysDecidesWhereTheQueryLooksTests(unittest.TestCase):
    """Session 5. The invariant at the query boundary, in both directions.

    A place is the one dimension with a silent fallback behind it, so it
    is the one where being wrong is invisible. Three rules:

    * a place mentioned in passing is context for its own topic and goes
      when the topic goes;
    * a place stated about the person is a fact about them and stays;
    * a turn that names its own place is not asking anything to remember
      one, and a turn that names none may have the market filled in.
    """

    def _walk(self, turns):
        import time

        focus = conversation_focus.start(now=time.monotonic())
        for said, subject in turns:
            focus = conversation_focus.update(
                focus, said, subject=subject, now=time.monotonic(),
            )
        return focus

    CLOCK = (
        ("What time is it in Seattle right now?", "time"),
        ("Can you give me the date as well?", "time"),
        ("know in Seattle.", "time"),
        ("Do you know what time it is in London right now?", "time"),
    )

    def test_a_place_mentioned_in_passing_does_not_outlive_its_topic(self):
        # Session 5: twenty turns after asking the time in Seattle, a
        # search for packing peanuts went out as "packing peanuts Seattle"
        # and a search for casinos on an island came back about Seattle.
        focus = self._walk(
            self.CLOCK + (("Where can I buy packing peanuts?", "shopping"),),
        )

        self.assertNotIn(
            "seattle", " ".join(focus.query_context()).casefold(),
        )

    def test_a_place_stated_about_the_person_does_outlive_it(self):
        # The other direction, and the reason the rule is about provenance
        # rather than about age: this is what "find me a studio" a week
        # later is supposed to search.
        focus = self._walk((
            ("I'm moving to Seattle on September 18.", "moving"),
            ("Where can I buy packing peanuts?", "shopping"),
        ))

        self.assertIn("seattle", " ".join(focus.query_context()).casefold())

    def test_a_turn_that_names_its_own_place_holds_nothing_back(self):
        # Session 5: "a studio near the University of Washington" matched
        # the relational-reference reader on "near the university", so the
        # previous subject was kept as the task's anchor -- and the
        # previous subject was `time`. The query went out as
        # "accommodation University of Washington time Seattle".
        focus = self._walk(
            self.CLOCK
            + ((
                "Can you find me a studio near the University of "
                "Washington with a budget?", "accommodation",
            ),),
        )

        self.assertNotIn("time", focus.background)
        self.assertNotIn("time", " ".join(focus.query_context()).casefold())

    def test_a_turn_that_names_only_a_role_still_points_back(self):
        # The over-correction to watch: "near my school" has to keep
        # meaning the school under discussion, or B-28's fix is undone.
        focus = self._walk((
            ("I mean I'm going to University of Washington", "UW"),
            ("Where can I rent a place near my school?", "rentals"),
        ))

        self.assertIn(
            "washington", " ".join(focus.query_context()).casefold(),
        )

    def test_the_query_says_where_when_the_turn_did(self):
        problem = state.update(
            state.start("accommodation", domain="hotel"),
            "Can you find me a studio near the University of Washington "
            "with a budget?",
        )

        self.assertEqual(
            problem.values(state.AREA), ("University of Washington",),
        )
        self.assertIn(
            "university of washington",
            problem.search_query("Find a studio near UW").casefold(),
        )

    def test_the_market_still_fills_in_when_the_turn_did_not(self):
        # The over-correction to watch: losing the user's market entirely.
        problem = state.update(
            state.start("packing peanuts"), "Where can I buy packing peanuts?",
        )

        self.assertEqual(problem.values(state.AREA), ())


class ACorrectionRepairsTheTaskItCorrectsTests(unittest.TestCase):
    """Session 5. Three fragments, one shape.

    None of "In Korea though", "Only one S" or "So open it" is a request
    by itself. Each is a request the turn before it, with one thing
    changed. Routing each as if it arrived out of nowhere is how a
    correction became small talk, an apology, and fourteen rounds of the
    desktop planner.
    """

    def test_saying_where_re_runs_the_open_lookup(self):
        problem = state.update(
            state.start("packing peanuts"), "Where can I buy packing peanuts?",
        )
        self.assertEqual(state.supplies_only_a_place("In Korea though."), "Korea")

        revised = state.update(problem, "In Korea though.")

        self.assertIn("korea", revised.search_query("").casefold())
        self.assertNotIn("seattle", revised.search_query("").casefold())

    def test_a_turn_with_a_request_in_it_is_not_only_a_place(self):
        # The over-correction to watch: this must stay a request of its
        # own, or every rental search becomes a resumed one.
        for said in (
            "Can you find me a studio near the University of Washington?",
            "I'm in Korea now, find me a hotel",
            "Where can I buy packing peanuts?",
        ):
            with self.subTest(said=said):
                self.assertEqual(state.supplies_only_a_place(said), "", said)

    def test_a_spelling_correction_goes_back_to_the_address(self):
        goal = (
            "Can you use my browser control and then open to "
            "isss.washington.edu?"
        )

        self.assertEqual(
            browser_progress.respelled_address(goal, "Only one S."),
            "is.washington.edu",
        )

    def test_a_spelling_correction_is_never_guessed_at(self):
        # Two runs of the letter are both candidates, so neither is the
        # answer. Saying so is the honest outcome; picking one is not.
        self.assertEqual(
            browser_progress.respelled_address("assess.com", "only one S"), "",
        )
        for said in ("Only one.", "I only have one S in my name", "one more"):
            with self.subTest(said=said):
                self.assertEqual(
                    browser_progress.respelled_address(
                        "isss.washington.edu", said,
                    ),
                    "", said,
                )

    def test_a_pronoun_is_not_the_name_of_an_application(self):
        # Session 5: "So open it." matched the desktop matcher on "open
        # it", was rescued to the desktop planner, and it spent fourteen
        # rounds looking for a native window -- including trying to play
        # media -- before running out of budget.
        self.assertFalse(CapabilityRegistry.match("So open it.").matched)
        self.assertTrue(browser_progress.continues_the_last_action("So open it."))

    def test_a_named_application_still_is_one(self):
        for said in ("open Spotify", "close Discord", "launch Notepad"):
            with self.subTest(said=said):
                match = CapabilityRegistry.match(said)
                self.assertTrue(match.matched, said)
                self.assertEqual(match.capability.id, "ui_control", said)

    def test_a_turn_that_carries_its_own_target_is_not_a_continuation(self):
        for said in (
            "open zillow.com", "open Spotify",
            "So open it and tell me what it says",
            "open the second one",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    browser_progress.continues_the_last_action(said), said,
                )

    def test_the_last_action_is_what_a_bare_instruction_reopens(self):
        from brain.intent_router import IntentDecision
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._last_computer_action = "open_url"
        engine._last_computer_goal = "iss.washinton.edu"

        route, note = engine._rescue_capability_route(
            IntentDecision(
                intent="computer_action", confidence=0.95,
                normalized_request="So open it.", reason="t",
                computer_operation="unsupported",
            ),
            "So open it.",
        )

        self.assertEqual(route.computer_operation, "open_url")
        self.assertEqual(route.action_target, "iss.washinton.edu")
        self.assertEqual(note, "")

    def test_a_spelling_correction_reaches_the_action_layer(self):
        from brain.intent_router import IntentDecision
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._last_computer_action = "browser_action"
        engine._last_computer_goal = (
            "Can you use my browser control and then open to "
            "isss.washington.edu?"
        )

        route, _ = engine._rescue_capability_route(
            IntentDecision(
                intent="conversation", confidence=0.95,
                normalized_request="Only one S", reason="t",
            ),
            "Only one S.",
        )

        self.assertEqual(route.computer_operation, "open_url")
        self.assertEqual(route.action_target, "is.washington.edu")


class ADisputeIsCheckedNotOfferedTests(unittest.TestCase):
    """Session 5. The guard fired and then nothing happened.

        Elaina: No casinos are listed for Brainsome Island right now.
        User:   but I've been there.
        Elaina: You said you've been there -- I'm here now. Say the word
                and I'll go through casinos.

    ``[Grounding Guard] Disputed claim: verifying rather than repeating.``
    was printed on that turn. The need came out as live_verification, and
    the interaction layer then downgraded it to an offer because the turn
    was a remark rather than a request. Being told you are wrong is not a
    remark.
    """

    def _escalated(self):
        from brain.intent_router import IntentDecision
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._router_history.extend([
            {"role": "assistant", "content": (
                "No casinos are listed for Brainsome Island right now. The "
                "nearest options are in Seattle and Snoqualmie."
            )},
        ])
        return engine._escalate_disputed_claim(
            IntentDecision(
                intent="conversation", confidence=0.95,
                normalized_request="but I've been there.",
                reason="a personal experience",
                request_explicitness="statement",
            ),
            "but I've been there.",
        )

    def test_the_dispute_is_acted_on_rather_than_offered(self):
        from brain.deliberation import goal_intent, interaction

        escalated = self._escalated()
        decision = interaction.decide(
            escalated,
            goal=goal_intent.read(escalated),
            has_usable_context=False,
        )

        self.assertEqual(decision.mode, interaction.EXECUTE)

    def test_an_ordinary_remark_is_still_only_offered(self):
        # The over-correction to watch: the remark test exists because
        # "Spotify won't play anything today." produced a web search. It
        # has to keep doing that job.
        from brain.deliberation import goal_intent, interaction
        from brain.intent_router import IntentDecision

        route = IntentDecision(
            intent="web_search", confidence=0.9,
            normalized_request="Spotify won't play anything today.",
            reason="t", request_explicitness="statement",
            requires_external_evidence=True,
        )
        decision = interaction.decide(
            route, goal=goal_intent.read(route), has_usable_context=False,
        )

        self.assertEqual(decision.mode, interaction.RECOMMEND)


class TheCorrectedPlaceReachesTheSearchTests(unittest.TestCase):
    """Session 5, S5-02, through the whole turn rather than one reader.

        User:   Where can I buy packing peanuts?
        [Query] packing peanuts Seattle
        User:   In Korea though.
        Elaina: Cool, you're in Korea! What's new there?

    Two failures in two turns: a place from an older topic beat the user's
    own market, and the correction to it was answered as small talk. This
    is the pair, end to end.
    """

    SEARCH = {
        "intent": "web_search", "confidence": 0.95,
        "normalized_request": "buy packing peanuts", "reason": "s5",
        "computer_operation": "none", "action_target": "",
        "speech_act": "information_request", "action_requested": True,
        "topic": "shopping", "recommendation_needed": True,
        "requires_external_evidence": True,
        "search_query": "buy packing peanuts",
    }
    REMARK = {
        "intent": "conversation", "confidence": 0.95,
        "normalized_request": "In Korea though", "reason": "s5",
        "computer_operation": "none", "action_target": "",
        "speech_act": "statement", "action_requested": False,
        "topic": "shopping", "request_explicitness": "statement",
    }

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine({
            "packing peanuts": self.SEARCH,
            "in korea": self.REMARK,
            "kiwis": dict(self.REMARK, normalized_request="I like kiwis"),
        })

    def tearDown(self):
        self.engine.close()

    def test_the_place_correction_re_runs_the_lookup_there(self):
        self.engine._route_turn("Where can I buy packing peanuts?", timings={})

        second = self.engine._route_turn("In Korea though.", timings={})
        resolved = self.engine._resolved_search_query(
            second.route, second.goal_intent,
        )

        self.assertEqual(second.route.intent, "web_search")
        self.assertIn("korea", resolved.casefold())
        self.assertNotIn("seattle", resolved.casefold())
        self.assertEqual(
            self.engine.task_sessions.active_recommendation().values(
                state.AREA,
            ),
            ("Korea",),
        )

    def test_an_ordinary_remark_does_not_re_run_anything(self):
        # The over-correction to watch: an open task must not turn every
        # later sentence into a repeat search.
        self.engine._route_turn("Where can I buy packing peanuts?", timings={})

        second = self.engine._route_turn("I like kiwis.", timings={})

        self.assertEqual(second.route.intent, "conversation")


class OneRepresentationOfTheCurrentTurnTests(unittest.TestCase):
    """Session 6. The turn may not survive in two versions at once.

    The router repairs a mishearing by putting the person's own word back
    into its paraphrase. It repaired one field and left the rest, so the
    same turn left the router saying two things:

        [Router] restored 'Are there Casinos in Seattle?'
        [Router] conversation (0.95): The user is asking about the
                 existence of Cousinos in Seattle

    Which one a layer downstream believes then depends on which field it
    happens to read.
    """

    class _Scripted:
        def __init__(self, payload):
            self.payload = payload

        def chat(self, **_kwargs):
            import json

            return {"message": {"content": json.dumps(self.payload)}}

    def _decision(self):
        from brain.intent_router import SemanticIntentRouter

        router = SemanticIntentRouter(
            self._Scripted({
                "intent": "web_search",
                "confidence": 0.95,
                "normalized_request": "Are there Cousinos in Seattle?",
                "reason": (
                    "The user is asking about the existence of Cousinos in "
                    "Seattle, which is a direct question."
                ),
                "topic": "Cousinos in Seattle",
                "entity": "Cousinos",
                "search_query": "Cousinos in Seattle",
                "speech_act": "information_request",
            }),
            "qwen3:8b",
        )
        return router.route("No, no, are there casinos in Seattle?")

    def test_the_repair_reaches_every_field_the_model_wrote(self):
        decision = self._decision()

        for field in (
            "normalized_request", "reason", "topic", "entity", "search_query",
        ):
            with self.subTest(field=field):
                self.assertNotIn(
                    "cousinos", str(getattr(decision, field, "")).casefold(),
                )

    def test_the_repair_does_not_damage_the_prose_it_touches(self):
        # The over-correction: "the" and "there" score 0.75 against each
        # other, so a transcript containing "are there casinos" rewrote
        # every "the" in the reason to "there".
        decision = self._decision()

        self.assertTrue(decision.reason.startswith("The user"), decision.reason)
        self.assertIn("about the existence", decision.reason)


class ACorrectionToAnActionIsNotANewSubjectTests(unittest.TestCase):
    """Session 6, S6-01. The correction became a topic.

        User:   open isss.washington.edu
        User:   I meant only one S.
        [Conversation State] Current subject: only one S
                             No longer the focus: browser_action

    Two layers read the same turn and disagreed: the repair layer had it
    as a correction to an address, the focus layer as a change of subject.
    The focus layer runs second, and it won.
    """

    def _engine(self, action="open_url", goal="isss.washington.edu"):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._turn_points_at_the_last_action = False
        engine._last_computer_action = action
        engine._last_computer_goal = goal
        return engine

    def test_the_correction_reaches_the_address(self):
        from brain.intent_router import IntentDecision

        engine = self._engine()
        try:
            route, _ = engine._rescue_capability_route(
                IntentDecision(
                    intent="conversation", confidence=0.95,
                    normalized_request="I meant only one S", reason="t",
                ),
                "I meant only one S.",
            )
        finally:
            engine.close()

        self.assertEqual(route.computer_operation, "open_url")
        self.assertEqual(route.action_target, "is.washington.edu")

    def test_a_turn_the_repair_layer_claimed_names_no_new_subject(self):
        import time

        focus = conversation_focus.start(now=time.monotonic())
        focus = conversation_focus.update(
            focus, "Can you use my browser control and open isss.washington.edu?",
            subject="browser_action", now=time.monotonic(),
        )
        after = conversation_focus.update(
            focus, "I meant only one S.", subject="only one S",
            now=time.monotonic(), pointer=True,
        )

        self.assertEqual(after.subject, focus.subject)
        self.assertNotIn("browser_action", after.superseded)

    def test_without_that_claim_a_correction_still_moves_the_subject(self):
        # The over-correction: an ordinary "I mean X" must still work.
        import time

        focus = conversation_focus.start(now=time.monotonic())
        focus = conversation_focus.update(
            focus, "I'm moving to Seattle.", subject="moving",
            now=time.monotonic(),
        )
        after = conversation_focus.update(
            focus, "No, I mean I'm going to UW.", subject="Seattle",
            now=time.monotonic(),
        )

        self.assertEqual(after.subject, "UW")

    def test_the_operative_clause_is_the_last_one(self):
        # "There's three S's in there. I just want two S's in there."
        # Both clauses are letter counts and they disagree; the one that
        # says what is wanted is the one at the end.
        self.assertEqual(
            browser_progress.respelled_address(
                "isss.washington.edu",
                "There's three S's in there. I just want two S's in there.",
            ),
            "iss.washington.edu",
        )


class EvidenceThatAnActionFailedReachesItTests(unittest.TestCase):
    """Session 6, S6-03.

        Elaina: [open_url Zillow.com status=url_opened]
        User:   you didn't open it.
        Elaina: I can't do that one. Right now I can use browser control...

    Saying she cannot do the thing while listing it as something she can
    do. English lets the denial take the agent as its subject or the
    thing as its subject, and only one of the two was read.
    """

    def test_the_denial_goes_back_to_the_action(self):
        from brain.intent_router import IntentDecision
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._turn_points_at_the_last_action = False
        engine._last_computer_action = "open_url"
        engine._last_computer_goal = "Zillow.com"
        try:
            route, note = engine._rescue_capability_route(
                IntentDecision(
                    intent="computer_action", confidence=0.95,
                    normalized_request="you didn't open it.", reason="t",
                    computer_operation="unsupported",
                ),
                "you didn't open it.",
            )
        finally:
            engine.close()

        self.assertEqual(route.computer_operation, "open_url")
        self.assertEqual(route.action_target, "Zillow.com")
        self.assertEqual(note, "")

    def test_both_word_orders_are_the_same_complaint(self):
        for said in (
            "you didn't open it.", "You never opened it",
            "it didn't open", "You haven't shown me anything",
            "it's not open",
        ):
            with self.subTest(said=said):
                self.assertTrue(
                    state.complains_about_missing_results(said), said,
                )

    def test_a_denial_that_names_no_action_is_not_one(self):
        # The over-correction: not every "you didn't" is about the last
        # machine action.
        for said in (
            "you didn't understand me", "you didn't say that",
            "you didn't have to do that", "I didn't open it",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    state.complains_about_missing_results(said), said,
                )


class AnAnchorIsAThingTests(unittest.TestCase):
    """Session 6, S6-04 and S6-06. What the anchor is allowed to be.

    It held an errand -- "no, open Zillow.com" -- and then a placeholder,
    the literal word "Conversation". Both rode into searches:

        Are there Cousinos in Seattle? no, open Zillow.com
        studio apartments near Conversation $1,500
    """

    def _walk(self, turns):
        import time

        focus = conversation_focus.start(now=time.monotonic())
        for said, subject in turns:
            focus = conversation_focus.update(
                focus, said, subject=subject, now=time.monotonic(),
            )
        return focus

    def test_an_errand_is_not_what_the_conversation_is_about(self):
        focus = self._walk((
            ("Can you use my browser control and open zelo.com?", "browser_action"),
            ("No, no, open Zillow.com", "website"),
        ))

        self.assertNotIn("about", focus.background)

    def test_a_placeholder_is_not_either(self):
        focus = self._walk((
            ("Can you find me a studio near the University of Washington?",
             "accommodation"),
            ("I like strawberries.", "Conversation"),
            ("Can you find me a rent near my school?", "rental housing"),
        ))

        self.assertNotEqual(focus.background.get("about", ""), "Conversation")

    def test_my_school_resolves_to_the_school_that_was_named(self):
        # She could answer "where's my university again?" correctly one
        # turn later, so the knowledge was there. The referent resolver
        # was reading the previous subject instead of looking for it.
        focus = self._walk((
            ("Can you find me a studio near the University of Washington?",
             "accommodation"),
            ("You better be.", "Conversation"),
            ("I like strawberries.", "Conversation"),
            ("Can you find me a rent near my school?", "rental housing"),
        ))

        self.assertEqual(
            focus.background.get("about", ""), "University of Washington",
        )

    def test_a_real_topic_correction_still_becomes_the_anchor(self):
        # The over-correction: the anchor exists so that "rent near my
        # school" three turns later still means near UW.
        focus = self._walk((
            ("I'm moving to Seattle on September 18.", "moving"),
            ("No, I mean I'm going to UW.", "Seattle"),
            ("Where can I rent a place near my school?", "rentals"),
        ))

        self.assertIn("UW", focus.query_context())


class TheExistentialThereIsNotAPointerTests(unittest.TestCase):
    """Session 6, S6-04. "Are there casinos in Seattle?"

    Read as pointing back at the previous topic, so the anchor was kept,
    and the anchor was a browser instruction from three turns earlier.
    """

    def test_asking_whether_something_exists_moves_the_topic_on(self):
        import time

        focus = conversation_focus.start(now=time.monotonic())
        focus = conversation_focus.update(
            focus, "No, I mean Bainbridge Island.", subject="Seattle",
            now=time.monotonic(),
        )
        self.assertEqual(focus.background.get("about"), "Bainbridge Island")

        after = conversation_focus.update(
            focus, "Are there Cousinos in Seattle?",
            subject="Cousinos in Seattle", now=time.monotonic(),
        )

        self.assertNotIn(
            "bainbridge", " ".join(after.query_context()).casefold(),
        )

    def test_a_deictic_there_still_points_back(self):
        # The over-correction: "is it cheaper there?" is about the place
        # under discussion and must keep pointing at it.
        import time

        focus = conversation_focus.start(now=time.monotonic())
        focus = conversation_focus.update(
            focus, "I mean I'm going to UW.", subject="Seattle",
            now=time.monotonic(),
        )
        after = conversation_focus.update(
            focus, "Is it expensive there?", subject="cost of living",
            now=time.monotonic(),
        )

        self.assertIn("UW", " ".join(after.query_context()))


class ACandidateMustBeTheThingAskedForTests(unittest.TestCase):
    """Session 6, S6-02.

        [Query] packing peanuts in Korea
        Selected: Coffee Flavor Peanut, Korea price supplier - 21food
        Why: fits Korea

    It did fit Korea. Nothing checked whether it was packing material,
    because the fit reader looked at every dimension except the one that
    says what the thing is.
    """

    def _problem(self):
        problem = state.update(
            state.start("packing peanuts"), "Where can I buy packing peanuts?",
        )
        return state.update(problem, "In Korea though.")

    def test_a_partial_match_on_a_compound_name_is_a_mismatch(self):
        from brain import candidate_fit

        fits = candidate_fit.evaluate(
            [{"title": "Coffee Flavor Peanut, Korea price supplier",
              "url": "https://21food.com/x", "summary": ""}],
            self._problem(),
        )

        self.assertEqual(fits[0].verdict, "MISMATCH")
        self.assertFalse(fits[0].viable)

    def test_the_thing_actually_asked_for_outranks_it(self):
        from brain import candidate_fit

        fits = candidate_fit.evaluate(
            [
                {"title": "Coffee Flavor Peanut, Korea price supplier",
                 "url": "https://21food.com/x", "summary": ""},
                {"title": "White Anti-Static Packing Peanuts in Korea",
                 "url": "https://shop.example.com/p/2", "summary": "$20"},
            ],
            self._problem(),
        )

        self.assertIn("Packing Peanuts", fits[0].name)

    def test_naming_the_subject_is_not_by_itself_a_fit(self):
        # The over-correction: being the right kind of thing is the floor,
        # not evidence. A page titled "Guitars" says nothing about
        # electric or about the budget.
        from brain import candidate_fit
        from brain.task_session import TaskSessionStore

        store = TaskSessionStore()
        for turn in (
            "I'm thinking about getting a guitar.", "Electric.",
            "About 500,000 won.",
        ):
            problem = store.note_recommendation_turn(turn, subject="guitar")

        fits = candidate_fit.evaluate(
            [{"title": "Guitars", "summary": ""}], problem,
        )

        self.assertEqual(fits[0].verdict, "UNCHECKED")


class TheSentenceOutranksItsParaphraseTests(unittest.TestCase):
    """Session 6, S6-07. A studio is not a hotel.

        [Preference Resolution] Domain: hotel

    for "a studio near the University of Washington" -- because the goal
    layer's one-word summary of it was "accommodation", which types as a
    booking, and the summary was read before the sentence.
    """

    def test_the_request_decides_the_category(self):
        problem = state.update(
            state.start("accommodation"),
            "Can you find me a studio near the University of Washington "
            "with a budget?",
        )

        self.assertEqual(problem.category, "realestate")
        self.assertIn("studio", problem.search_query("").casefold())

    def test_a_discarded_clause_still_may_not_supply_it(self):
        # The over-correction: when part of the sentence was judged to be
        # a condition, that part may not type the request either.
        # "What if you have a car?" typed the whole thing as the car
        # domain and searched "Washington State cars Seattle".
        problem = state.update(
            state.start("travel"),
            "What if you have a car? Where are good places to travel "
            "along Washington State?",
        )

        self.assertNotIn("car", problem.search_query("").casefold().split())


class WhatWasFoundIsWhatGetsSaidTests(unittest.TestCase):
    """Session 6, S6-08.

        [Recommendation Reasoning] Candidates: 3 (3 fit, 0 unchecked)
        Selected: Studio off-campus student housing near University of
                  Washington
        Why: fits studio, University of Washington

        Elaina: [Grounding Guard] Unverified place(s): Perchn.
                ... I don't want to send you somewhere I haven't checked
                -- want me to look up real ones?

    Three candidates had been checked and ranked, one had been chosen and
    the reason written down, and the answer named something else entirely.
    Deleting the invented name is right. Offering to go and find what had
    already been found is not.
    """

    def _engine(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._grounded_context = {"subject": "studios near UW", "statement": ""}
        return engine

    def test_the_answer_names_the_candidate_that_was_chosen(self):
        engine = self._engine()
        try:
            engine.task_sessions.note_recommendation_turn(
                "Can you find me a studio near the University of Washington?",
                subject="accommodation",
            )
            engine.task_sessions.record_candidates(
                ("Studio off-campus student housing near University of "
                 "Washington",),
                evidence=("checked",),
            )

            reply = engine._enforce_grounded_entities(
                "Try Perchn for studios near campus.",
                user_input="Can you find me a studio near UW?",
                action_performed=False,
            )
        finally:
            engine.close()

        self.assertNotIn("Perchn", reply)
        self.assertIn("Studio off-campus student housing", reply)
        self.assertNotIn("look up real ones", reply)

    def test_with_nothing_found_it_still_offers_to_look(self):
        # The other direction: the offer is the right answer when there
        # is nothing to name.
        engine = self._engine()
        try:
            reply = engine._enforce_grounded_entities(
                "Try Walmart or Target for packing peanuts.",
                user_input="Where can I buy packing peanuts?",
                action_performed=False,
            )
        finally:
            engine.close()

        self.assertNotIn("Walmart", reply)
        self.assertIn("look up real ones", reply)


class ARetryIsCheckedLikeADraftTests(unittest.TestCase):
    """Session 6, S6-09. The guard fired and the retry did it again.

        User:   I like strawberries.
        [Response Guard] Repeated an unrelated prior answer; regenerating
        Elaina: You're welcome -- strawberries are tasty. Want to try some?

    Nobody had said thank you. The draft was rejected for opening this
    way and the regeneration was accepted without the same test -- the
    same shape as B-56, one layer over.
    """

    def test_an_unearned_courtesy_is_removed(self):
        from brain.response_quality import ResponseQualityGuard

        self.assertEqual(
            ResponseQualityGuard.without_stale_courtesy(
                "You're welcome -- strawberries are tasty. Want to try some?",
                "I like strawberries.",
            ),
            "Strawberries are tasty. Want to try some?",
        )

    def test_an_earned_one_is_left_alone(self):
        from brain.response_quality import ResponseQualityGuard

        for reply, said in (
            ("You're welcome! Enjoy the film.", "thanks a lot"),
            ("Strawberries are tasty.", "I like strawberries."),
            ("No problem. Here you go.", "thank you"),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    ResponseQualityGuard.without_stale_courtesy(reply, said),
                    reply,
                )

if __name__ == "__main__":
    unittest.main()
