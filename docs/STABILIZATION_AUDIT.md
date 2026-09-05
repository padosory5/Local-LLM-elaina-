# Stabilization audit for the Session 9 gate

Starting revision: `38dc0547`. Scope: correctness of the existing release candidate.

## Root causes and ownership

| Root cause | Owning boundary | Failures explained | Minimal change |
| --- | --- | --- | --- |
| Interpretation is mutable and distributed | `ChatEngine._route_turn`, `TurnRouting`, focus, task sessions, consent | stale rentals, geography, offers, clarification and machine targets; corrected transcript disagreement | Resolve authority before consent; carry the resolved query/task snapshot through dispatch and answering; expire superseded action state |
| Dispatch drops page evidence and identity | `SafeBrowserControl.open`, computer result, browser services, navigation verifier | false arrivals, wrong tab, error page counted as success, recovery never reached | Preserve the dispatcher's exact page observation and classify it centrally; URL/title alone cannot verify |
| Recovery and candidate eligibility lose provenance/type | navigation history, spelling parser, recommendation problem, candidate fit | guessed unfused domains, lost URL corrections, products replaced by articles or keyword matches | Bound corrections to the active URL; require parser provenance for fusion; carry expected candidate type |
| A model pass follows the final guards | `_final_response_check` after commitment/grounding | regenerated repetition fixes can introduce unsupported promises or facts | Apply final invariants to the actual emitted text, after the last generation |

## What already works and should be preserved

The project already has `TurnRouting`, immutable `RecommendationProblem`, provenance-bearing
`Slot`, `Focus`, typed clarification and bounded consent gates. These are extended rather
than replaced with a second state architecture. Existing regressions cover cancellation,
phone numbers versus budgets, relational school anchors, location fallback, natural
clarification answers, factual challenges, institutional contact values and shutdown.

Production navigation does **not** currently call `webbrowser.open_new_tab` when browser
page control is enabled. `ChatEngine` supplies `browser_service.open_url` as the opener.
The default opener still exists for standalone `SafeBrowserControl` users. The screen
service navigates a selected HWND, but its result is discarded by `SafeBrowserControl.open`;
the engine subsequently asks a separate observer for matching tabs or `describe_page(None)`.
That is not a correlation guarantee. CDP's service also reduces its result to a boolean.

Duplicated interpretation remains in router paraphrases, `goal_intent.read`, focus updates,
recommendation constraint extraction, `_resolved_search_query`, discovery policy and
planner subgoals. Local extraction before the resolved boundary is necessary; reconstructing
the current search payload from mutable session state after that boundary is not.
Planner subgoals and live element grounding remain local to their existing executors.

## Validation

Baseline and final deterministic results, targeted boundary tests, routing, consent walls,
browser stress and latency evidence will be recorded here. Automated checks do not establish
release readiness. The human A–G Session 9 gate remains required after a green freeze.
