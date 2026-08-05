import unittest

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

    def test_agent_start_cannot_claim_work_finished(self):
        generator = BriefResponseGenerator(FakeClient(["Done, I found it."]), "test")

        reply = generator.generate("work_started", detail="Web search starting.")

        self.assertNotIn("done", reply.casefold())
        self.assertNotIn("found it", reply.casefold())


if __name__ == "__main__":
    unittest.main()
