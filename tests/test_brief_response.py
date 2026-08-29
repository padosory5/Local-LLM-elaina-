import unittest
from collections import Counter

from brain.brief_response import BriefResponseGenerator


class FakeClient:
    def __init__(self, replies):
        self.replies = iter(replies)

    def chat(self, **_kwargs):
        return {"message": {"content": next(self.replies)}}


class BriefResponseGeneratorTests(unittest.TestCase):
    def test_valid_short_success_is_preserved(self):
        generator = BriefResponseGenerator(FakeClient(["Opening Discord now."]), "test")

        reply = generator.generate("opened", subject="Discord")

        self.assertEqual(reply, "Opening Discord now.")

    def test_repeated_response_uses_a_different_safe_fallback(self):
        generator = BriefResponseGenerator(FakeClient(["On it.", "On it."]), "test")

        first = generator.generate("opened", subject="Discord")
        second = generator.generate("opened", subject="Steam")

        self.assertNotEqual(first.casefold(), second.casefold())

    def test_generic_closing_is_removed(self):
        generator = BriefResponseGenerator(
            FakeClient(["Discord is open. Let me know if you need anything else."]),
            "test",
        )

        reply = generator.generate("opened", subject="Discord")

        self.assertNotIn("let me know", reply.casefold())
        self.assertLessEqual(len(reply.split()), 6)

    def test_failure_cannot_be_rephrased_as_success(self):
        generator = BriefResponseGenerator(FakeClient(["Battle.net is open."]), "test")

        reply = generator.generate("not_found", subject="Battle.net")

        self.assertTrue(any(
            phrase in reply.casefold()
            for phrase in ("couldn't", "can't", "isn't", "not found")
        ))

    def test_control_mode_off_recommends_the_visible_toggle(self):
        generator = BriefResponseGenerator(
            FakeClient(["Enable Computer Control to open Discord."]),
            "test",
        )

        reply = generator.generate(
            "control_mode_off",
            subject="Discord",
            operation="open_app",
        )

        self.assertIn("computer control", reply.casefold())
        self.assertIn("discord", reply.casefold())
        self.assertNotIn("opened", reply.casefold())
        self.assertFalse(reply.endswith("?"))

    def test_control_mode_off_handles_a_spoken_domain(self):
        generator = BriefResponseGenerator(
            FakeClient(["Enable Computer Control to open github.com."]),
            "test",
        )

        reply = generator.generate(
            "control_mode_off",
            subject="github.com",
            operation="open_url",
        )

        self.assertEqual(reply, "Enable Computer Control to open github.com.")

    def test_delete_offer_names_target_and_recoverable_action(self):
        generator = BriefResponseGenerator(
            FakeClient(["Recycle Notes now?"]),
            "test",
        )

        reply = generator.generate(
            "delete_offer",
            subject="Notes",
            operation="delete_folder",
        )

        self.assertEqual(reply, "Recycle Notes now?")

    def test_repeated_model_acknowledgement_rotates_safe_success_lines(self):
        generator = BriefResponseGenerator(
            FakeClient(["Got it.", "Got it.", "Got it."]),
            "test",
        )

        replies = {
            generator.generate("opened", subject="Discord", operation="open_app"),
            generator.generate("opened", subject="Steam", operation="open_app"),
            generator.generate("opened", subject="Spotify", operation="open_app"),
        }

        self.assertEqual(len(replies), 3)

    def test_force_quit_offer_cannot_claim_the_app_closed(self):
        generator = BriefResponseGenerator(
            FakeClient(["Force quit Discord? Unsaved work?"]),
            "test",
        )

        reply = generator.generate("force_quit_offer", subject="Discord")

        self.assertIn("force quit", reply.casefold())
        self.assertTrue(reply.endswith("?"))

    def test_not_running_cannot_be_rephrased_as_closed_success(self):
        generator = BriefResponseGenerator(
            FakeClient(["Discord closed successfully."]),
            "test",
        )

        reply = generator.generate("not_running", subject="Discord")

        self.assertTrue(any(
            phrase in reply.casefold()
            for phrase in ("isn't", "can't", "already closed")
        ))

    def test_starting_work_is_no_longer_this_module_s_job(self):
        # "work_started" moved to brain.action_status, which chooses locally
        # instead of spending a model call on the line that covers a wait.
        # What has to hold here is that the kind is gone and an unknown kind
        # degrades to the safe one rather than being invented.
        generator = BriefResponseGenerator(
            FakeClient(["A far too long reply that will never pass validation"]),
            "test",
        )

        self.assertNotIn("work_started", generator._FALLBACKS)

        reply = generator.generate("work_started", detail="Web search starting.")

        self.assertIn(reply, generator._FALLBACKS["blocked"])


