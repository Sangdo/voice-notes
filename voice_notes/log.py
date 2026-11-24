"""Log file for troubleshooting: what ran, on which device, how long it took, and full errors.

The app and its worker processes append to one file. Recording names and transcript text are never logged:
`redact()` replaces the paths of the current job's files with placeholders.
"""
import faulthandler
import logging
import os
from pathlib import Path
import platform
import sys

NAME = 'voice-notes.log'
MAX_BYTES = 2_000_000
_redactions = {}
_crash_file = None


def folder():
    if os.environ.get('VOICE_NOTES_LOG_DIR'):   # tests
        base = Path(os.environ['VOICE_NOTES_LOG_DIR'])
    elif sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData'/'Local'))/'Voice Notes'/'Logs'
    elif sys.platform == 'darwin':
        base = Path.home()/'Library'/'Logs'/'Voice Notes'
    else:
        base = Path.home()/'.cache'/'voice-notes'/'logs'
    base.mkdir(parents=True, exist_ok=True)
    return base


def path():
    return folder()/NAME


def redact(**paths):
    """Hide these files' names in later log lines, e.g. redact(audio=job['audio'])."""
    for label, value in paths.items():
        if value:
            p = Path(value)
            for text in {str(p), p.as_posix(), p.name, p.stem}:
                if len(text) > 2:
                    _redactions[text] = f'<{label}>'


def scrub(text):
    for secret in sorted(_redactions, key=len, reverse=True):
        text = text.replace(secret, _redactions[secret])
    return text


class _Redact(logging.Filter):
    def filter(self, record):
        record.msg = scrub(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = scrub(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def build():
    try:
        return (Path(__file__).parent/'build.txt').read_text(encoding='utf-8').strip()
    except OSError:
        return 'source'


def setup(role):
    """Start logging for this process ('app' or 'worker'). Returns the logger."""
    target = path()
    if role == 'app':
        try:
            if target.stat().st_size > MAX_BYTES:
                target.replace(target.with_suffix('.old.log'))
        except OSError:
            pass
    handler = logging.FileHandler(target, encoding='utf-8')
    handler.setFormatter(logging.Formatter(f'%(asctime)s {role}[{os.getpid()}] %(levelname)s %(name)s: %(message)s'))
    handler.addFilter(_Redact())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for noisy in ('urllib3', 'httpx', 'httpcore', 'huggingface_hub', 'filelock', 'faster_whisper'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Native crashes (e.g. a GPU driver fault) kill the process without a Python error; this records where.
    global _crash_file
    _crash_file = open(target, 'a', encoding='utf-8')
    faulthandler.enable(_crash_file)
    log = logging.getLogger('voice_notes')
    if role == 'app':
        log.info('---- Voice Notes %s · %s %s (%s) · Python %s%s', build(), platform.system(), platform.release(),
                 platform.machine(), platform.python_version(), ' · packaged' if getattr(sys, 'frozen', False) else '')

    def excepthook(kind, value, tb):
        log.error('Uncaught error', exc_info=(kind, value, tb))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = excepthook
    return log
