import unittest

from brain.conversation_manager import ConversationManager
from brain.response_messages import build_personality_messages


class PersonalityRoutingTests(unittest.TestCase):
    def test_topic_shift_can_exclude_old_conversation_history(self):
        conversation = ConversationManager()
        conversation.add("user", "Who is this?")
        conversation.add("assistant", "That is Eros.")

        messages = conversation.build_messages(
            system_prompt="PERSONALITY",
            context_prompt="Should I use Live2D or 3D?",
            history=[],
        )

        self.assertNotIn("Eros", str(messages))
        self.assertIn("Live2D", messages[-1]["content"])

    def test_factual_answer_keeps_personality_as_only_system_prompt(self):
        messages = build_personality_messages(
            system_prompt="PERSONALITY FILE CONTENT",
            history=[],
            user_input="Who won?",
            context_sections=(
                ("CURRENT RETRIEVED EVIDENCE", "Spain won."),
            ),
        )

        system_messages = [
            message
            for message in messages
            if message["role"] == "system"
        ]
        self.assertEqual(
            system_messages,
            [{
                "role": "system",
                "content": "PERSONALITY FILE CONTENT",
            }],
        )
        self.assertIn("Spain won.", messages[-1]["content"])

    def test_tool_result_keeps_personality_as_only_system_prompt(self):
        messages = build_personality_messages(
            system_prompt="PERSONALITY FILE CONTENT",
            history=[],
            user_input="Change the button.",
            context_sections=(
                (
                    "TRUSTED TOOL RESULT",
                    "A proposal is waiting. Nothing changed.",
                ),
            ),
        )

        system_messages = [
            message
            for message in messages
            if message["role"] == "system"
        ]
        self.assertEqual(len(system_messages), 1)
        self.assertEqual(
            system_messages[0]["content"],
            "PERSONALITY FILE CONTENT",
        )
        self.assertIn("Nothing changed.", messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