class AlwaysInvalidClient:
    """Forces the fallback path -- what runs whenever the model is unusable."""

    def chat(self, **_kwargs):
        return {"message": {"content": "a reply far too long to ever pass"}}


class OpeningVarietyTests(unittest.TestCase):
    """The repetition you actually hear.

    These are the lines she speaks after an action, and three stock words
    used to open twelve of every twenty: the fallback scan always started at
    index 0 where the stock opener sat, only whitelisted openers were tracked
    as repetition at all, and the prompt itself listed those words as
    examples to use.
    """

    SCRIPT = (
        ("opened", "Spotify", "open_app"),
        ("opened", "Discord", "open_app"),
        ("url_opened", "github.com", "open_url"),
        ("closed", "Steam", "close_app"),
        ("folder_created", "Trip", "create_folder"),
        ("opened", "Chrome", "open_app"),
        ("file_created", "notes.txt", "create_file"),
        ("closed", "Discord", "close_app"),
        ("url_opened", "naver.com", "open_url"),
        ("opened", "Notepad", "open_app"),
        ("folder_created", "Notes", "create_folder"),
        ("force_quit", "Discord", "force_quit_app"),
        ("opened", "Steam", "open_app"),
        ("file_deleted", "old.txt", "delete_file"),
        ("url_opened", "youtube.com", "open_url"),
        ("closed", "Spotify", "close_app"),
        ("opened", "Battle.net", "open_app"),
        ("folder_created", "Trip", "create_folder"),
        ("file_created", "todo.txt", "create_file"),
        ("closed", "Chrome", "close_app"),
    )

    def _run(self):
        generator = BriefResponseGenerator(AlwaysInvalidClient(), "test")
        lines = [
            generator.generate(kind, subject=subject, operation=operation)
            for kind, subject, operation in self.SCRIPT
        ]
        openings = [generator._opening(line) for line in lines]
        return generator, lines, openings

    def test_no_single_word_opens_more_than_a_sixth_of_the_lines(self):
        _generator, lines, openings = self._run()
        counts = Counter(openings)

        worst, count = counts.most_common(1)[0]

        self.assertLessEqual(
            count, len(lines) // 6,
            f"{worst!r} opened {count} of {len(lines)}: "
            + " | ".join(lines),
        )

    def test_the_stock_openers_no_longer_dominate(self):
        _generator, lines, openings = self._run()

        stock = sum(
            1 for opening in openings
            if opening in {"got", "sure", "done", "okay", "all"}
        )

        # Twelve of twenty before this change.
        self.assertLessEqual(stock, len(lines) // 2, " | ".join(lines))

    def test_most_lines_start_a_different_way(self):
        _generator, lines, openings = self._run()

        self.assertGreaterEqual(
            len(set(openings)), 12,
            f"only {len(set(openings))} distinct openings: " + " | ".join(lines),
        )

    def test_an_unremarkable_opening_still_counts_as_repetition(self):
        # "Opened Discord" recorded nothing, so it never displaced "sure"
        # from the window. Every line competes for the budget now.
        generator = BriefResponseGenerator(AlwaysInvalidClient(), "test")

        self.assertEqual(generator._opening("Opened Discord."), "opened")
        self.assertEqual(generator._opening("Created the Trip folder."), "created")
        self.assertEqual(generator._opening("Sure, it's ready."), "sure")
        self.assertEqual(generator._opening(""), "")

    def test_the_prompt_no_longer_teaches_the_model_the_stock_openers(self):
        generator = BriefResponseGenerator(AlwaysInvalidClient(), "test")
        generator.generate("opened", subject="Spotify", operation="open_app")

        prompt = generator._prompt("opened", "Discord", "", "open_app")

        self.assertNotIn("got it, done, all set", prompt.casefold())
        self.assertIn("do not start with:", prompt.casefold())
        # What it just used has to reach the model as something to avoid.
        for opening in generator._recent_openings_for("opened"):
            self.assertIn(opening, prompt.casefold())


class RefusalTruthfulnessTests(unittest.TestCase):
    """A refused action must never come back sounding like a success.

    "blocked" is the kind every unrecognized kind degrades to, so it is the
    one that decides what happens when a caller asks for something this class
    does not know about. It used to have no validation at all: whatever the
    model said was returned, including a claim that the work was done.
    """

    def test_a_blocked_action_cannot_claim_success(self):
        generator = BriefResponseGenerator(FakeClient(["Done, I found it."]), "test")

        reply = generator.generate("blocked", subject="that")

        self.assertIn(reply, generator._FALLBACKS["blocked"])
        self.assertNotIn("found it", reply.casefold())

    def test_a_blocked_action_is_stated_not_asked(self):
        generator = BriefResponseGenerator(FakeClient(["Should I try that?"]), "test")

        reply = generator.generate("blocked", subject="that")

        self.assertNotIn("?", reply)

    def test_an_honest_refusal_is_still_allowed_through(self):
        generator = BriefResponseGenerator(
            FakeClient(["I can't do that one yet."]), "test",
        )

        reply = generator.generate("blocked", subject="that")

        self.assertEqual(reply, "I can't do that one yet.")

    def test_every_failure_report_reads_as_negative(self):
        # The live check kept a shorter copy of this vocabulary and failed
        # "That app is missing." -- one of this class's own not_found lines.
        # Asserting the class agrees with itself stops that drifting again.
        #
        # Only the kinds that genuinely *report a failure* are checked here.
        # not_running, invalid_target and outside_allowed also demand a
        # negative phrase in _valid, but some of their own fallbacks are
        # corrective rather than negative ("Choose Desktop, Documents, or
        # Downloads."), so requiring it of them asserts something this class
        # does not currently believe. See test_a_corrective_line_is_not_a_
        # failure_report below.
        generator = BriefResponseGenerator(FakeClient([]), "test")

        for kind in (
            "not_found", "item_not_found", "failed", "declined",
            "wrong_type", "blocked",
        ):
            for template in generator._FALLBACKS[kind]:
                option = template.format(subject="Discord", action="open")
                with self.subTest(kind=kind, option=option):
                    self.assertTrue(
                        generator.reads_as_negative(option),
                        f"{option!r} would be rejected as not negative",
                    )

    def test_the_ordinary_contractions_all_count(self):
        # "don't" was missing from a list that already had didn't, isn't,
        # won't, can't and couldn't, so "File and folder types don't match."
        # -- one of this class's own wrong_type lines -- did not read as a
        # failure at all.
        generator = BriefResponseGenerator(FakeClient([]), "test")

        for line in (
            "File and folder types don't match.",
            "That doesn't exist here.",
            "That isn't allowed.",
            "I can't do that.",
        ):
            with self.subTest(line=line):
                self.assertTrue(generator.reads_as_negative(line))

    def test_a_corrective_line_is_not_a_failure_report(self):
        # Documenting a known asymmetry rather than hiding it: these three
        # kinds require a negative phrase in _valid, so a model line shaped
        # like their own fallback would be rejected and replaced by that very
        # fallback. Harmless today -- each kind keeps two options that do read
        # as negative -- but it is the wrong requirement for a corrective
        # line, and this test will fail loudly if the concept is reworked.
        generator = BriefResponseGenerator(FakeClient([]), "test")

        for line in (
            "I need a valid target.",
            "Choose Desktop, Documents, or Downloads.",
            "Discord is already closed.",
        ):
            with self.subTest(line=line):
                self.assertFalse(generator.reads_as_negative(line))

    def test_every_blocked_fallback_passes_its_own_validation(self):
        generator = BriefResponseGenerator(FakeClient([]), "test")

        for option in generator._FALLBACKS["blocked"]:
            with self.subTest(option=option):
                self.assertTrue(generator._valid(option, "blocked", "that", ""))

    def test_a_requested_close_cannot_claim_the_app_is_closed(self):
        # The prompt says "do not claim full exit"; nothing enforced it.
        generator = BriefResponseGenerator(
            FakeClient(["Discord is closed."]), "test",
        )

        reply = generator.generate(
            "close_requested", subject="Discord", operation="close_app",
        )

        self.assertIn(reply, [
            option.format(subject="Discord", action="close")
            for option in generator._FALLBACKS["close_requested"]
        ])

    def test_a_requested_close_still_reports_the_request(self):
        generator = BriefResponseGenerator(
            FakeClient(["Asking Discord to close."]), "test",
        )

        reply = generator.generate(
            "close_requested", subject="Discord", operation="close_app",
        )

        self.assertEqual(reply, "Asking Discord to close.")


if __name__ == "__main__":
    unittest.main()
