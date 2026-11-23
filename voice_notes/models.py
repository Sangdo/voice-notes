"""Speech-recognition model catalogue.

To add a model, append one ModelSpec to MODELS. Nothing else needs to change:
the settings dialog lists every entry, and the worker looks the spec up by id.

`backend` picks the code path in engine.py (currently only 'faster-whisper').
`options` are passed straight to that backend's transcribe call.
"""
from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass(frozen=True)
class ModelSpec:
    id: str                 # stable key stored in settings
    label: str              # shown in the settings list
    repo: str               # Hugging Face repo (downloaded once, then used offline)
    size: str               # approximate download size, shown to the user
    note: str               # one-line speed/accuracy guidance
    backend: str = 'faster-whisper'
    options: dict = field(default_factory=dict)
    weights: str = 'model.bin'   # file name (glob) that marks a complete download


MODELS = [
    ModelSpec('turbo', 'Whisper large-v3-turbo', 'mobiuslabsgmbh/faster-whisper-large-v3-turbo', '1.6 GB',
              'Recommended · 1 hr ≈ 7 min'),
    ModelSpec('medium.en', 'Whisper medium.en', 'Systran/faster-whisper-medium.en', '1.5 GB',
              'Good · slower'),
    ModelSpec('small.en', 'Whisper small.en', 'Systran/faster-whisper-small.en', '0.5 GB',
              'Fast · less accurate'),
    ModelSpec('base.en', 'Whisper base.en', 'Systran/faster-whisper-base.en', '0.15 GB',
              'Fastest · testing'),
    ModelSpec('large-v3', 'Whisper large-v3', 'Systran/faster-whisper-large-v3', '3.1 GB',
              'Most accurate · slow'),
]

DEFAULT_MODEL = 'turbo'

# Local language models for summaries (MLX, Apple Silicon). Same idea: add one ModelSpec to offer another.
SUMMARY_MODELS = [
    ModelSpec('qwen3-4b', 'Qwen3 4B', 'mlx-community/Qwen3-4B-Instruct-2507-4bit', '2.3 GB',
              'Recommended · fast', backend='mlx', weights='model*.safetensors'),
    ModelSpec('qwen3-8b', 'Qwen3 8B', 'mlx-community/Qwen3-8B-4bit', '4.6 GB',
              'Slower · more detail', backend='mlx', weights='model*.safetensors'),
]
DEFAULT_SUMMARY_MODEL = 'qwen3-4b'

# Where the model runs. 'auto' tries the Apple GPU (Metal) and falls back to the CPU.
DEVICES = [('auto', 'Auto (GPU)'), ('cpu', 'CPU')]
DEFAULT_DEVICE = 'auto'


def get(model_id):
    for spec in MODELS + SUMMARY_MODELS:
        if spec.id == model_id:
            return spec
    raise KeyError(f'Unknown model: {model_id}')


def hub_cache():
    if os.environ.get('HF_HUB_CACHE'):
        return Path(os.environ['HF_HUB_CACHE'])
    return Path(os.environ.get('HF_HOME', Path.home()/'.cache'/'huggingface'))/'hub'


def local_path(spec):
    """Folder of a complete downloaded copy, or None."""
    snapshots = hub_cache()/('models--'+spec.repo.replace('/', '--'))/'snapshots'
    for path in sorted(snapshots.glob('*'), key=lambda p: p.stat().st_mtime, reverse=True):
        if (path/'config.json').is_file() and (path/'tokenizer.json').is_file() and any(path.glob(spec.weights)):
            return path
    return None


def is_downloaded(spec):
    return local_path(spec) is not None
