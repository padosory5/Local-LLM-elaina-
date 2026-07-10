# Elaina AI

A local conversational AI assistant built with Python and Ollama.

## Overview

Elaina AI is a personal AI assistant that runs completely on a local machine without relying on cloud APIs. The goal of this project is to create an AI companion capable of remembering conversations, speaking naturally, and eventually synchronizing with a 3D avatar in real time.

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

## Project Structure

```text
elainaAI/
│
├── brain/
│   ├── chat_engine.py
│   ├── conversation_manager.py
│   ├── prompt_builder.py
│   ├── attention.py
│   └── memory_ranker.py
│
├── memory/
│   ├── memory_manager.py
│   ├── extractor.py
│   ├── consolidator.py
│   ├── context_builder.py
│   ├── router.py
│   ├── embedding.py
│   └── faiss_manager.py
│
├── database/
├── main.py
└── requirements.txt
```

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
