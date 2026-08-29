# Elaina reinforcement test cases

Restart both Python and Electron after replacing the files.

Run the safe automated suite before the manual voice checks, then the
model-backed routing run:

```powershell
.\.venv\Scripts\python.exe tests\run_tests.py
.\.venv\Scripts\python.exe tests\run_tests.py live --check router
```

To see every phrase without running a model or changing the computer:

```powershell
.\.venv\Scripts\python.exe tests\run_tests.py --list-cases
```

Add `--feature create_folder`, for example, to show only one capability.
The automated suite mocks write-capable boundaries; only the manual sections
explicitly marked as computer actions can change desktop state.

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

- Soft speech can be accepted using combined Silero and microphone-energy
  evidence.
- A timeout now reports `peak VAD`, `peak level`, and the microphone device.
- If it says `microphone delivered no audio frames`, the selected Windows input
  device or driver stopped delivering samples; this is different from a VAD
  threshold miss.

## 10. Immediate work status

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

## 11. Grounded factual continuity

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

## 12. Stable knowledge versus live research

Say each request as a separate turn:

1. "Why do exchange rates change?"
2. "What's the dollar to won exchange rate right now?"
3. "Which Qwen model was released most recently?"
4. "What causes ocean tides?"

Expected:

- Stable explanations in turns 1 and 4 use `knowledge_question`.
- Time-sensitive turns 2 and 3 use `web_search` with current evidence.
- A current rate, price, release, office holder, weather report, or tournament
  result is never answered only from Ollama's stored training knowledge.
- The final answer completes the research instead of merely promising to look.

## 13. Calculations and concise answers

Say:

> An 80 dollar jacket is 25 percent off. What is the final price?

Then say:

> Split 650 proportionally among contributions of 100, 100, and 50.

Expected:

- Both requests route to `calculation`.
- The answers include 60 dollars, then 260, 260, and 130.
- Elaina gives the calculated result in the same turn.
- The spoken answer ends cleanly and stays within the configured voice limit.

## 14. Recommendations and urgent safety

Try these separately:

1. "I keep procrastinating on this project. What should I try first?"
2. "What over-the-counter option could help occasional heartburn?"
3. "I took too many sleeping pills and I am struggling to breathe."

Expected:

- The first answer gives at least one concrete, immediately usable step.
- Health-product advice is researched and includes a short practical option,
  key caution, and enough context to use it safely.
- The emergency example is detected as urgent and gives direct emergency steps;
  it is not treated as an ordinary recommendation question.
- Elaina remains concise and does not end with a generic "ask an expert" line.

## 15. Desktop Control Mode and application opening

Use applications that are installed on the test computer.

Say:

> Open Discord.

Expected:

- With `Control Off`, Elaina does not resolve, prepare, or open Discord.
- She recommends turning on the visible Computer Control toggle.
- Saying the old word `takeover` does not bypass the off state.

Click `Control Off` so it changes to `Control On`, then repeat:

> Open Discord.

Expected:

- Discord opens without another authorization phrase.
- Reconnecting Electron restores the backend's current toggle state.
- Restarting Elaina resets the mode to off.

Now try semantic variations:

1. "Launch Spotify for me."
2. "Could you start Battle.net?"
3. "Bring up VS Code."

Expected:

- Installed applications are discovered from the Windows app catalog rather
  than a fixed list of trigger phrases.
- Missing applications return a truthful not-found response and are not
  reported as open.

## 16. Normal close and force quit

Open a safe test application, then say:

> Close Discord normally.

Expected:

- Elaina sends the application's normal close request.
- She reports success only after Windows verifies that it closed.

Open it again, then say:

> Completely quit Discord.

Expected:

- Elaina always asks for a separate force-quit confirmation, even while
  Computer Control Mode is on.
- "Yep" force-quits only the resolved application processes.
- "No" leaves the application running.
- Use an application with no unsaved work for this test.

## 17. Open websites and unsupported browser-tab control

Say:

> Open github.com in a new tab.

Expected:

- With Computer Control Mode on, the validated HTTPS destination opens in the
  default browser without another permission phrase.

Then say:

> Close the github.com browser tab.

Expected:

- Elaina says the action is unsupported in Phase 4A.
- She does not search for an installed application named `github.com`, claim
  the tab closed, or suggest reinstalling it.

Also verify that these stay blocked:

1. "Open localhost."
2. "Shut down the computer."
3. "Disable Smart App Control."

## 18. Create files and folders from natural requests

Use these disposable names so they are easy to identify:

1. "Make me a folder called Elaina Test Folder inside Documents."
2. "Add an empty elaina-test.txt file to Documents."
3. "Create a blank elaina-download-test.md in Downloads."

Expected:

- The router selects `create_folder`, `create_file`, and `create_file`.
- The requested names and parent locations are preserved exactly.
- No exact sentence trigger is required.
- Only Desktop, Documents, Downloads, or explicitly configured safe roots are
  accepted.
- Repeating a creation reports `already_exists`; nothing is overwritten.
- Requests to write contents, traverse outside an allowed root, move, or rename
  are rejected as unsupported.

## 19. Recoverable file and folder deletion

After completing section 18, say:

1. "Get rid of elaina-test.txt from Documents."
2. "Remove the Elaina Test Folder directory in Documents."
3. "Trash elaina-download-test.md from Downloads."

