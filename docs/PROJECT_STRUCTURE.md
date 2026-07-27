# Refactor notes

The second architecture layer adds `agents/` and `security/`. Existing feature
implementations remain behind agent adapters so the migration does not rewrite
working screen, search, project, Git, voice, or memory behavior all at once.

## Preserved behavior

- Electron still owns and shuts down the Python backend.
- WebSocket event names and renderer behavior are unchanged.
- Existing SQLite memories and the FAISS index are preserved.
- Screen, web, visual identification, MCP edits, Git approval, TTS
  interruption, and grounded factual context remain available.

## Cleanup performed

- Removed checked-in `node_modules`, `__pycache__`, and bytecode.
- Removed the zero-byte `desktop/package.js`.
- Removed unused `brain/personality.py`.
- Removed the malformed machine-specific dependency freeze.
- Replaced requirements with direct runtime dependencies.
- Moved generated state under `runtime/`.
- Added package boundaries, tests, documentation, `.gitignore`, and
  `.env.example`.
- Centralized filesystem paths in `core/paths.py`.
- Made STT/VAD read `config.yaml` instead of using hidden hard-coded values.
- Removed redundant Faster-Whisper VAD filtering.
- Added cancellable project research during interruption and shutdown.

## Router safety

The semantic model still determines meaning, but it no longer owns the project
write boundary. A local safety policy requires direct delegation of a concrete
change before `project_edit` is allowed. Statements about the user's plans and
requests for suggestions are downgraded safely.
