"""Two models, and whether this machine can actually hold both.

Routing, consent, planning and tool selection are read as JSON and acted
on; the reply is read aloud. Those are different jobs and, measured, they
want different models. Same session, same matrices, a 27B against
``qwen3:8b``:

    Korean clean turns      58%  ->  83%
    English clean turns     75%  ->  83%
    router accuracy    130/134  ->  129/134
    dangerous false positives 0  ->  2
    JSON repair retries       0  ->  13

Better words, worse judgement, and cleanly separable. So the split: the
decisions stay on the model that gets them right, and only the words move.

**The failure mode this module exists for.** Both models have to stay
resident, because a turn uses both. If they do not fit together, Ollama
evicts one and reloads it on the next call, and the split becomes far
worse than either model alone -- measured at roughly 24 seconds per turn
during A2, which is what made a 35B split impossible on this card.

Nothing here can prevent that; the sizes are what they are. What it can do
is *say so at startup*, loudly, instead of leaving someone to discover it
as mysterious slowness. A configuration that cannot work should announce
itself.
"""

from __future__ import annotations

import shutil
import subprocess

# Ollama needs headroom over the raw weights for the KV cache and context.
# Rough, and deliberately generous: the point is to catch "these two
# obviously do not fit", not to predict the last hundred megabytes.
_OVERHEAD = 1.25

_BYTES_PER_GB = 1024 ** 3


def model_sizes(client) -> dict[str, float]:
    """Every local model's size in GB, by name."""
    try:
        listing = client.list()
    except Exception:
        return {}
    models = (
        listing.get("models", []) if isinstance(listing, dict)
        else getattr(listing, "models", [])
    )
    sizes: dict[str, float] = {}
    for entry in models:
        name = (
            entry.get("model") if isinstance(entry, dict)
            else getattr(entry, "model", "")
        )
        size = (
            entry.get("size") if isinstance(entry, dict)
            else getattr(entry, "size", 0)
        )
        if name and size:
            sizes[str(name)] = float(size) / _BYTES_PER_GB
    return sizes


def total_vram_gb() -> float:
    """This card's memory in GB, or 0.0 when it cannot be read.

    Zero means "do not guess": a fit check that invents a number is worse
    than no fit check, because it would either cry wolf or give a machine
    a clean bill of health it never earned.
    """
    if not shutil.which("nvidia-smi"):
        return 0.0
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return 0.0
    for line in output.splitlines():
        line = line.strip()
        if line.isdigit():
            return float(line) / 1024
    return 0.0


def report(
    client,
    structure_model: str,
    conversation_model: str,
    *,
    vram_gb: float | None = None,
) -> str:
    """What to print at startup about the model configuration.

    Empty when one model does both jobs -- there is nothing to warn about
    and nothing worth a line of log.
    """
    structure = str(structure_model or "").strip()
    conversation = str(conversation_model or "").strip()
    if not conversation or conversation == structure:
        return ""

    sizes = model_sizes(client)
    structure_size = sizes.get(structure, 0.0)
    conversation_size = sizes.get(conversation, 0.0)
    vram = total_vram_gb() if vram_gb is None else float(vram_gb)

    lines = [
        "[Models] Two models, split by job:",
        f"  decisions  {structure}"
        + (f"  ({structure_size:.1f} GB)" if structure_size else ""),
        f"  speech     {conversation}"
        + (f"  ({conversation_size:.1f} GB)" if conversation_size else ""),
    ]

    if not (structure_size and conversation_size and vram):
        lines.append(
            "  Could not measure both sizes against this card's memory, so "
            "whether they fit together is unverified."
        )
        return "\n".join(lines)

    needed = (structure_size + conversation_size) * _OVERHEAD
    lines.append(
        f"  together   ~{needed:.1f} GB of {vram:.1f} GB"
    )
    if needed > vram:
        lines.append(
            "  WARNING: these two cannot both stay resident. Every turn "
            "uses both, so Ollama will evict and reload one each time -- "
            "measured at about 24s per turn when this last happened. "
            "Either pick a smaller speech model or set "
            "conversation_model to \"\" to run one model for both."
        )
    return "\n".join(lines)


def fits(
    structure_gb: float, conversation_gb: float, vram_gb: float,
) -> bool:
    """Whether two models can be held at once, with room to work."""
    if not (structure_gb and conversation_gb and vram_gb):
        return True
    return (structure_gb + conversation_gb) * _OVERHEAD <= vram_gb