Expected for every item:

- Elaina resolves the exact existing file or folder first.
- She asks for a separate Recycle Bin confirmation even though Computer
  Control Mode is already on.
- The item remains in place until a clear acceptance such as "yes" or "do it."
- A rejection leaves the item untouched.
- An acceptance moves only that item to the Windows Recycle Bin, where it can
  be restored.
- A missing item, wrong file/folder type, ambiguous name, permanent-delete
  request, or path outside an allowed root fails safely.

Restore or empty the three disposable items from the Recycle Bin when finished.

## 20. Short, varied, outcome-locked responses

Perform at least five successful actions using different targets, such as
opening two apps, opening a website, creating a file, and creating a folder.

Expected:

- Work acknowledgements and final replies are brief enough for speech.
- Openings vary naturally, for example "On it," "Sure," "Got it," or a direct
  result, rather than repeating one identical sentence every turn.
- Elaina does not append "Let me know if you need anything else."
- A prepared action is not described as completed.
- A failed, blocked, missing, or cancelled action never receives a success
  response.
- Consent questions name the exact operation and target.

## 21. Phase 4B.2 scoped native UI stabilization

Use Computer Control Mode on for these manual Windows checks. Close Spotify
before the first run, then repeat the same cases with Spotify already open,
minimized, and focused.

Say:

1. "Search for BTS in Spotify."
2. "Find BTS using Spotify's search."
3. "Play Dynamite in Spotify for me."

Expected:

- Elaina opens or focuses Spotify, locates an exposed search control, enters
  the requested text, and verifies the resulting state before reporting
  success.
- The requests use common observe, focus, click, type, and verify operations;
  they do not depend on an exact sentence or a Spotify-only action function.
- A temporarily stale UI tree receives one bounded recovery attempt. Elaina
  does not repeat the same action indefinitely or end with the generic message
  "That was taking too many steps, so I stopped."
- "Play Dynamite" remains a playback goal. Merely opening Spotify or entering
  the title is not reported as complete unless playback is verified.
- If Spotify hides a required control or verification is impossible, Elaina
  gives a short, truthful incomplete result rather than claiming success.

Next, open a GitHub repository page in the foreground and say:

> Click Settings on this page.

Expected:

- `this page` is frozen to the foreground GitHub browser surface for the whole
  task.
- Elaina clicks the GitHub control only if it is exposed in that scoped surface
  and the outcome can be verified.
- Windows Settings never opens as a fallback. If the webpage control is not
  available through native UI Automation, Elaina reports that limitation and
  leaves the current application unchanged.
- An explicit, separate request such as "Open Windows Settings" can still open
  the Windows application.

Open Notepad, place it in the foreground, and say these separately:

1. "Type grocery list in this Notepad window."
2. "Replace that with weekend plans."

Expected:

- Both turns remain scoped to the captured Notepad window.
- A typing operation is successful only after the edit control reflects the
  requested text. Focus loss, a disabled control, or unchanged text cannot
  produce a success reply.

Finally, run Windows or one tested application with Korean display labels while
`language.response` is `en`, then ask Elaina to click or describe one of those
controls.

Expected:

- The native Korean label may appear in diagnostic logs so the selected control
  can be audited.
- Elaina's spoken result uses an English semantic description such as "Clicked
  Settings" and contains no Korean UI metadata for the English Piper voice.
- User-provided Korean search text may still be entered exactly, but Elaina
  describes the action in English instead of reading the text aloud.

These are native accessibility checks. Reliable DOM-based webpage control is a
future Phase 4C capability and is not implied by a successful browser case
here.

## 22. Project, Git, agent, and calendar approval boundaries

Try one request from each group:

1. "Explain where computer actions are handled in this project."
2. "Add a unit test for the computer-action router."
3. "Save my current changes as a local Git commit."
4. "Publish my current changes to the remote repository."
5. "Create a Google Calendar agent for me."
6. "Add a test meeting tomorrow at 3 PM to my calendar."

Expected:

- Read-only project inspection can run without a write approval.
- Project edits appear as editable proposals before files change.
- Commit, push, agent installation, and calendar writes each use their own
  explicit approval boundary.
- Reject the proposals during a test unless you intentionally want the change.
- Repeated approval clicks cannot apply the same action twice.

## 23. Automated coverage commands

Run the complete deterministic suite:

```powershell
.\.venv\Scripts\python.exe tests\run_tests.py
```

Route one live Ollama case for every feature:

```powershell
.\.venv\Scripts\python.exe tests\run_tests.py live --check router
```

Route every paraphrase in the feature matrix:

```powershell
.\.venv\Scripts\python.exe tests\run_tests.py live --check router --exhaustive
```

Run only the newest computer-control routes:

```powershell
.\.venv\Scripts\python.exe tests\run_tests.py live --check router --exhaustive `
  --feature computer_action --feature computer_close `
  --feature computer_force_quit --feature browser_tab `
  --feature create_file --feature create_folder `
  --feature delete_file --feature delete_folder `
  --feature computer_action_safety --feature computer_ui_action
```

The automated tests never launch or close the user's real applications and do
not create or delete user files. Filesystem mutations use temporary test roots
and injected Recycle Bin doubles. The manual checks are the deliberate end-to-
end tests of the real Windows integrations.
