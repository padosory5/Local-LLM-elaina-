# Elaina AI

Elaina is an open LLM model that runs as a personal AI assistant. The main focus is trying to run the model as local, avoiding cloud APIs. Although if the user prefers using APIs the configurations maybe made. The goal of this project is to create an AI agent capable of remembering conversations, finishing tasks for user, and synchronizing with a live2D avatar.

## Features

* Local LLM using Ollama (Qwen3)
* Persistent long-term memory with SQLite
* Semantic memory search using FAISS
* Automatic memory extraction
* Memory consolidation
* Conversation history management
* Context-aware responses
* Streaming text generation
* Modular architecture for future expansion

## Tech Stack

* Python
* Ollama
* Qwen3 8B
* SQLite
* FAISS
* Sentence Transformers

## Current Status

Current progress includes:

* Local chat engine
* Persistent memory system
* Semantic memory retrieval
* Context management
* Streaming responses

## Language

Elaina supports multiple response languages through `config/config.yaml`.

Open:

```text
config/config.yaml
```

Find:

```yaml
language:

  # Response language
  # en = English
  # ko = Korean

  response: "en"
```

### English

```yaml
language:
  response: "en"
```

### Korean

```yaml
language:
  response: "ko"
```

Changing this setting will:

- Load the corresponding personality file (`personality_en.txt` or `personality_ko.txt`)
- Change Elaina's response language
- Configure the speech recognition language (if enabled)
- Use the configured TTS provider for that language

After changing the language, restart Elaina for the changes to take effect.

## Live2D (Unity) Setup

### If the Live2D model is invisible

If your Live2D model imports successfully but does not appear in the Scene or Game view, follow these steps before importing the Cubism SDK.

### 1. Configure the Render Pipeline

Open:

```
Edit → Project Settings → Quality
```

Find **Render Pipeline Asset** and set it to:

```
UniversalRP
```

---

### 2. Change the Color Space

Open:

```
Edit → Project Settings → Player → Other Settings
```

Set:

```
Color Space → Gamma
```

---

### 3. Import the Cubism SDK

After changing the settings above, import the **Cubism SDK for Unity (URP)** into your project.

---

### 4. Configure the Universal Renderer

In the **Project** window:

1. Press **Ctrl + F**
2. Search for:

```
UniversalRP
```

3. Select the **UniversalRP** asset.

In the **Inspector**:

- Locate **Renderer List**
- Add:

```
CubismURPRenderer
```

- Set **CubismURPRenderer** as the **Default Renderer**.

---

### 5. Import your Live2D model

After completing the steps above, import your `.model3.json` file.

The model should now render correctly.

> **Note:** If the model is still invisible, double-check the camera position, model scale, and that all texture files were imported with the model.

## Live2D Desktop Application Setup

The live2d uses pixiv live2d display which only uses CubismSDK4. Therefore, when you export your live2d file make sure to set it up as Cubism4 and no other versions.

## Roadmap

### Version 0.2

* Voice conversation (Speech-to-Text)
* Local Text-to-Speech

### Version 0.3

* Emotion engine
* Personality engine
* Improved memory ranking

### Version 0.4

* Unity integration
* 3D avatar synchronization
* Lip sync
* Facial expressions

### Version 1.0

* Fully local AI companion with memory, voice, and avatar support.

## Installation

```bash
git clone <repository-url>
cd elainaAI

python -m venv .venv

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

Start Ollama and make sure the Qwen3 model is installed:

```bash
ollama pull qwen3:8b
```

Run the application:

```bash
python main.py
```

## Vision

The long-term goal of Elaina AI is to become a fully local AI companion capable of natural conversation, long-term memory, voice interaction, and expressive 3D avatar synchronization, providing a private and extensible personal assistant without requiring cloud services.
