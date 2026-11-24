"""Build the desktop app with PyInstaller: python packaging/build.py
Puts FFmpeg from packaging/ffmpeg/ (ffmpeg + ffprobe, full build with all codecs) inside the app.
Output: dist/Voice Notes/ (Windows) or dist/Voice Notes.app (macOS).
"""
import os
from pathlib import Path
import sys

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT/'packaging'/'ffmpeg'
exe = '.exe' if os.name == 'nt' else ''
tools = [FFMPEG/f'{name}{exe}' for name in ('ffmpeg', 'ffprobe')]
missing = [str(t) for t in tools if not t.is_file()]
if missing:
    sys.exit('FFmpeg not found: ' + ', '.join(missing))

# Build stamp shown at the top of each log session (voice_notes/log.py).
import datetime
import subprocess
commit = os.environ.get('GITHUB_SHA') or subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                                         capture_output=True, text=True).stdout.strip()
version = os.environ.get('APP_VERSION', '0.0.0')   # CI: 1.0.<run number>
(ROOT/'voice_notes'/'build.txt').write_text(f"{version} {commit[:7] or 'unknown'} {datetime.date.today()}",
                                            encoding='utf-8')

args = [str(ROOT/'packaging'/'entry.py'),
        '--name', 'Voice Notes', '--windowed', '--noconfirm', '--clean',
        '--distpath', str(ROOT/'dist'), '--workpath', str(ROOT/'build'), '--specpath', str(ROOT/'build'),
        '--paths', str(ROOT),
        '--add-data', f'{ROOT/"voice_notes"/"fonts"}{os.pathsep}voice_notes/fonts',
        '--add-data', f'{ROOT/"voice_notes"/"build.txt"}{os.pathsep}voice_notes',
        # Native libraries and model assets these packages load at run time.
        '--collect-all', 'faster_whisper', '--collect-all', 'ctranslate2', '--collect-all', 'sherpa_onnx',
        '--collect-all', 'av', '--collect-all', 'onnxruntime', '--collect-data', 'certifi',
        '--hidden-import', 'voice_notes.worker']
for t in tools:
    args += ['--add-binary', f'{t}{os.pathsep}ffmpeg']
if sys.platform == 'darwin':
    args += ['--osx-bundle-identifier', 'com.voicenotes.app', '--collect-all', 'mlx', '--collect-all', 'mlx_lm']
PyInstaller.__main__.run(args)
