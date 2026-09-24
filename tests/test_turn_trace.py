"""The turn record: complete, checkable, and invisible to the turn.

core/turn_trace.py exists because the answer to "what did the model say,
and which stage turned it into this?" used to be gone by the time anyone
asked. These tests hold it to three promises:

* **Complete.** Every rewrite of the reply in ``_answer_turn`` is recorded
  -- checked statically on the source, and dynamically by the chain, which
  notices a rewrite that was not recorded.
* **Faithful.** Model calls, printed reasons, speech and evidence land in
  the record as they happened.
* **Invisible.** With the record on or off, a turn says the same thing and
  makes the same model calls in the same order.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import turn_trace
from core.turn_trace import TracingClient, TurnTrace


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _RecordingModule(unittest.TestCase):
    """Tracing on, nothing written unless a test asks for it."""

    def setUp(self) -> None:
        self._saved = (
            turn_trace.ENABLED, turn_trace.PERSIST,
            turn_trace.DIRECTORY, turn_trace.BANK_DIRECTORY,
        )
        folder = Path(tempfile.mkdtemp(prefix="elaina-trace-test-"))
        turn_trace.ENABLED = True
        turn_trace.PERSIST = False
        turn_trace.DIRECTORY = folder / "turn_trace"
        turn_trace.BANK_DIRECTORY = folder / "failure_bank"
        self.folder = folder

    def tearDown(self) -> None:
        # A test that failed half-way must not leave its record open for
        # the next one to write into.
        leftover = turn_trace._mine()
        if leftover is not None:
            turn_trace.finish(leftover)
        (
            turn_trace.ENABLED, turn_trace.PERSIST,
            turn_trace.DIRECTORY, turn_trace.BANK_DIRECTORY,
        ) = self._saved


# ------------------------------------------------------------------ the chain


class ChainTests(unittest.TestCase):

    def test_a_stage_that_changes_the_reply_is_recorded_with_both_texts(self):
        trace = TurnTrace("what is a taylor series")
        trace.record_draft("A Taylor series is a sum. Anything else?", source="model")
        trace.record_step(
            "speech_filter",
            "A Taylor series is a sum. Anything else?",
            "A Taylor series is a sum.",
        )
        step = trace.steps[0]
        self.assertTrue(step["changed"])
        self.assertEqual(step["before"], "A Taylor series is a sum. Anything else?")
        self.assertEqual(step["after"], "A Taylor series is a sum.")
        self.assertTrue(trace.chain_ok)
        self.assertTrue(trace.mutated)

    def test_a_stage_that_changes_nothing_is_counted_without_its_text(self):
        trace = TurnTrace("hi")
        trace.record_draft("Hello.", source="model")
        trace.record_step("grounded_values", "Hello.", "Hello.")
        trace.record_step("grounded_values", "Hello.", "Hello.")
        self.assertEqual(trace.runs["grounded_values"], 2)
        self.assertNotIn("before", trace.steps[0])
        self.assertFalse(trace.mutated)

    def test_a_rewrite_nobody_recorded_breaks_the_chain(self):
        trace = TurnTrace("hi")
        trace.record_draft("first", source="model")
        # Something turned "first" into "second" without a step, and the
        # next stage was handed "second".
        trace.record_step("closing_offer", "second", "second")
        self.assertFalse(trace.chain_ok)
        gap = trace.steps[0]
        self.assertTrue(gap["gap"])
        self.assertEqual((gap["before"], gap["after"]), ("first", "second"))
        self.assertIn("closing_offer", gap["name"])

    def test_what_was_displayed_must_be_what_the_last_stage_returned(self):
        trace = TurnTrace("hi")
        trace.record_draft("one", source="model")
        trace.record_step("speech_filter", "one", "two")
        trace.record_display("three")
        self.assertFalse(trace.chain_ok)
        trace_ok = TurnTrace("hi")
        trace_ok.record_draft("one", source="model")
        trace_ok.record_step("speech_filter", "one", "two")
        trace_ok.record_display("two")
        self.assertTrue(trace_ok.chain_ok)


class OutsideATurnTests(unittest.TestCase):

    def test_step_hands_back_what_it_was_given_when_nothing_is_recording(self):
        self.assertIsNone(turn_trace._mine())
        value = object()
        self.assertIs(turn_trace.step("anything", "before", value), value)

    def test_nothing_is_opened_when_tracing_is_off(self):
        with patch.object(turn_trace, "ENABLED", False):
            self.assertIsNone(turn_trace.begin("hello"))

    def test_an_empty_turn_is_not_a_turn(self):
        self.assertIsNone(turn_trace.begin("   "))


# ------------------------------------------------------------- what it keeps


class PrintedReasonTests(_RecordingModule):

    def test_what_a_stage_printed_is_kept_as_its_reason(self):
        console = io.StringIO()
        with contextlib.redirect_stdout(console):
            trace = turn_trace.begin("I like strawberries.")
            turn_trace.draft("You're welcome! Strawberries are great.", source="model")
            print("\n[Response Guard] The retry opened with a courtesy nobody had earned; removed it.")
            turn_trace.step(
                "repetition_retry",
                "You're welcome! Strawberries are great.",
                "Strawberries are great.",
            )
            print("some unrelated line")
            turn_trace.finish(trace)
        step = trace.steps[0]
        self.assertEqual(
            step["log"],
            ["[Response Guard] The retry opened with a courtesy nobody had earned; removed it."],
        )
        # The console still got every word.
        self.assertIn("courtesy nobody had earned", console.getvalue())
        self.assertIn("some unrelated line", console.getvalue())
        self.assertIn("some unrelated line", trace.log)


class _ScriptedModel:
    def __init__(self, reply="Sure.", chunks=None, error=None):
        self.reply = reply
        self.chunks = chunks
        self.error = error
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if kwargs.get("stream"):
            return iter(self.chunks or [{"message": {"content": self.reply}}])
        return {
            "message": {"content": self.reply},
            "done_reason": "stop",
            "eval_count": 30,
            "eval_duration": 600_000_000,
            "prompt_eval_count": 900,
        }

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": self.reply}


class ModelCallTests(_RecordingModule):

    def _messages(self, system="You are Elaina."):
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "older follow-up"},
            {"role": "assistant", "content": "older reply"},
            {"role": "user", "content": "EVIDENCE\nfive snippets\n\nCURRENT USER MESSAGE\nwhen are taylor series used?"},
        ]

    def test_a_call_is_passed_through_and_written_into_the_turn(self):
        model = _ScriptedModel(reply="Here is an example.")
        client = TracingClient(model)
        trace = turn_trace.begin("when are taylor series used?")
        response = client.chat(
            model="qwen3:8b", messages=self._messages(),
            options={"temperature": 0.1}, stream=False, think=False,
        )
        turn_trace.finish(trace)

        self.assertEqual(response["message"]["content"], "Here is an example.")
        self.assertEqual(len(model.calls), 1)
        call = trace.model_calls[0]
        self.assertEqual(call["model"], "qwen3:8b")
        self.assertTrue(call["caller"].endswith(
            "test_a_call_is_passed_through_and_written_into_the_turn"))
        self.assertEqual(call["output"], "Here is an example.")
        self.assertEqual(call["tokens_per_second"], 50.0)
        self.assertEqual(call["prompt_eval_count"], 900)
        self.assertEqual(call["options"], {"temperature": 0.1})
        # The system prompt in full, the history by count and its last two
        # messages, and the message that carried this call's evidence.
        messages = call["messages"]
        self.assertEqual(messages[0]["content"], "You are Elaina.")
        self.assertEqual(call["history"], 4)
        self.assertEqual(
            [m["content"] for m in messages[1:3]],
            ["older follow-up", "older reply"],
        )
        self.assertIn("five snippets", messages[-1]["content"])

    def test_a_streamed_answer_arrives_chunk_for_chunk(self):
        chunks = [
            {"message": {"content": "Say a computer "}},
            {"message": {"content": "needs e^0.1."}},
            {"message": {"content": ""}, "done": True, "done_reason": "stop",
             "eval_count": 40, "eval_duration": 800_000_000},
        ]
        client = TracingClient(_ScriptedModel(chunks=chunks))
        trace = turn_trace.begin("example?")
        received = list(client.chat(model="m", messages=self._messages(), stream=True))
        turn_trace.finish(trace)

        self.assertEqual(len(received), 3)
        for got, sent in zip(received, chunks):
            self.assertIs(got, sent)
        call = trace.model_calls[0]
        self.assertEqual(call["output"], "Say a computer needs e^0.1.")
        self.assertEqual(call["done_reason"], "stop")
        self.assertEqual(call["tokens_per_second"], 50.0)
        self.assertIn("ttft", call)

    def test_a_failed_call_is_recorded_and_still_raised(self):
        client = TracingClient(_ScriptedModel(error=ConnectionError("refused")))
        trace = turn_trace.begin("hello")
        with self.assertRaises(ConnectionError):
            client.chat(model="m", messages=self._messages())
        turn_trace.finish(trace)
        self.assertIn("ConnectionError: refused", trace.model_calls[0]["error"])

    def test_outside_a_turn_nothing_is_recorded(self):
        model = _ScriptedModel()
        client = TracingClient(model)
        self.assertIsNone(turn_trace.current())
        client.chat(model="m", messages=self._messages())
        client.generate(model="m", prompt="")
        self.assertEqual(len(model.calls), 2)

    def test_the_wrapped_client_is_still_the_one_configured(self):
        model = _ScriptedModel()
        client = TracingClient(model)
        client.reply = "changed through the wrapper"
        self.assertEqual(model.reply, "changed through the wrapper")
        self.assertIs(client.calls, model.calls)
        self.assertIs(client.wrapped, model)

    def test_a_repeated_system_prompt_is_kept_once_per_turn(self):
        client = TracingClient(_ScriptedModel())
        trace = turn_trace.begin("hello")
        client.chat(model="m", messages=self._messages("A long persona."))
        client.chat(model="m", messages=self._messages("A long persona."))
        turn_trace.finish(trace)
        first, second = (call["messages"][0] for call in trace.model_calls)
        self.assertEqual(first["sha1"], second["sha1"])
        self.assertEqual(first["content"], "A long persona.")
        self.assertNotIn("content", second)


class SpeechTests(_RecordingModule):

    def test_what_the_voice_was_handed_is_recorded(self):
        from config.loader import Config
        from voice.audio_manager import AudioManager

        config = Config()
        config.data = copy.deepcopy(config.data)
        config.data["tts"]["enabled"] = False
        with patch("voice.audio_manager.threading.Thread"):
            audio = AudioManager(config=config)
        trace = turn_trace.begin("example?")
        said = (
            "Say a computer needs e^0.1, which it cannot look up anywhere. "
            "A few terms of 1 + x + x²/2! get it there."
        )
        audio.speak(said)
        turn_trace.finish(trace)

        speech = trace.speech[0]
        self.assertEqual(speech["said"], said)
        self.assertTrue(speech["spoken"])
        self.assertGreaterEqual(len(speech["chunks"]), 1)
        # The same symbols reach the voice today; that is what the record
        # is for.
        self.assertIn("x²/2!", speech["spoken"])


# ------------------------------------------------------------------ the files


class WriterTests(_RecordingModule):

    def setUp(self) -> None:
        super().setUp()
        turn_trace.PERSIST = True

    def _turn(self, *, changed: bool) -> TurnTrace:
        trace = turn_trace.begin("what time is it?")
        turn_trace.draft(
            "It's 10:38 PM in Pacific Daylight Time, UTC-0700.", source="model",
        )
        after = (
            "It's 10:38 PM in Pacific Daylight Time, UTC-0700."
            if not changed else "It's 10:38 PM."
        )
        turn_trace.step(
            "speech_filter",
            "It's 10:38 PM in Pacific Daylight Time, UTC-0700.",
            after,
        )
        turn_trace.display(after)
        turn_trace.finish(trace)
        self.assertTrue(turn_trace.flush(5.0))
        return trace

    def _lines(self, directory: Path, trace: TurnTrace) -> list[dict]:
        path = directory / f"{trace.started_at[:10]}.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if json.loads(line)["turn_id"] == trace.turn_id
        ]

    def test_every_turn_is_written_and_only_rewritten_ones_are_banked(self):
        rewritten = self._turn(changed=True)
        untouched = self._turn(changed=False)

        record = self._lines(turn_trace.DIRECTORY, rewritten)[0]
        self.assertEqual(record["schema"], turn_trace.SCHEMA)
        self.assertEqual(record["user_input"], "what time is it?")
        self.assertTrue(record["mutated"])
        self.assertTrue(record["chain_ok"])
        self.assertEqual(record["display"], "It's 10:38 PM.")

        finding = self._lines(turn_trace.BANK_DIRECTORY, rewritten)[0]
        self.assertEqual(
            [(f["name"], f["after"]) for f in finding["findings"]],
            [("speech_filter", "It's 10:38 PM.")],
        )
        self.assertEqual(len(self._lines(turn_trace.DIRECTORY, untouched)), 1)
        self.assertEqual(self._lines(turn_trace.BANK_DIRECTORY, untouched), [])

    def test_old_days_are_deleted(self):
        turn_trace.DIRECTORY.mkdir(parents=True, exist_ok=True)
        old = turn_trace.DIRECTORY / "2000-01-01.jsonl"
        old.write_text("{}\n", encoding="utf-8")
        not_a_day = turn_trace.DIRECTORY / "notes.jsonl"
        not_a_day.write_text("{}\n", encoding="utf-8")
        turn_trace._pruned.discard(turn_trace.DIRECTORY)
        self._turn(changed=False)
        self.assertFalse(old.exists())
        self.assertTrue(not_a_day.exists())

    def test_a_directory_that_cannot_be_written_never_reaches_the_turn(self):
        blocker = self.folder / "blocked"
        blocker.write_text("a file where the directory should be", encoding="utf-8")
        turn_trace.DIRECTORY = blocker / "turn_trace"
        turn_trace.BANK_DIRECTORY = blocker / "failure_bank"
        self._turn(changed=True)  # must not raise


# -------------------------------------------------------------- whole turns


def _traced(engine):
    """Put the recorder in front of the scripted model, everywhere it is held."""
    client = TracingClient(engine.client)
    engine.client = client
    for owner in (
        engine.intent_router,
        engine.desktop_action_planner,
        engine.browser_action_planner,
        getattr(engine, "task_planner", None),
        getattr(engine, "task_intent_gate", None),
        getattr(engine, "brief_responses", None),
        getattr(engine, "consent_classifier", None),
    ):
        if owner is not None and hasattr(owner, "client"):
            owner.client = client
    return client


class WholeTurnTests(_RecordingModule):

    def setUp(self) -> None:
        super().setUp()
        from tests.turn_harness import build_engine
        self.build_engine = build_engine

    def test_a_turn_leaves_a_complete_record(self):
        engine = self.build_engine()
        _traced(engine)
        engine.client.reply = (
            "Paris is the capital of France. Anything else I can help with?"
        )
        reply = engine.chat("what is the capital of france?")
        trace = turn_trace.last()

        self.assertEqual(trace.user_input, "what is the capital of france?")
        self.assertTrue(trace.chain_ok, trace.steps)
        self.assertEqual(trace.display, reply)
        self.assertEqual(
            trace.draft["text"],
            "Paris is the capital of France. Anything else I can help with?",
        )
        self.assertEqual(trace.draft["source"], "model")
        # The generic closer is the speech filter's to remove, and the
        # record says so, with the text on both sides.
        filtered = next(s for s in trace.steps if s["name"] == "speech_filter")
        self.assertTrue(filtered["changed"])
        self.assertNotIn("Anything else", filtered["after"])
        self.assertIn("final_speech_filter", trace.runs)
        # The router, the answer and anything after them were all seen.
        callers = [call["caller"] for call in trace.model_calls]
        self.assertTrue(any(c.endswith(":collect_answer") for c in callers), callers)
        self.assertIn("route", trace.context)
        self.assertIn("act", trace.context)
        self.assertIn("total", trace.timings)

    def test_the_record_changes_nothing_the_turn_does(self):
        said = (
            "I like strawberries.",
            "what's 2+2",
            "what is the capital of France?",
            "can you explain what a hash table is?",
        )

        def run(enabled: bool):
            random.seed(7)
            engine = self.build_engine()
            # After construction: ChatEngine applies config.yaml's
            # debug.turn_trace, which would switch it back on.
            turn_trace.ENABLED = enabled
            _traced(engine)
            engine.client.reply = "Paris is the capital. Want to know more?"
            replies = [engine.chat(text) for text in said]
            return replies, list(engine.client.calls)

        with_record = run(True)
        without_record = run(False)
        self.assertEqual(with_record[0], without_record[0])
        self.assertEqual(with_record[1], without_record[1])


class SourceTests(unittest.TestCase):

    def test_every_rewrite_of_the_reply_is_recorded(self):
        """A ``reply = ...`` in _answer_turn without a step is a hole in
        every record from then on; the chain would catch it at run time,
        this catches it before."""
        path = PROJECT_ROOT / "brain" / "chat_engine.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        answer_turn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_answer_turn"
        )
        unrecorded = []
        recorded = 0
        for node in ast.walk(answer_turn):
            if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(t, ast.Name) and t.id == "reply" for t in targets):
                continue
            value = node.value
            if isinstance(value, ast.Constant) and value.value == "":
                continue
            is_step = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "step"
                and isinstance(value.func.value, ast.Name)
                and value.func.value.id == "turn_trace"
            )
            if is_step:
                recorded += 1
            else:
                unrecorded.append(node.lineno)
        self.assertEqual(unrecorded, [], "wrap these in turn_trace.step(...)")
        self.assertGreaterEqual(recorded, 50)


if __name__ == "__main__":
    unittest.main()
