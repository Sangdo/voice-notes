"""NVIDIA GPU acceleration on Windows.

ctranslate2's Windows build can run Whisper on CUDA, but needs NVIDIA's cuBLAS and cuDNN libraries (~1.3 GB).
They are not bundled with the app: PCs with an NVIDIA card download them once from NVIDIA's official Python
packages on PyPI (checked against PyPI's SHA-256) into FOLDER. The worker adds FOLDER to the DLL search path
before loading ctranslate2.
"""
import importlib.util
import os
from pathlib import Path
import re
import shutil
import sys

FOLDER = Path.home()/'.cache'/'voice-notes'/'cuda'
SIZE = '1.3 GB'
CUBLAS = '12.9.2.10'        # CUDA 12, as ctranslate2 4.x links cublas64_12.dll
NVRTC = '12.9.86'           # needed by cuDNN's runtime-compiled engines
CUDNN_FALLBACK = '9.10.2.21'
REQUIRED = ('cublas64_12.dll', 'cublasLt64_12.dll', 'cudnn_graph64_9.dll', 'cudnn_ops64_9.dll', 'cudnn_cnn64_9.dll')
MARKER = 'installed.txt'


def has_nvidia():
    """An NVIDIA driver is installed (it ships nvidia-smi)."""
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'nvidia-smi.exe')
    return bool(shutil.which('nvidia-smi')) or system.is_file()


def supported():
    return sys.platform == 'win32' and has_nvidia()


def file_version(path):
    """FileVersion from a Windows DLL's version resource, or None."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    i = data.find('FileVersion'.encode('utf-16le'))
    if i < 0:
        return None
    match = re.search(r'\d+\.\d+\.\d+(?:\.\d+)?', data[i:i + 120].decode('utf-16le', 'ignore'))
    return match.group(0) if match else None


def cudnn_version():
    """cuDNN parts must match the small cuDNN loader that ctranslate2 ships next to its DLL."""
    spec = importlib.util.find_spec('ctranslate2')
    for folder in (spec.submodule_search_locations or []) if spec else []:
        version = file_version(Path(folder)/'cudnn64_9.dll')
        if version:
            return version
    return CUDNN_FALLBACK


def packages():
    return [('nvidia-cublas-cu12', CUBLAS), ('nvidia-cudnn-cu12', cudnn_version()), ('nvidia-cuda-nvrtc-cu12', NVRTC)]


def is_installed():
    try:
        return ((FOLDER/MARKER).read_text() == repr(packages())
                and all((FOLDER/name).is_file() for name in REQUIRED))
    except OSError:
        return False


def enable():
    """Make the downloaded libraries loadable. Call before importing ctranslate2. True if they are there."""
    if sys.platform != 'win32' or not is_installed():
        return False
    os.add_dll_directory(str(FOLDER))
    os.environ['PATH'] = str(FOLDER) + os.pathsep + os.environ.get('PATH', '')
    return True


def download(on_progress=None):
    """Fetch NVIDIA's Windows wheels from PyPI, check them, and keep only their DLLs."""
    import hashlib
    import json
    import ssl
    import tempfile
    from urllib.request import Request, urlopen
    import zipfile
    import certifi
    context = ssl.create_default_context(cafile=certifi.where())

    def get(url, timeout=60):
        return urlopen(Request(url, headers={'User-Agent': 'VoiceNotes/1.0'}), timeout=timeout, context=context)

    wheels = []
    for name, version in packages():
        with get(f'https://pypi.org/pypi/{name}/{version}/json') as r:
            files = [f for f in json.load(r)['urls'] if f['filename'].endswith('win_amd64.whl')]
        if not files:
            raise RuntimeError(f'No Windows download for {name} {version}.')
        wheels.append(files[0])
    total = sum(f['size'] for f in wheels)
    FOLDER.mkdir(parents=True, exist_ok=True)
    (FOLDER/MARKER).unlink(missing_ok=True)
    for f in wheels:
        with tempfile.TemporaryDirectory(dir=FOLDER) as tmp:
            target = Path(tmp)/f['filename']
            digest = hashlib.sha256()
            with get(f['url'], timeout=120) as r, target.open('wb') as out:
                while chunk := r.read(1 << 20):
                    out.write(chunk)
                    digest.update(chunk)
                    if on_progress:
                        on_progress(len(chunk), total)
            if digest.hexdigest() != f['digests']['sha256']:
                raise RuntimeError(f"{f['filename']} did not download correctly. Try again.")
            with zipfile.ZipFile(target) as z:
                for member in z.infolist():
                    if member.filename.lower().endswith('.dll'):
                        with z.open(member) as src, (FOLDER/Path(member.filename).name).open('wb') as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
    (FOLDER/MARKER).write_text(repr(packages()))
