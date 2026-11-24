"""One row per transcription (and speaker identification) in transcriptions.csv, next to the log file.

For later statistics: how long each job took, on which device and computer. Recording names and transcript text
are never written; error messages are scrubbed of the job's file names like the log.
"""
import csv
from datetime import datetime
import functools
import logging
import os
from pathlib import Path
import platform
import subprocess
import sys

from . import log as logfile

NAME = 'transcriptions.csv'
COLUMNS = ['date', 'time', 'task', 'result', 'audio_min', 'processed_min', 'took_min', 'speed_x', 'device',
           'model', 'lines', 'startup_sec', 'audio_type', 'audio_mb', 'app', 'os', 'cpu', 'gpu', 'error']
NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
log = logging.getLogger('voice_notes.stats')


def path():
    return logfile.folder()/NAME


def _run(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5, **NO_WINDOW).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


@functools.cache
def hardware():
    """(cpu, gpu) names, e.g. ('Apple M2 Pro', 'Apple GPU') or ('Intel Core i7-12700', 'NVIDIA GeForce RTX 3060')."""
    cpu, gpu = '', ''
    if sys.platform == 'darwin':
        cpu = _run('sysctl', '-n', 'machdep.cpu.brand_string')
        gpu = 'Apple GPU' if platform.machine() == 'arm64' else ''
    elif sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                cpu = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
        except OSError:
            pass
        from . import gpu as nvidia
        if nvidia.has_nvidia():
            smi = 'nvidia-smi'
            system = Path(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'nvidia-smi.exe')
            if system.is_file():
                smi = str(system)
            names = _run(smi, '--query-gpu=name', '--format=csv,noheader').splitlines()
            gpu = names[0].strip() if names else 'NVIDIA'
    else:
        try:
            cpu = next(line.split(':', 1)[1].strip() for line in open('/proc/cpuinfo')
                       if line.startswith('model name'))
        except (OSError, StopIteration):
            pass
    return cpu or platform.processor() or platform.machine(), gpu


def audio_info(audio):
    try:
        p = Path(audio)
        return {'audio_type': p.suffix.lower().lstrip('.'), 'audio_mb': round(p.stat().st_size/1e6, 1)}
    except (OSError, TypeError):
        return {}


def record(task, result, *, seconds, audio_sec=None, from_sec=0.0, **fields):
    """Append one row. Never raises: statistics must not break a transcription."""
    try:
        now = datetime.now()
        processed = max(0.0, (audio_sec or 0) - (from_sec or 0)) if audio_sec else None
        cpu, gpu = hardware()
        row = {'date': now.strftime('%Y-%m-%d'), 'time': now.strftime('%H:%M'), 'task': task, 'result': result,
               'audio_min': round(audio_sec/60, 1) if audio_sec else '',
               'processed_min': round(processed/60, 1) if processed is not None else '',
               'took_min': round(seconds/60, 1),
               'speed_x': round(processed/seconds, 1) if processed and seconds > 0 else '',
               'app': logfile.build().split()[0], 'os': f'{platform.system()} {platform.release()}',
               'cpu': cpu, 'gpu': gpu}
        row.update({k: v for k, v in fields.items() if v is not None})
        if row.get('error'):
            row['error'] = logfile.scrub(' '.join(str(row['error']).split()))[:200]
        target = path()
        new = not target.is_file() or target.stat().st_size == 0
        # utf-8-sig on a new file so Excel reads it as UTF-8.
        with open(target, 'a', newline='', encoding='utf-8-sig' if new else 'utf-8') as f:
            writer = csv.DictWriter(f, COLUMNS, extrasaction='ignore')
            if new:
                writer.writeheader()
            writer.writerow(row)
        log.info('Stats: %s %s · %s min audio in %s min (%sx) · %s · %s', task, result, row['processed_min'],
                 row['took_min'], row['speed_x'], row.get('device', ''), row.get('model', ''))
    except Exception:
        log.warning('Could not write statistics', exc_info=True)
