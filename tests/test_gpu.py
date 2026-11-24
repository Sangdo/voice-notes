"""NVIDIA GPU support: device order, CPU fallback, DLL version parsing."""
import sys
from types import SimpleNamespace

import pytest

from voice_notes import engine, gpu


class FakeModel:
    """Stands in for faster_whisper.WhisperModel; `fail` maps device -> 'load' or 'run'."""
    fail = {}
    used = []

    def __init__(self, path, device, compute_type, cpu_threads):
        FakeModel.used.append((device, compute_type))
        self.device = device
        if FakeModel.fail.get(device) == 'load':
            raise RuntimeError('CUDA driver version is insufficient')

    def transcribe(self, wav, **options):
        def segments():
            if FakeModel.fail.get(self.device) == 'run':
                raise RuntimeError('cuDNN failed')
            yield SimpleNamespace(start=0.0, end=1.0, text=' Hello. ')
            if FakeModel.fail.get(self.device) == 'late':
                raise RuntimeError('out of memory')
            yield SimpleNamespace(start=1.0, end=2.0, text='World.')
        return segments(), None


@pytest.fixture
def fake(monkeypatch):
    import faster_whisper
    monkeypatch.setattr(faster_whisper, 'WhisperModel', FakeModel)
    monkeypatch.setattr(engine, 'candidates', lambda device: ['cuda', 'cpu'])
    FakeModel.fail, FakeModel.used = {}, []
    return FakeModel


def run(devices_seen):
    spec = SimpleNamespace(options={})
    return list(engine.faster_whisper(spec, 'model', 'a.wav', on_device=devices_seen.append))


def test_gpu_is_used_with_float16(fake):
    seen = []
    assert run(seen) == [(0.0, 1.0, 'Hello.'), (1.0, 2.0, 'World.')]
    assert fake.used == [('cuda', 'float16')] and seen == ['cuda']


@pytest.mark.parametrize('stage', ['load', 'run'])
def test_gpu_failure_before_any_line_falls_back_to_cpu(fake, stage):
    fake.fail = {'cuda': stage}
    seen = []
    assert len(run(seen)) == 2
    assert [d for d, _ in fake.used] == ['cuda', 'cpu']
    assert seen[-1] == 'cpu'


def test_gpu_failure_after_lines_is_reported_not_repeated(fake):
    fake.fail = {'cuda': 'late'}
    with pytest.raises(RuntimeError):
        run([])
    assert [d for d, _ in fake.used] == ['cuda']


def test_cpu_only_when_device_is_cpu():
    assert engine.candidates('cpu') == ['cpu']


@pytest.mark.skipif(sys.platform == 'win32', reason='checks the non-Windows path')
def test_no_cuda_off_windows():
    assert not gpu.supported() and not gpu.enable()
    assert engine.candidates('auto') in (['mps', 'cpu'], ['cpu'])


def test_file_version_reads_version_resource(tmp_path):
    dll = tmp_path/'cudnn64_9.dll'
    dll.write_bytes(b'MZ\0\0' + 'FileVersion'.encode('utf-16le') + b'\0\0' + '9.10.2.21'.encode('utf-16le') + b'\0\0')
    assert gpu.file_version(dll) == '9.10.2.21'
    assert gpu.file_version(tmp_path/'missing.dll') is None


def test_installed_needs_marker_for_current_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu, 'FOLDER', tmp_path)
    for name in gpu.REQUIRED:
        (tmp_path/name).write_bytes(b'')
    assert not gpu.is_installed()
    (tmp_path/gpu.MARKER).write_text(repr(gpu.packages()))
    assert gpu.is_installed()
    (tmp_path/gpu.MARKER).write_text("[('nvidia-cudnn-cu12', '9.1.0')]")   # ctranslate2 updated: download again
    assert not gpu.is_installed()
