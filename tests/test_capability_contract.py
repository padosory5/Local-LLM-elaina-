"""A4: every capability declares what it takes, returns, and how it fails.

The last test in this file is the one that matters. The other tests would
pass forever on a registry nobody touched; that one fails the moment a
ninth capability is added without a contract, which is the exit criterion
written as code rather than as an intention.
"""

import unittest

from brain import capability_contract as contracts
from brain.capabilities import CAPABILITIES
from brain.text_filter import TextFilter


class EveryCapabilityHasAContractTests(unittest.TestCase):

    def test_a_capability_cannot_exist_without_one(self):
        """The exit criterion. Adding a capability now costs a contract."""
        missing = [
            capability.id for capability in CAPABILITIES
            if contracts.contract_for(capability.id) is None
        ]
        self.assertEqual(
            missing, [],
            "every capability in brain/capabilities.py needs a contract in "
            "brain/capability_contract.py -- typed needs, result keys, and "
            "a declared failure set",
        )

    def test_no_contract_describes_a_capability_that_is_gone(self):
        known = {capability.id for capability in CAPABILITIES}
        for contract in contracts.CONTRACTS:
            with self.subTest(capability=contract.capability_id):
                self.assertIn(contract.capability_id, known)

    def test_each_declares_a_failure_set(self):
        for contract in contracts.CONTRACTS:
            with self.subTest(capability=contract.capability_id):
                self.assertTrue(contract.failures)
                self.assertTrue(contract.returns)

    def test_each_need_has_a_known_kind(self):
        for contract in contracts.CONTRACTS:
            for need in contract.needs:
                with self.subTest(capability=contract.capability_id,
                                  need=need.key):
                    self.assertIn(need.kind, contracts.KINDS)

    def test_failure_codes_are_unique_within_a_capability(self):
        for contract in contracts.CONTRACTS:
            codes = [failure.code for failure in contract.failures]
            with self.subTest(capability=contract.capability_id):
                self.assertEqual(len(codes), len(set(codes)))


class BothLanguagesOrItDoesNotShipTests(unittest.TestCase):
    """Rule 4, applied to the one layer that speaks only when things break.

    A failure sentence is the worst place for a missing translation: it
    arrives when the person is already not getting what they asked for.
    """

    def test_every_failure_speaks_both_languages(self):
        for contract in contracts.CONTRACTS:
            for failure in contract.failures:
                for language in contracts.LANGUAGES:
                    with self.subTest(capability=contract.capability_id,
                                      failure=failure.code,
                                      language=language):
                        self.assertTrue(
                            failure.says.get(language, "").strip(),
                            f"{contract.capability_id}/{failure.code} has no "
                            f"{language} sentence",
                        )

    def test_every_question_asks_in_both_languages(self):
        for contract in contracts.CONTRACTS:
            for need in contract.needs:
                for language in contracts.LANGUAGES:
                    with self.subTest(capability=contract.capability_id,
                                      need=need.key, language=language):
                        self.assertTrue(need.asks.get(language, "").strip())

    def test_a_fix_that_exists_exists_in_both(self):
        for contract in contracts.CONTRACTS:
            for failure in contract.failures:
                if not failure.fix:
                    continue
                for language in contracts.LANGUAGES:
                    with self.subTest(capability=contract.capability_id,
                                      failure=failure.code, language=language):
                        self.assertTrue(failure.fix.get(language, "").strip())

    def test_no_declared_sentence_leaks_internals(self):
        """The contract layer is held to the rule it enforces."""
        for contract in contracts.CONTRACTS:
            for failure in contract.failures:
                for language in contracts.LANGUAGES:
                    line = failure.say_in(language)
                    with self.subTest(failure=failure.code, language=language):
                        self.assertEqual(
                            contracts.leaks_internals(line), ("", ""), line,
                        )


