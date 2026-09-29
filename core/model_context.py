"""Every call to a model asks for the same, sufficient context window.

Ollama loads a model with the context window the loading call asked for --
4,096 tokens when nothing asks -- and reloads it whenever a later call asks
for a different one. Nothing in Elaina asked, so every model ran at 4,096.

That is smaller than the router needs. Measured in the Phase 3 rerun
(docs/COMMUNICATION_FINDINGS.md §Phase 3): the router's prompt is 3,700 to
4,100 tokens and its answer 270 to 360. On the 27B the answer was cut off
mid-JSON on a third of routed turns, and each one was rebuilt by a repair
call that no longer had the conversation in front of it. On the 8B nothing
was reported at all: Ollama shifted the context and silently dropped the
start of the prompt partway through the answer.

So every client in the backend process sends one configured size
(``llm.ollama.num_ctx``, env ``ELAINA_NUM_CTX``) with every call. One size
for every caller is the point: two callers asking for different windows
would reload the model between them. Measured cost at 6,144: +130 MB for
the 27B, +390 MB for the 8B.
"""

from __future__ import annotations

import os

DEFAULT_NUM_CTX = 6144


def configured(config=None) -> int:
    """The context window to ask for; 0 leaves Ollama's own default."""
    raw = os.environ.get("ELAINA_NUM_CTX", "").strip()
    if not raw and config is not None:
        try:
            raw = str(config.get("llm", "ollama", "num_ctx",
                                 default=DEFAULT_NUM_CTX, required=False) or "")
        except Exception:
            raw = ""
    try:
        value = int(raw) if raw else DEFAULT_NUM_CTX
    except ValueError:
        value = DEFAULT_NUM_CTX
    return max(0, value)


class ContextSizedClient:
    """An Ollama client that adds the configured ``num_ctx`` to every call.

    A call that sets its own ``num_ctx`` keeps it. Everything else passes
    through untouched, including attributes set on the client.
    """

    def __init__(self, inner, num_ctx: int) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_num_ctx", int(num_ctx))

    @property
    def wrapped(self):
        return self._inner

    def _with_context(self, kwargs: dict) -> dict:
        if self._num_ctx > 0:
            options = dict(kwargs.get("options") or {})
            options.setdefault("num_ctx", self._num_ctx)
            kwargs["options"] = options
        return kwargs

    def chat(self, *args, **kwargs):
        return self._inner.chat(*args, **self._with_context(kwargs))

    def generate(self, *args, **kwargs):
        return self._inner.generate(*args, **self._with_context(kwargs))

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value) -> None:
        setattr(self._inner, name, value)
