"""Behavioural evaluation: how she answers, measured, instead of guarded.

The unit suite proves what the code does. This package measures what she
*says* -- whether an explanation starts somewhere concrete, whether a time
answer stops at the time, whether notation survives on screen and reaches
the voice as words -- against a live model, the way the person meets it.

    evals/scenarios/*.json   the corpus: one file per suite
    evals/rubric.json        the judged properties, defined once
    evals/modality_pairs.json  display text and what the voice should say
    evals/run.py             drive every scenario through a fresh backend
    evals/judge.py           grade the replies with a separate model
    evals/report.py          aggregate one or more runs
    evals/calibration.py     compare the judge with a person's labels

Failures create data before they create code: a behaviour that went wrong
becomes a scenario here, and a stage that exists to hide it can be retired
once the scenario passes without it. See docs/EVALS.md.
"""
