# Elaina reinforcement test cases

Restart both Python and Electron after replacing the files.

## 0. Semantic agent permission

Say these as separate turns:

1. "The buttons on this project look boring."
2. "Yeah, let's do that."

Expected:

- The first turn stays with Elaina. She may offer to use the Coding Agent, but
  no agent task starts and no project files are inspected.
- The second reply is interpreted against the pending offer and starts the
  Coding Agent.
- Variants such as "sure" and "let's go for it" can also accept because the
  router judges their conversational meaning rather than matching one phrase.
- "No, I was just saying" rejects and clears the offer.
- Changing the topic instead clears the offer without running anything.
- Any resulting file edit still requires the separate Electron approval.

A direct request such as "Can you search for when Elon Musk was born?" should
start Research Agent immediately because the request itself grants permission;
Elaina must not ask whether to search a second time.

To check the real local model rather than test doubles, run:

```text
python scripts/live_router_check.py
```

This performs five read-only routing checks against the configured Ollama model.

## 1. Contextual STT correction and Git approval

Say:

> Push my changes to get.

Expected:

- Router intent: `git_publish`
- Electron opens one Git approval window.
- Nothing is staged, committed, or pushed before approval.
- Repeating the request or saying "yeah" reports that the proposal is already
  waiting instead of creating a duplicate.

## 2. Corrected entity and search continuity

Say these as separate turns:

1. "Can you find recent Quen releases?"
2. "No, I said Gwynn. Q W E N."
3. "Search the latest releases about Quinn."

Expected:

- The spelling turn is authoritative and immediately repeats the search using
  `Qwen`; you do not need to ask Elaina to search again.
- Later STT variants such as `Quinn`, `Quen`, and `Gwynn` resolve to `Qwen`
  while the current topic is still Qwen.
- Elaina returns completed results and never says she will search later.
- Repeating the same query within five minutes uses the search cache.

## 3. General factual follow-up

Say:

1. "How does mental illness develop?"
2. "For example?"

Expected:

- The second answer gives a concrete example of how mental illness can develop,
  using the active topic rather than treating "For example" as a new topic.
- Elaina does not mention the time of day.

## 4. Project editing

Say:

> Add a test button next to the Screen button.

Expected:

- Router intent: `project_edit`
- Electron displays editable proposed code.
- No file changes before approval.
- Repeating the request does not create another proposal.

Reject the proposal after testing if you do not want the button.

## 5. Direct screen analysis

Select text or code with Screen, then say:

> Translate this text.

or:

> Explain this code.

Expected:

- Router intent: `screen_analysis`
- Vision router: `direct`
- After selecting the region, the console may show
  `[Vision] Preloading qwen3-vl:8b...`; this runs while you speak.
- Qwen3-VL handles the image locally.
- Google Web Detection is not called.
- The answer contains no jokes, hype, unrelated reactions, or follow-up offer.

## 6. Screen identification

Select a game, product, landmark, logo, or other identifiable image, then say:

> What exactly is this?

Expected:

- Vision router: `identify`
- Google Web Detection runs.
- Electron shows the strongest matched-page title and retrieval score.
- Qwen3 provides a concise answer with high, moderate, or low confidence.
- Failed retrieval produces uncertainty instead of a guess.

## 7. Performance timing

After each completed turn, check for:

```text
[Timing] route=...s memory_retrieval=...s web_search=...s
         visual_pipeline=...s generation=...s total=...s
```

Only stages used by that turn are printed. Project actions add
`project_tools`; background memory writes print their own
`background_memory` timing without delaying Elaina's reply.

Use these numbers to determine whether delay comes from routing, memory,
retrieval/tools, model generation, or the entire pipeline.

## 8. Voice interruption

Ask Elaina for an explanation long enough to speak for several seconds. While
she is speaking, say:

> Elaina, stop. Tell me the short version.

Expected:

- Her current voice stops as soon as speech is confirmed.
- The old streamed response is cancelled and is not resumed.
- The console prints `[ChatEngine] Response interrupted.`
- Your interruption is transcribed and receives a new response.
- If the microphone captures Elaina's speaker output, probable matching text
  is discarded with `[STT] Ignored probable speaker echo.`

Headphones provide the most reliable interruption because they prevent speaker
audio from physically entering the microphone.

## 9. Intermittent missed-speech diagnostics

Make a screen selection, wait for the vision preload message, and speak at a
normal volume during `Listening...`.

Expected:

- Startup prints `[Microphone] Persistent input stream is active.` once. It
  should not print once per listening cycle.
- The same stream stays open while Elaina is thinking and speaking, so the
  headset is not allowed to enter microphone-idle mode between turns.
- Soft speech can be accepted using combined Silero and microphone-energy
  evidence.
- A timeout now reports `peak VAD`, `peak level`, and the microphone device.
- If it says `microphone delivered no audio frames`, the selected Windows input
  device or driver stopped delivering samples; this is different from a VAD
  threshold miss.
- If the stream stops delivering frames for two seconds, Elaina automatically
  closes and reopens it.

## 10. Context and spoken-answer quality

Identify an image as Eros, then change topics and ask:

> Should I use Live2D or a 3D model for my local LLM avatar?

Expected:

- Elaina answers the Live2D-versus-3D question directly and does not mention
  Eros or the previous image.
- She does not suggest Agent Builder for creating an avatar.
- Visual answers do not describe URLs, matching pages, evidence lists, or
  confidence calculations.
- Answers end on a complete sentence and Markdown cleanup does not fuse words.

## 11. Immediate work status

Try one request from each group:

1. "Search for the latest Qwen release."
2. Select an image and say "Identify this."
3. "Explain where the Screen button is implemented in my project."
4. "Add a test button next to Screen."
5. "Push my changes to Git."

Expected:

- Electron immediately displays a short status message.
- Elaina speaks that status while the tool works.
- The later final answer or approval proposal is displayed separately.
- The status never claims that a search, edit, commit, or push succeeded before
  its actual result is known.

## 12. Grounded factual continuity

Select an image from Bungie's 2026 Marathon and say these as separate turns:

1. "What game is this?"
2. "When was it released?"
3. "I thought Marathon was an older game."
4. "Look, I was right about the new one."

Expected:

- The visual result becomes the grounded subject, including which version of
  Marathon was identified.
- "When was it released?" routes to `web_search`, never `time_question`.
- Elaina distinguishes the original 1994 game from Bungie's 2026 extraction
  shooter.
- A correction that still needs evidence routes to `fact_check` with web
  verification.
- After verification, "I was right" uses the grounded result without asking
  "right about what?" or performing an unnecessary second search.
