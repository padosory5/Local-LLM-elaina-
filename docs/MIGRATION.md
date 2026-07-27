# Moving to the cleaned project

The cleaned archive is a complete project, not a patch. Install it beside the
current project first so old generated files do not remain mixed into the new
layout.

1. Close Elaina, Electron, and any terminal running the Python backend.
2. Rename the current project folder to `elainaAI_backup`.
3. Extract the cleaned archive as a new `elainaAI` folder.
4. Copy your private `.env` file from the backup into the new project.
5. Confirm the Piper paths and other machine-specific settings in
   `config/config.yaml`.
6. Recreate Python dependencies:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

7. Recreate Electron dependencies:

   ```powershell
   cd desktop
   npm install
   cd ..
   ```

8. Run the regression suite:

   ```powershell
   python -m unittest discover -s tests -v
   ```

9. Start Elaina and test ordinary conversation before approving a project
   change.

The original memory database and FAISS index from the supplied project are
already stored in `runtime/database`. Keep the backup until you have confirmed
that old conversations, TTS, screen selection, project approval, and Git
approval all behave correctly.