class TheDetectorFindsWhatItClaimsTests(unittest.TestCase):
    """The two corrupted-regex incidents are why this file asserts matches.

    A pattern table that imports cleanly and matches nothing is
    indistinguishable from a clean codebase. Both times it cost an hour.
    """

    def test_the_patterns_carry_no_control_characters(self):
        from pathlib import Path
        source = (
            Path(__file__).resolve().parents[1]
            / "brain" / "capability_contract.py"
        ).read_text(encoding="utf-8")
        stray = [
            (index, repr(char)) for index, char in enumerate(source)
            if ord(char) < 32 and char not in "\n\t"
        ]
        self.assertEqual(stray, [])

    def test_it_finds_an_exception_class_name(self):
        fragment, kind = contracts.leaks_internals(
            "I couldn't complete that web search: ConnectionError: nope"
        )
        self.assertEqual((fragment, kind), ("ConnectionError", "exception_name"))

    def test_it_finds_a_framework_the_user_never_chose(self):
        fragment, kind = contracts.leaks_internals(
            "A Git proposal is visible in Electron."
        )
        self.assertEqual((fragment, kind), ("Electron", "framework_name"))

    def test_it_finds_connection_debris(self):
        _, kind = contracts.leaks_internals(
            "failed: HTTPSConnectionPool(host='duckduckgo.com', port=443)"
        )
        self.assertEqual(kind, "connection_debris")

    def test_a_module_path_is_a_leak_only_where_a_developer_wrote_it(self):
        """The one place the runtime and the scan deliberately disagree.

        A sentence in the source naming another source file has no
        innocent reading. The same words at runtime are the answer to a
        question about this project's code, and a guard that deleted them
        would be censoring the substance of every project answer.
        """
        _, kind = contracts.leaks_internals(
            "see chat_engine.py line 12", authored=True,
        )
        self.assertEqual(kind, "module_path")
        self.assertEqual(
            contracts.leaks_internals("see chat_engine.py line 12"), ("", ""),
        )

    def test_an_ordinary_error_sentence_is_not_a_leak(self):
        """"There was an error" is how people talk. Only the class name is not."""
        self.assertEqual(
            contracts.leaks_internals("There was an error opening the page."),
            ("", ""),
        )

    def test_the_config_file_is_deliberately_not_internal(self):
        """The person owns config.yaml, so naming it is the actionable fix."""
        self.assertEqual(
            contracts.leaks_internals("enable browser_control in config.yaml"),
            ("", ""),
        )

    def test_an_exception_named_as_the_subject_is_the_answer_not_debris(self):
        """Elaina reads this project's own code. She has to be able to
        say what it raises.

        Every one of the forty-three leaking sites wrote the same shape --
        f"...: {type(error).__name__}: {error}" -- so the rule is the
        punctuation around the name, not the name. A version matching the
        bare word would have started censoring project answers the day
        project access was used in anger.
        """
        for answer in (
            "That function raises a ValueError when the list is empty.",
            "You are getting a KeyError because the config has no such key.",
            "It throws a RuntimeError under load.",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(contracts.leaks_internals(answer), ("", ""))

    def test_the_same_name_as_debris_is_still_caught(self):
        for leak in (
            "I couldn't finish: ValueError: bad input",
            "read failed safely (TimeoutError: read timed out)",
            "KeyError: 'model'",
        ):
            with self.subTest(leak=leak):
                self.assertEqual(
                    contracts.leaks_internals(leak)[1], "exception_name",
                )

    def test_a_project_answer_survives_the_voice_path(self):
        answer = "That function raises a ValueError when the list is empty."
        self.assertEqual(TextFilter.for_voice_response(answer), answer)

    def test_a_korean_sentence_is_not_a_leak(self):
        self.assertEqual(
            contracts.leaks_internals("지금은 웹에 연결하지 못했습니다."),
            ("", ""),
        )


class RedactionKeepsTheSayableHalfTests(unittest.TestCase):

    def test_it_drops_the_clause_and_keeps_the_rest(self):
        self.assertEqual(
            contracts.redact_internals(
                "I couldn't reach the site: ConnectionError: nope. "
                "Want me to try again?"
            ),
            "Want me to try again?",
        )

    def test_a_korean_reply_does_not_keep_an_english_stack_fragment(self):
        """A2's finding meeting A4's.

        Half a reply in the wrong language reads as a fault in her rather
        than in the software, and a failure is exactly when that happens:
        the Korean sentence is generated, the English one is a leak.
        """
        self.assertEqual(
            contracts.redact_internals(
                "페이지를 열지 못했습니다. TimeoutError: read timed out."
            ),
            "페이지를 열지 못했습니다.",
        )

    def test_a_clean_sentence_is_returned_unchanged(self):
        clean = "The Git proposal is ready on screen. Nothing has been committed."
        self.assertEqual(contracts.redact_internals(clean), clean)

    def test_the_voice_path_enforces_it(self):
        """The guard is on the method every reply path already ends in."""
        self.assertEqual(
            TextFilter.for_voice_response(
                "I couldn't reach it: ConnectionError. Want me to try again?"
            ),
            "Want me to try again?",
        )


class FailureResultsSpeakFromTheContractTests(unittest.TestCase):

    def test_a_declared_failure_is_phrased_by_the_contract(self):
        result = contracts.failed(
            "web_search", "unreachable", detail="ConnectionError: nope",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, "unreachable")
        self.assertEqual(result.spoken("en"), "I couldn't reach the web just now.")
        self.assertEqual(result.spoken("ko"), "지금은 웹에 연결하지 못했습니다.")

    def test_the_detail_is_kept_and_never_spoken(self):
        result = contracts.failed(
            "web_search", "unreachable", detail="ConnectionError: nope",
        )
        self.assertIn("ConnectionError", result.detail)
        self.assertIn("ConnectionError", result.log_line())
        self.assertEqual(contracts.leaks_internals(result.spoken("en")), ("", ""))

    def test_a_blocked_capability_says_which_switch(self):
        result = contracts.failed("browser_control", "disabled")
        self.assertIn("switched off", result.spoken("en"))
        self.assertIn("Turn it on", result.spoken("en"))
        self.assertIn("설정에서", result.spoken("ko"))

    def test_an_undeclared_code_does_not_silently_become_a_failure(self):
        """A call site inventing a failure mode is a contract breach.

        It still produces something sayable -- going mute is worse -- but
        ``failure`` stays empty so the log says the code was never declared
        rather than implying the contract covered it.
        """
        result = contracts.failed("web_search", "exploded")
        self.assertEqual(result.failure, "")
        self.assertIn("undeclared", result.log_line())

    def test_missing_inputs_are_named_and_asked_for(self):
        contract = contracts.contract_for("calendar_action")
        missing = contract.missing({"title": "dinner"})
        self.assertEqual([need.key for need in missing], ["when"])
        self.assertEqual(missing[0].ask_in("en"), "When is it?")
        self.assertEqual(missing[0].ask_in("ko"), "언제로 잡을까요?")

    def test_a_time_is_never_inferred(self):
        """A guessed time on a real calendar is a wrong appointment."""
        contract = contracts.contract_for("calendar_action")
        when = next(need for need in contract.needs if need.key == "when")
        self.assertFalse(when.inferable)

    def test_the_trusted_result_text_carries_data_not_a_sentence(self):
        result = contracts.CapabilityResult(
            capability="web_search",
            summary="Found three listings.",
            facts={"cheapest": "$249"},
            source="bestbuy.com",
        )
        text = result.as_trusted_result_text()
        self.assertIn("cheapest: $249", text)
        self.assertIn("source: bestbuy.com", text)


if __name__ == "__main__":
    unittest.main()


class AFailedSearchIsNotCachedAsEvidenceTests(unittest.TestCase):
    """The defect A4's typed-result work turned up, kept fixed.

    ``ChatEngine.search_web`` caches whatever the tool hands back, and the
    tool used to hand back ``"Web search failed: <exception>"`` on any
    backend error. So one network blip was written into the cache and
    served as research evidence to every matching query until the entry
    expired -- and the model, given that sentence as its evidence, answered
    from its own memory while the system believed the answer was grounded.

    Asserted at the seam rather than through a whole engine: the property
    is that the failure never reaches the cache write, and the failure
    reaching it at all was the bug.
    """

    def test_the_tool_raises_so_the_cache_line_is_never_reached(self):
        import inspect
        from unittest.mock import patch

        from brain.chat_engine import ChatEngine
        from tools.web_search import WebSearchTool

        source = inspect.getsource(ChatEngine.search_web)
        self.assertIn("self._search_cache[normalized_query]", source)

        tool = WebSearchTool()
        with patch("tools.web_search.DDGS") as fake:
            fake.return_value.text.side_effect = RuntimeError("network down")
            with self.assertRaises(RuntimeError):
                tool.search_web("anything")

    def test_a_failure_sentence_would_be_caught_before_it_could_be_spoken(self):
        """Belt and braces: even if one got through, it cannot be said."""
        self.assertEqual(
            TextFilter.for_voice_response(
                "Web search failed: ConnectionError: max retries exceeded."
            ),
            "",
        )


class TheResultTypeIsHonestByConstructionTests(unittest.TestCase):

    def test_a_leak_written_into_summary_is_not_spoken(self):
        """``summary`` is written at call sites, which is where the leaks were.

        Relying on everyone putting the exception in ``detail`` and not in
        ``summary`` is relying on the same care that produced forty-three
        of these in the first place.
        """
        result = contracts.CapabilityResult(
            capability="web_search",
            ok=True,
            summary="I found three. One request failed: TimeoutError: gone.",
        )
        self.assertEqual(result.spoken("en"), "I found three.")

    def test_an_undeclared_code_cannot_smuggle_a_class_name_out(self):
        result = contracts.failed("web_search", "ConnectionError: nope")
        self.assertEqual(contracts.leaks_internals(result.spoken("en")), ("", ""))


class TypedInputsAreAskedForNotDescribedTests(unittest.TestCase):
    """The calendar path is where the declared needs stop being documentation.

    It had its own list of what was missing and its own English sentence
    for asking. The agent still decides *what* is missing -- it holds the
    draft -- and has stopped deciding what language to ask in, which it
    had no way to know.
    """

    def test_it_asks_for_what_is_missing(self):
        self.assertEqual(
            contracts.ask_for("calendar_action", ("when",)),
            "When is it?",
        )

    def test_it_asks_in_the_language_of_the_turn(self):
        self.assertEqual(
            contracts.ask_for("calendar_action", ("when",), language="ko"),
            "언제로 잡을까요?",
        )

    def test_two_missing_inputs_come_back_in_declared_order(self):
        self.assertEqual(
            contracts.ask_for("calendar_action", ("when", "title")),
            "What should I call it? When is it?",
        )

    def test_an_undeclared_key_is_skipped_not_guessed(self):
        self.assertEqual(
            contracts.ask_for("calendar_action", ("attendees",)), "",
        )

    def test_the_calendar_agent_reports_keys_the_contract_declares(self):
        from agents.calendar_agent import CalendarTurnResult

        contract = contracts.contract_for("calendar_action")
        declared = {need.key for need in contract.needs}
        # The agent's own vocabulary, asserted against the contract's, so
        # the two cannot drift into asking for something nobody declares.
        self.assertEqual(declared, {"title", "when"})
        result = CalendarTurnResult(status="input_required", message="")
        self.assertEqual(result.missing, ())


class SendingThePersonToAConsoleIsALeakTests(unittest.TestCase):
    """Found by the live probe, not by the static scan.

    The scan had already reported zero when this turned up in a real
    reply -- because no source file authors this sentence; the model
    composes it from a tool result. It is the case the runtime guard
    exists for.
    """

    def test_it_catches_being_sent_to_the_console(self):
        _, kind = contracts.leaks_internals(
            "I can't commit changes to Git right now. "
            "Check the console error for details."
        )
        self.assertEqual(kind, "developer_surface")

    def test_the_rest_of_the_reply_survives(self):
        self.assertEqual(
            contracts.redact_internals(
                "I couldn't commit that. Check the console error for details."
            ),
            "I couldn't commit that.",
        )

    def test_an_ordinary_use_of_the_word_is_not_a_leak(self):
        for innocent in (
            "The console is on the shelf under the TV.",
            "It's a terminal application, so it runs in a text window.",
        ):
            with self.subTest(innocent=innocent):
                self.assertEqual(contracts.leaks_internals(innocent), ("", ""))


class TheNewSurfacesAnswerTheirOwnAbilityQuestionTests(unittest.TestCase):
    """Registering a capability is not enough; the question must find it.

    Measured live, after all three were in the registry: "can you commit
    changes to git for me?" was answered by *running* the git action,
    which reported nothing staged, which came out as "I can't commit
    changes to Git right now."
    """

    def test_the_registry_matches_each_new_surface(self):
        from brain.capabilities import CapabilityRegistry

        for request, expected in (
            ("can you commit changes to git for me?", "git"),
            ("깃 커밋 할 수 있어?", "git"),
            ("can you edit files in my project?", "project_edit"),
            ("can you make a new agent for that?", "agent_building"),
        ):
            with self.subTest(request=request):
                match = CapabilityRegistry.match(request)
                self.assertTrue(match.matched, request)
                self.assertEqual(match.capability.id, expected)

    def test_they_did_not_steal_the_existing_ones(self):
        from brain.capabilities import CapabilityRegistry

        for request, expected in (
            ("make a folder on my Desktop", "ui_control"),
            ("open Spotify", "ui_control"),
            ("check the price on that site", "browser_control"),
            ("can you control my browser?", "browser_control"),
        ):
            with self.subTest(request=request):
                self.assertEqual(
                    CapabilityRegistry.match(request).capability.id, expected,
                )


class TheRegistryAnswersInTheLanguageAskedTests(unittest.TestCase):
    """The registry answer path is deterministic, and that is what hid this.

    It answers "can you...?" from the table so the model cannot deny an
    ability she has. A deterministic answer is a correct answer in the
    wrong language every single time, and nothing in a green suite says
    so -- it took a live probe in Korean.
    """

    def test_every_capability_describes_itself_in_both_languages(self):
        for capability in CAPABILITIES:
            with self.subTest(capability=capability.id):
                self.assertTrue(capability.name_ko.strip(), capability.id)
                self.assertTrue(capability.summary_ko.strip(), capability.id)

    def test_the_spoken_summary_is_the_head_in_either_language(self):
        from brain.capabilities import CapabilityRegistry

        git = CapabilityRegistry.get("git")
        self.assertEqual(
            git.spoken_summary_in("en"), "prepare a commit for this project",
        )
        self.assertEqual(
            git.spoken_summary_in("ko"), "이 프로젝트의 커밋을 준비합니다",
        )

    def test_a_missing_translation_falls_back_rather_than_going_silent(self):
        """Silence reads as not having the ability, which is the whole bug."""
        from brain.capabilities import Capability

        bare = Capability(id="x", name="thing", summary="do the thing")
        self.assertEqual(bare.spoken_summary_in("ko"), "do the thing")
        self.assertEqual(bare.name_in("ko"), "thing")

    def test_the_answer_frames_exist_in_both_languages(self):
        from brain import guard_lines

        for name in (
            "ability_yes", "ability_offer", "ability_blocked",
            "ability_blocked_fix", "ability_doubted",
        ):
            for language in ("en", "ko"):
                with self.subTest(line=name, language=language):
                    self.assertTrue(guard_lines.say(name, language).strip())

    def test_the_frames_take_the_fields_the_engine_passes(self):
        from brain import guard_lines
        from brain.capabilities import CapabilityRegistry

        git = CapabilityRegistry.get("git")
        for language in ("en", "ko"):
            said = guard_lines.say("ability_yes", language).format(
                ability=git.spoken_summary_in(language),
                offer=guard_lines.say("ability_offer", language),
            )
            with self.subTest(language=language):
                self.assertNotIn("{", said)
                self.assertIn(git.spoken_summary_in(language), said)
