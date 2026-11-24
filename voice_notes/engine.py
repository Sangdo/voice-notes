"""Recognition backends. Each backend yields (start, end, text) tuples for a 16 kHz mono WAV.

To add a backend (e.g. mlx-whisper), write a generator with the same signature and
register it in BACKENDS; then point a ModelSpec at it in models.py.
"""
import logging
import os
import platform

log = logging.getLogger('voice_notes.engine')


def apple_gpu():
    return platform.system() == 'Darwin' and platform.machine() == 'arm64'


def candidates(device):
    """Devices to try in order. NVIDIA needs the optional CUDA download on Windows (see gpu.py)."""
    if device != 'auto':
        return ['cpu']
    if apple_gpu():
        return ['mps', 'cpu']
    from . import gpu
    if gpu.supported() and not gpu.enable():
        log.info('NVIDIA GPU found but GPU support is not downloaded: using CPU')
    elif gpu.supported():
        import ctranslate2
        count = ctranslate2.get_cuda_device_count()
        log.info('NVIDIA GPU support ready · %d CUDA device(s) · ctranslate2 %s', count, ctranslate2.__version__)
        if count > 0:
            return ['cuda', 'cpu']
    return ['cpu']


DEVICE_NAMES = {'cpu': 'CPU', 'cuda': 'NVIDIA GPU', 'mps': 'Mac GPU'}
COMPUTE = {'cpu': 'int8', 'cuda': 'float16', 'mps': 'default'}


def faster_whisper(spec, model_dir, wav, *, device='auto', hint='', on_device=None):
    from faster_whisper import WhisperModel
    threads = max(1, min(8, (os.cpu_count() or 4) - 2))
    options = dict(language='en', beam_size=5, vad_filter=True,
                   # Long recordings: do not let one bad window poison the next one.
                   condition_on_previous_text=False)
    if hint:
        options['hotwords'] = hint
    options.update(spec.options)
    devices = candidates(device)
    for i, dev in enumerate(devices):
        last = i == len(devices) - 1
        produced = False
        try:
            model = WhisperModel(str(model_dir), device=dev, compute_type=COMPUTE[dev], cpu_threads=threads)
            segments, info = model.transcribe(str(wav), **options)
            if on_device:
                on_device(dev)
            for seg in segments:
                text = seg.text.strip()
                if text:
                    produced = True
                    yield seg.start, seg.end, text
            return
        except Exception:
            # A GPU that fails to start (old driver, too little memory) falls back to the next device. Once lines
            # have been written, stop instead: the app resumes from the saved notes.
            if last or produced:
                raise
            log.warning('%s failed, trying %s', DEVICE_NAMES.get(dev, dev), devices[i + 1], exc_info=True)


BACKENDS = {'faster-whisper': faster_whisper}


def transcribe(spec, model_dir, wav, **kwargs):
    if spec.backend not in BACKENDS:
        raise ValueError(f'Unsupported backend: {spec.backend}')
    return BACKENDS[spec.backend](spec, model_dir, wav, **kwargs)
