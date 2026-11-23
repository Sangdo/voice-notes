"""Recognition backends. Each backend yields (start, end, text) tuples for a 16 kHz mono WAV.

To add a backend (e.g. mlx-whisper), write a generator with the same signature and
register it in BACKENDS; then point a ModelSpec at it in models.py.
"""
import os
import platform


def apple_gpu():
    return platform.system() == 'Darwin' and platform.machine() == 'arm64'


def faster_whisper(spec, model_dir, wav, *, device='auto', hint='', on_device=None):
    from faster_whisper import WhisperModel
    threads = max(1, min(8, (os.cpu_count() or 4) - 2))
    candidates = ['mps', 'cpu'] if device == 'auto' and apple_gpu() else ['cpu']
    model = None
    for i, dev in enumerate(candidates):
        try:
            model = WhisperModel(str(model_dir), device=dev,
                                 compute_type='int8' if dev == 'cpu' else 'default', cpu_threads=threads)
            break
        except Exception:
            if i == len(candidates) - 1:
                raise
    if on_device:
        on_device(dev)
    options = dict(language='en', beam_size=5, vad_filter=True,
                   # Long recordings: do not let one bad window poison the next one.
                   condition_on_previous_text=False)
    if hint:
        options['hotwords'] = hint
    options.update(spec.options)
    segments, info = model.transcribe(str(wav), **options)
    for seg in segments:
        text = seg.text.strip()
        if text:
            yield seg.start, seg.end, text


BACKENDS = {'faster-whisper': faster_whisper}


def transcribe(spec, model_dir, wav, **kwargs):
    if spec.backend not in BACKENDS:
        raise ValueError(f'Unsupported backend: {spec.backend}')
    return BACKENDS[spec.backend](spec, model_dir, wav, **kwargs)
