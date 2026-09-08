"""What one turn's prompt carries, decided once instead of per path.

There are three ways a prompt gets assembled in ``ChatEngine._answer_turn``
-- a plain conversational one, a factual one with evidence, and a trusted
tool result -- and until now each decided for itself what context to carry.
They did not agree:

===========================  ===========================================
``conversation.build_messages``  history dropped on ``route.topic_shift``,
                                 which the *model* sets and often does not
``_build_factual_messages``      history dropped on ``reset_history``,
                                 which the caller passes
``_build_tool_result_messages``  **no history control at all** -- it takes
                                 the whole conversation, always
===========================  ===========================================

That third row is not a tidiness problem. Measured live, three sessions
apart:

    User:   i had a rough night / just couldn't sleep / yeah
    User:   what's 2+2
    Elaina: That's straightforward.

The router classified it as a calculation, correctly, and a successful
calculation plan builds *trusted-result* messages -- the one path that
cannot drop history. Four turns of sympathy went into the prompt, and the
number never came out. A rule added to the other two paths could not
possibly have fixed it, and did not: it never fired.

So the decision moves up. One :class:`TurnContext` per turn, built before
the branching, read by every path. What a turn inherits stops being an
emergent property of which branch it happened to take.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnContext:
    """The context this turn's prompt is allowed to carry.

    Deliberately small, and deliberately not the whole prompt: the builders
    still know how to write their own sections. This is only the set of
    decisions that *all three* of them have to make the same way, which is
    exactly the set that was being made differently.
    """

    #: The request being answered, already resolved by the router.
    question: str = ""

    #: Whether the conversation so far belongs to this turn. False starts
    #: the prompt clean -- the turn is about something else.
    inherit_history: bool = True

    #: Whether previously verified evidence is about this subject.
    include_grounded: bool = False

    #: What a subjectless follow-up ("which one would you choose?") is
    #: about, when the turn cannot say for itself.
    followup_subject: str = ""

    #: Why history is not being inherited, for the log. Empty when it is.
    reason: str = ""

    #: The two subjects the decision was made from, so a log line can say
    #: why it went the way it did. Written after the fact: the first
    #: version of this rule reported only when it *acted*, so a rule that
    #: never fired and a rule that was never reached looked identical --
    #: and the difference was the whole question.
    held_subject: str = ""
    current_subject: str = ""

    def log_line(self) -> str:
        if not self.inherit_history:
            return f"[Context] Starting clean: {self.reason}"
        return (
            "[Context] Inheriting the conversation "
            f"(was {self.held_subject or '(nothing held)'}, "
            f"now {self.current_subject or '(unknown)'})"
        )

    @property
    def history_for_builder(self):
        """``[]`` to start clean, ``None`` to use the conversation.

        The shape ``ConversationManager.build_messages`` already expects,
        so the decision travels without every caller restating it.
        """
        return None if self.inherit_history else []
