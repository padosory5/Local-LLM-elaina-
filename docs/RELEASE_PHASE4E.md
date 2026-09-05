# Phase 4E — release record

**Status:** PASS with known limitations. Phase 4E is complete.

| | |
|---|---|
| **Verified commit** | `cea23299` — every gate below ran here |
| **Release tag** | `phase4e-release`, on the commit adding this record (documentation only) |
| **Branch** | `phase4e-stabilization` |
| **Last behaviour commit** | `cb1c9632`; everything after it is documentation only |
| **`main`** | `6f427aff` — **untouched**, 64 commits behind |
| **Verified** | 2026-09-06 |
| **Working tree** | clean of tracked changes at freeze |

## Verification results

| Gate | Result |
|---|---|
| Full test suite | **2487 passed**, 1 expected failure, 0 unexpected (147 modules) |
| Python compilation | PASS |
| JavaScript syntax | PASS |
| Live routing | **41/41** |
| Consent walls | **5/5** |
| Browser stress | **7/7** sites |
| Screen browser | **11/11** |
| Startup | **`[Lifecycle] READY`**, `driver=screen`, `input_watch=on` |
| `runtime/data/` | untouched by the run |

**Verification failures: none.**

The single expected failure is recorded, not incidental:
`test_turn_behaviour.py::test_opening_a_folder_does_not_create_one` — the
router has no `open_folder` operation in its vocabulary, so "open my
documents folder" resolves to the nearest folder-shaped thing. It flips to
a pass when opening a folder becomes a real capability.

Clean shutdown was observed in the session-14 dogfood run
(`[Lifecycle] Shutting down: the desktop window closed`,
`[Desktop] Electron exited with code 0`). The verification startup above
was ended by its own timeout, so it is evidence of a clean start, not of
shutdown.

## What is release-ready

- Direct URL navigation is stable.
- URL recovery stays inside navigation and does not leak into other layers.
- Valid destinations can be verified.
- Invalid or unverified destinations are reported honestly, never as success.
- Browser and desktop-app routing are separated; a hostname never reaches
  the application planner.
- Ambiguity clarification preserves the original page action.
- Retries preserve the structured action rather than the last utterance.
- Machine failures are answered from the machine result and are no longer
  rewritten into unrelated search or recommendation language.
- User takeover is handled conservatively: real input stops a run
  immediately; pointer movement with no input event behind it does not.
- Shutdown is clean.
- No known P0/P1 remains in the core release path.

## Known limitations — post-release backlog

Carried forward deliberately. **Not** to be fixed in this cycle.

| # | Limitation |
|---|---|
| 1 | Page interaction is less reliable than direct URL navigation. |
| 2 | Relational element targeting ("X next to Y") is inconsistent. |
| 3 | Duplicate page elements may require clarification. |
| 4 | Pointer drift can still conservatively interrupt automation. |
| 5 | STT can still distort domains and names. |
| 6 | Some browser wording remains imperfect. |
| 7 | Piper TTS exit-code issue remains (S11R-11). |
| 8 | Router latency remains (S7-12, `route_model` 9–12s observed). |
| 9 | Previously deferred low-priority geography, persona and phrasing issues remain (A-03 invalid-host classification, A-08 wording, S7-10 geographic containment, S4-06 image phrasing, S6-10). |

## Release integration

`main` was deliberately left untouched throughout, per standing instruction.
Integrating `phase4e-stabilization` into `main` is a separate, explicit
decision and has **not** been performed.

## Commit chain

The stabilization arc, oldest first:

```
186719d0  session5: held state beat the current turn, in two more places
b210e1a9  session6: every layer was deciding for itself what the turn was about
4f719fa1  session7: dispatch is not arrival
38dc0547  session8: a page that rendered has a name of its own
e545435e  stabilization baseline
5cd47274  session9
2e40710e  standing orders
a1782602  looking vs judging
ca76ef0d  session11: stale title
dc6697a6  navigation: one owner for opening an address
a66909c7  acceptance: a pending offer is the last reading of a turn
4a8970e0  input watch: say only what the evidence supports
0e070289  page interaction: the request, kept whole
5a928b0d  "on this page" locates, it does not refer
a906653c  ambiguity: a choice between elements is not a new command
cb1c9632  page elements: the ones a person can see, named by what they sit beside
cea23299  docs  <- verified release candidate
(this record) <- tagged phase4e-release, documentation only
```
