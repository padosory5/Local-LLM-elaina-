"""Every stage that can rewrite a reply, and what kind of thing it is.

``ChatEngine._answer_turn`` hands the model's draft through a long ordered
list of stages before anyone sees it. Each one is recorded under a name by
``core/turn_trace.py``. This module states, for each of those names, the
judgments no program can make on its own: what the stage is for, whether it
guards a hard invariant or shapes soft behaviour, why it had to exist, what
it must run after, and which conflicts with other stages are on record.

Everything that *can* be computed is not written here: where each stage is,
in what order it runs, whether it runs twice, under what condition, which
tests exercise it, and how often it actually changes a reply. Those come
from the source and from the turn records, in
``scripts/response_pipeline_report.py``, so they cannot go stale.
``tests/test_response_stages.py`` fails when a recorded stage has no entry
here, or an entry names a stage that no longer exists.

**Classes.** HARD is an invariant that belongs in deterministic code for as
long as she exists: permissions, execution truthfulness, grounded values,
arithmetic, units, the reply language, state integrity. SOFT is behaviour --
tone, length, offers, follow-ups, relevance -- that should improve through
instructions, state and evaluation and eventually stop needing a rule.
MIXED does both in one place, or sits on the line; each one says which part
is which.

**Why it exists.** ``invariant``: a hard rule that holds whatever the model
does. ``model``: compensates for something the model does wrong. ``pipeline``:
repairs damage another stage did -- these go away when the order is fixed,
not when the model improves. ``modality``: the reply is displayed and spoken
from one string, and this stage is making the screen text speakable.
``infrastructure``: a service misbehaved.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


HARD = "HARD"
SOFT = "SOFT"
MIXED = "MIXED"
CLASSES = (HARD, SOFT, MIXED)

INVARIANT = "invariant"
MODEL = "model"
PIPELINE = "pipeline"
MODALITY = "modality"
INFRASTRUCTURE = "infrastructure"
REASONS = (INVARIANT, MODEL, PIPELINE, MODALITY, INFRASTRUCTURE)

# What a stage does to the reply when it acts.
FORMATS = "formats"          # rewrites characters, not content
REMOVES = "removes"          # takes sentences or clauses out
REPLACES = "replaces"        # swaps in a fixed or computed line
REGENERATES = "regenerates"  # asks the model for new text
APPENDS = "appends"          # adds text to what is there
ACTIONS = (FORMATS, REMOVES, REPLACES, REGENERATES, APPENDS)


@dataclass(frozen=True)
class Stage:
    name: str
    kind: str
    purpose: str
    exists_because: str
    action: str
    # Dotted names of the code that does the work, for locating it and
    # finding the tests that exercise it directly.
    implemented_by: tuple[str, ...] = ()
    # Whether the stage can call the model. Declared here; the turn records
    # show whether it actually did.
    model_call: bool = False
    # For a HARD or MIXED stage: the rule it holds.
    protects: str = ""
    # For MIXED: which part is which.
    split: str = ""
    # Stages that must already have run, and why.
    runs_after: tuple[str, ...] = ()
    # Conflicts with other stages that are on record in the code, with
    # where the record is.
    conflicts: tuple[str, ...] = ()


STAGES: tuple[Stage, ...] = (
    # ------------------------------------------------------------ the draft
    Stage(
        "budget_cut", HARD,
        "Drop a sentence the token budget cut off mid-word.",
        PIPELINE, REMOVES,
        implemented_by=("brain.chat_engine._drop_unfinished_sentence",),
        protects="Never say half a sentence.",
    ),
    Stage(
        "vision_retry", HARD,
        "Ask the vision model again, unstreamed, when its stream came back empty.",
        INFRASTRUCTURE, REGENERATES, model_call=True,
        protects="Never answer a screen question with nothing.",
    ),
    Stage(
        "final_form", HARD,
        "Make the reply the text the screen shows: the invariants, then "
        "damage repair only.",
        INVARIANT, FORMATS,
        implemented_by=(
            "brain.realize.display",
            "brain.realize.invariants",
            "brain.conversation_style.repair_display",
        ),
        protects="No internal identifiers or foreign-script sentences reach the person.",
        split=(
            "Phase 3A. HARD: capability_contract.redact_internals, dropping "
            "kana and glued-Han sentences, internal snake_case identifiers "
            "shown as words. DISPLAY: emphasis marks, headings, code fences, "
            "leaked labels, unpaired quotes, punctuation runs, a lowercase "
            "first word unless it is notation. Notation, dashes, line breaks "
            "and list structure are kept. Speech is realized separately at "
            "the audio boundary (brain/realize.speech); the old speech_filter, "
            "natural_dashes and final_speech_filter no longer touch the "
            "display. The trailing 'Anything else?' it used to cut is "
            "closing_offer's."
        ),
    ),
    # ------------------------------------------------- is it an answer at all
    Stage(
        "completion_retry", HARD,
        "Ask again when a value question came back without the value.",
        INVARIANT, REGENERATES, model_call=True,
        implemented_by=("brain.response_policy.AnswerCompletionGuard.needs_retry",),
        protects="A question that asks for a number gets the number.",
    ),
    Stage(
        "computed_result", HARD,
        "Compute a plain arithmetic answer after two drafts gave none.",
        INVARIANT, REPLACES,
        implemented_by=(
            "brain.chat_engine.ChatEngine._say_the_arithmetic",
            "tools.calculator.evaluate_expression",
        ),
        protects="Arithmetic is computed, never left unanswered.",
        runs_after=("completion_retry",),
    ),
    Stage(
        "repetition_retry", SOFT,
        "Ask again when the draft repeats an unrelated earlier answer.",
        MODEL, REGENERATES, model_call=True,
        implemented_by=(
            "brain.response_quality.ResponseQualityGuard.should_retry",
            "brain.response_quality.ResponseQualityGuard.without_stale_courtesy",
        ),
        conflicts=(
            "The retry reintroduced the same stale courtesy the guard caught, "
            "so the retry's output is trimmed as well (chat_engine, "
            "'I like strawberries' comment in the retry block).",
        ),
    ),
    # ------------------------------------------------------------- length
    Stage(
        "length_rewrite", SOFT,
        "Rewrite a draft that runs over the word or sentence limit, or that "
        "turned advice into a referral.",
        MODEL, REGENERATES, model_call=True,
        implemented_by=(
            "brain.response_policy.ResponseLimits.exceeds",
            "brain.response_policy.AdviceResponseGuard.needs_rewrite",
            "brain.chat_engine.ChatEngine._rewrite_is_usable",
        ),
        conflicts=(
            "Rewrote 'what's 2+2' answers into result-less sentences; "
            "result_restored exists to undo it (chat_engine, 'The number she "
            "worked out, still in the answer' comment).",
            "Fired on 19 of 48 dogfood turns and its first attempt was "
            "rejected in 13 of those (mb1 dogfood backend logs, 2026-09-23).",
        ),
    ),
    Stage(
        "advice_finalizer", SOFT,
        "A second rewrite for advice when the first one was unusable.",
        MODEL, REGENERATES, model_call=True,
        implemented_by=("brain.response_policy.AdviceResponseGuard.needs_rewrite",),
        runs_after=("length_rewrite",),
    ),
    Stage(
        "merge_extra_sentences", SOFT,
        "Join clauses of a recommendation to meet the sentence count.",
        MODEL, FORMATS,
        implemented_by=("brain.response_policy.ResponseLimits.merge_extra_sentences",),
    ),
    Stage(
        "condense", SOFT,
        "Shorten a long trusted tool result without changing any value.",
        MODEL, REGENERATES, model_call=True,
        implemented_by=("brain.answer_condenser.AnswerCondenser.condense",),
    ),
    # ------------------------------------------------------------- her voice
    Stage(
        "her_voice", MIXED,
        "Repair structural damage, enforce the Korean register, and re-say a "
        "reply that sounds like an interface.",
        MODEL, REGENERATES, model_call=True,
        implemented_by=(
            "brain.chat_engine.ChatEngine._say_it_in_her_voice",
            "brain.conversation_style.review",
            "brain.korean_register.to_formal",
        ),
        protects="Korean replies are in 습니다체; consent and clarification "
                 "questions are never reworded.",
        split=(
            "HARD: 습니다체 conversion (product policy), locked acts left "
            "verbatim, leaked markers removed. SOFT: fourteen style failure "
            "classes, cut to the act's length, remove disallowed offers, "
            "re-say in her voice."
        ),
        conflicts=(
            "Its fact-keeping check protected the wrong answer's number when "
            "the draft repeated a previous answer (chat_engine, 'London time "
            "from two turns earlier' comment).",
            "A re-said sentence can leak kana, so foreign_script runs again "
            "after it.",
        ),
    ),
    # -------------------------------------------------- did it understand
    Stage(
        "acknowledgement", SOFT,
        "Replace the reply with a line showing she understood, when they told "
        "her something about their life and the reply did not.",
        MODEL, REGENERATES, model_call=True,
        implemented_by=("brain.chat_engine.ChatEngine._acknowledgement_if_missed",),
        conflicts=(
            "A later stage removed the acknowledgement as 'a restatement of the "
            "current message', so it is judged twice -- the second time after "
            "the last hard-guard pass (chat_engine, '시애틀로 돌아가시는군요' "
            "comment).",
        ),
    ),
    Stage(
        "corrected_question", MIXED,
        "Answer the corrected question on its own when a correction was "
        "answered with her previous answer.",
        MODEL, REGENERATES, model_call=True,
        implemented_by=(
            "brain.chat_engine.ChatEngine._answer_the_corrected_question",
            "brain.told_not_asked.repeats",
        ),
        protects="A correction changes what is being answered.",
        split="HARD: the corrected subject wins. SOFT: detecting it by word overlap.",
    ),
    Stage(
        "not_her_last_answer", MIXED,
        "Answer a value question again, without history, when the reply "
        "gives the previous question's answer.",
        MODEL, REGENERATES, model_call=True,
        implemented_by=("brain.chat_engine.ChatEngine._not_her_last_answer",),
        protects="A new question is not answered with the last one's value.",
        split="HARD: the value belongs to this question. SOFT: the retry.",
    ),
    Stage(
        "encyclopedia", HARD,
        "Check a number she gave from her own knowledge against Wikipedia.",
        INVARIANT, REPLACES,
        implemented_by=(
            "brain.chat_engine.ChatEngine._checked_against_the_encyclopedia",
            "brain.world_facts",
        ),
        protects="A stated standing fact matches the encyclopedia.",
    ),
    Stage(
        "told_not_asked", SOFT,
        "Remove a question that only repeats back what they just told her.",
        MODEL, REMOVES,
        implemented_by=("brain.told_not_asked.without_asking_back",),
    ),
    # --------------------------------------------------------- the language
    Stage(
        "foreign_script", HARD,
        "Drop sentences carrying Japanese kana or Chinese written into Korean.",
        INVARIANT, REMOVES,
        implemented_by=("brain.text_filter.TextFilter.without_foreign_script",),
        protects="Every sentence is in a script her voice can say.",
        runs_after=("her_voice",),
    ),
    Stage(
        "turn_language", HARD,
        "Say the reply again in the language the person spoke, when it drifted.",
        INVARIANT, REGENERATES, model_call=True,
        implemented_by=(
            "brain.chat_engine.ChatEngine._answered_in_the_turns_language",
            "brain.turn_language",
        ),
        protects="She answers in the person's language.",
    ),
    # ------------------------------------------------------------ the truth
    Stage(
        "action_commitment", HARD,
        "Make 'let me check that' mean something, or take it back.",
        INVARIANT, REPLACES,
        implemented_by=(
            "brain.chat_engine.ChatEngine._enforce_action_commitment",
            "brain.action_commitment",
        ),
        protects="Execution truthfulness: no promise of an action that is not happening.",
    ),
    Stage(
        "grounded_values", HARD,
        "Remove a price, rating or number nothing this session actually saw.",
        INVARIANT, REMOVES,
        implemented_by=(
            "brain.chat_engine.ChatEngine._enforce_grounded_values",
            "brain.grounded_values",
            "brain.attribute_values",
        ),
        protects="Grounded values: no unsourced value is stated.",
    ),
    Stage(
        "existence_claims", HARD,
        "Remove a denial that something exists when nothing was checked.",
        INVARIANT, REMOVES,
        implemented_by=(
            "brain.chat_engine.ChatEngine._enforce_existence_claims",
            "brain.existence_claims",
        ),
        protects="Grounding: 'it does not exist' needs evidence too.",
    ),
    Stage(
        "named_recommendation", HARD,
        "A search that found nothing may not still name the answer.",
        INVARIANT, REPLACES,
        implemented_by=("brain.chat_engine.ChatEngine._enforce_named_recommendation",),
        protects="Grounding: a recommendation names something that was found.",
    ),
    Stage(
        "named_candidates", HARD,
        "What she names as the answer has to be one of the results.",
        INVARIANT, REPLACES,
        implemented_by=(
            "brain.chat_engine.ChatEngine._enforce_named_candidates",
            "brain.candidate_fit",
        ),
        protects="Grounding: the pick is one of the held candidates.",
        runs_after=("named_recommendation",),
    ),
    Stage(
        "found_claim", HARD,
        "Never say she found options she cannot name.",
        INVARIANT, REPLACES,
        implemented_by=("brain.chat_engine.ChatEngine._enforce_found_claim",),
        protects="Execution truthfulness about what a search returned.",
    ),
    Stage(
        "grounded_entities", HARD,
        "Remove a shop, venue or product name nothing actually found.",
        INVARIANT, REMOVES,
        implemented_by=("brain.chat_engine.ChatEngine._enforce_grounded_entities",),
        protects="Grounding: named entities come from evidence.",
    ),
    Stage(
        "invented_capability", HARD,
        "Never say she used an ability by a name she does not have.",
        INVARIANT, REPLACES,
        implemented_by=(
            "brain.chat_engine.ChatEngine._refuse_invented_capability",
            "brain.capabilities",
        ),
        protects="Capability truthfulness.",
    ),
    Stage(
        "unearned_success", HARD,
        "Never say a machine action happened on a turn where none did.",
        INVARIANT, REPLACES,
        implemented_by=("brain.chat_engine.ChatEngine._refuse_unearned_success",),
        protects="Execution truthfulness.",
    ),
    Stage(
        "unobserved_app_activity", HARD,
        "Opening an app is evidence it is open, and nothing more.",
        INVARIANT, REPLACES,
        implemented_by=("brain.chat_engine.ChatEngine._refuse_unobserved_app_activity",),
        protects="Execution truthfulness about what an app is doing.",
    ),
    # ------------------------------------------------------------- offers
    Stage(
        "ground_offer_language", MIXED,
        "Make an offer she wrote real by parking it, instead of deleting it.",
        MODEL, REPLACES,
        implemented_by=("brain.chat_engine.ChatEngine._ground_offer_language",),
        protects="State integrity: an offer in the reply has a parked action behind it.",
        split="HARD: the ledger matches the words. SOFT: how the offer is phrased.",
    ),
    Stage(
        "redundant_permission", MIXED,
        "Remove a question asking to do what was already asked for or done.",
        MODEL, REMOVES,
        implemented_by=("brain.chat_engine.ChatEngine._refuse_redundant_permission",),
        protects="State integrity: she does not ask for consent she already has.",
        split="HARD: agreement with the action record. SOFT: detecting it in wording.",
    ),
    Stage(
        "closing_offer", SOFT,
        "Cut a canned 'anything else?' or unparked offer off the end.",
        MODEL, REMOVES,
        implemented_by=("brain.response_policy.ClosingOfferGuard.strip",),
        runs_after=("ground_offer_language",),
        conflicts=(
            "Running before ground_offer_language removed offers that were "
            "about to be parked (chat_engine, 'Before the strip, "
            "deliberately' comment).",
        ),
    ),
    Stage(
        "one_offer", SOFT,
        "Keep one offer where the reply made two.",
        MODEL, REMOVES,
        implemented_by=("brain.chat_engine.ChatEngine._one_offer_per_reply",),
        runs_after=("closing_offer",),
    ),
    Stage(
        "report_found", MIXED,
        "Name what a search found when the reply said nothing about it.",
        MODEL, REPLACES,
        implemented_by=("brain.chat_engine.ChatEngine._report_what_was_found",),
        protects="A search that found things is not answered with nothing.",
        split="HARD: completeness against held results. SOFT: the sentence it says.",
    ),
    Stage(
        "append_recommendation", SOFT,
        "Add one offer of a capability that would help, when policy allows.",
        MODEL, APPENDS,
        implemented_by=(
            "brain.chat_engine.ChatEngine._append_recommendation",
            "brain.recommendation",
        ),
        runs_after=("closing_offer", "one_offer"),
        conflicts=(
            "Appended after the speech filter, so 'product_recommendation' "
            "reached a person with the underscore; the filter runs again at "
            "the end (chat_engine, 'The true end of the line' comment).",
        ),
    ),
    # ----------------------------------------------------------- last word
    Stage(
        "final_check", SOFT,
        "Strip an echo of their words; ask again if the reply repeats the "
        "previous answer.",
        MODEL, REGENERATES, model_call=True,
        implemented_by=(
            "brain.chat_engine.ChatEngine._final_response_check",
            "brain.response_quality.ResponseQualityGuard.strip_current_turn_echo",
        ),
        conflicts=(
            "Its regeneration can undo every guard before it, so seven guards "
            "run a second time whenever it changes the text.",
            "Read two 'yeah's as repetition and answered 'Sorry, I answered "
            "the wrong thing'; receipts are exempt (docstring).",
            "Damaged curated greetings ('Hey! What's up?' -> 'What's up?'); "
            "locked replies are exempt (docstring).",
        ),
    ),
    Stage(
        "premise_correction", MIXED,
        "Replace the reply with a correction when they took something false "
        "for granted and the reply let it stand.",
        MODEL, REGENERATES, model_call=True,
        implemented_by=(
            "brain.chat_engine.ChatEngine._premise_corrected",
            "brain.premise_check",
        ),
        protects="A false premise is not left standing.",
        split=(
            "HARD: correcting a false premise. SOFT: the model judges it. "
            "Runs after the last hard-guard pass; only grounded_entities is "
            "applied to its text."
        ),
    ),
    Stage(
        "as_theirs", SOFT,
        "Their 'my sister' said back as 'my sister', put right.",
        MODEL, FORMATS,
        implemented_by=("brain.told_not_asked.as_theirs",),
    ),
    Stage(
        "their_name", SOFT,
        "Her own name used as the person's, put right.",
        MODEL, REPLACES,
        implemented_by=("brain.chat_engine.ChatEngine._their_name_not_hers",),
    ),
    Stage(
        "result_restored", HARD,
        "Put the worked-out value back when a later stage dropped it.",
        PIPELINE, REPLACES,
        implemented_by=(
            "brain.response_policy.AnswerCompletionGuard.dropped_the_result",
            "brain.response_policy.AnswerCompletionGuard.the_result_sentence",
        ),
        protects="A value that was worked out reaches the person.",
        conflicts=(
            "Undid the Celsius conversion by restoring the Fahrenheit draft, "
            "so celsius now runs after it (chat_engine, '몇 도' comment).",
        ),
    ),
    Stage(
        "celsius", HARD,
        "Say Fahrenheit as Celsius in a Korean answer.",
        INVARIANT, REPLACES,
        implemented_by=("brain.units.in_celsius",),
        protects="Unit correctness for the language of the question.",
        runs_after=("result_restored",),
    ),
    Stage(
        "near_miss_prefix", MIXED,
        "Say what she took a misheard name to be, before the answer.",
        INVARIANT, APPENDS,
        implemented_by=("brain.near_miss", "brain.known_names"),
        protects="An assumption about what was heard is stated, so it can be corrected.",
        split="HARD: the assumption is disclosed. SOFT: its wording.",
        runs_after=("final_form",),
    ),
    Stage(
        "empty_fallback", HARD,
        "Say something rather than nothing when every stage left the reply empty.",
        INFRASTRUCTURE, REPLACES,
        implemented_by=("brain.guard_lines.say",),
        protects="Never return to listening in silence after a request.",
    ),
)


BY_NAME: dict[str, Stage] = {stage.name: stage for stage in STAGES}


def classify(name: str) -> str:
    """HARD, SOFT or MIXED for a recorded stage name; '' when unknown."""
    stage = BY_NAME.get(name)
    return stage.kind if stage else ""


# ------------------------------------------------------------- the switch
#
# ELAINA_SOFT_STAGES=off runs the pipeline without its SOFT stages -- the
# "soft guards off" arm of the model experiment, and later the way a guard
# is retired behind a flag before it is deleted. It skips a stage rather
# than discarding its output, so a skipped stage costs no model call.
# HARD stages always run, and so do MIXED ones: each holds a hard rule
# (see its 'split'). The one MIXED stage with a separable soft half,
# her_voice, drops that half through soft_stages_on().

_OFF = frozenset({"0", "off", "false", "no"})


def soft_stages_on() -> bool:
    """Whether SOFT stages run. Read on every call, so a test can switch it."""
    return os.environ.get("ELAINA_SOFT_STAGES", "").strip().lower() not in _OFF


def active(name: str) -> bool:
    """Whether the named stage runs on this turn."""
    return soft_stages_on() or classify(name) != SOFT
