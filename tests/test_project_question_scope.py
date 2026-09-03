""""Project" is an ordinary English word, and the model reads it as one.

B-41, from session 2:

    User:   I'm interested in like AI software companies.
    [Goal] Intent: inspect
    Candidates: project_question: 1.00
    [Router] project_question (0.95): ... which is a specific
             project-related inquiry.
    [Agent] Coding Agent accepted task...
    [Project MCP] Researching: I'm interested in AI Software Engineering
                  Internship Applications companies.

The Coding Agent went and searched Elaina's own source tree for internship
employers. ``project_question`` means one thing -- the local codebase --
and a turn that names nothing in it is not asking about it.
"""

import unittest

from brain.intent_router import IntentDecision, SemanticIntentRouter


def routed(request: str, said: str = "") -> str:
    decision = IntentDecision(
        intent="project_question",
        confidence=0.95,
        normalized_request=request,
        reason="test",
        action_requested=True,
        action_target=request,
    )
    return SemanticIntentRouter._apply_optional_capability_policy(
        decision, original_input=said or request,
    ).intent


class NotAboutTheCodebaseTests(unittest.TestCase):

    def test_the_live_turn_is_not_a_project_question(self):
        self.assertEqual(
            routed("I'm interested in like AI software companies."),
            "conversation",
        )

    def test_the_world_is_not_the_repository(self):
        for said in (
            "companies that offer AI Software Engineering Internships",
            "what should I cook tonight",
            "tell me about the Seattle housing project",
            "which software companies are hiring interns",
            "I want a project management job",
        ):
            with self.subTest(said=said):
                self.assertEqual(routed(said), "conversation", said)


class StillAboutTheCodebaseTests(unittest.TestCase):
    """The half that must not be lost."""

    def test_a_real_question_about_the_code_survives(self):
        for said in (
            "what does main.py do",
            "how does this project handle routing",
            "show me the tests for the router",
            "where is the ChatEngine class defined",
            "what files are in the repo",
            "explain the traceback",
            "is there a function that reads the config",
            "코드에서 라우터 어디 있어",
        ):
            with self.subTest(said=said):
                self.assertEqual(routed(said), "project_question", said)


class TheParaphraseIsNotTheOnlyEvidenceTests(unittest.TestCase):
    """The model's normalisation can drop the word that made it one."""

    def test_the_original_transcript_still_counts(self):
        self.assertEqual(
            routed(
                "Explain the voice input flow.",
                said="Inspect the codebase and explain how voice input "
                     "reaches chat.",
            ),
            "project_question",
        )


if __name__ == "__main__":
    unittest.main()
