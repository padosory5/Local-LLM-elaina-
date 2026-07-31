import unittest

from brain.attention import Attention


class AttentionTests(unittest.TestCase):
    def test_old_technical_topic_does_not_leak_into_new_turn(self):
        attention = Attention()
        attention.update("I'm working in Unity.")
        self.assertIn("Unity", attention.build_context())

        attention.update("What should I eat tonight?")

        self.assertEqual(attention.build_context(), "")


if __name__ == "__main__":
    unittest.main()
