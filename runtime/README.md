# Runtime data

This directory contains generated or personal state rather than source code.

- `database/` stores Elaina's SQLite memories and FAISS index.
- `data/` stores API usage counters.
- `debug/screen_captures/` stores selected-screen diagnostics.
- `agents/` stores user-approved agent definitions.
- `audit/` records agent task and approval state changes.
- `secrets/` stores local OAuth tokens and must never be committed.

Back up `runtime/database/` before replacing or resetting the project.
