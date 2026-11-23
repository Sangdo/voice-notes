"""Separate process for heavy work, so the window stays responsive and cancel can kill it.

Usage: python -m voice_notes.worker job.json
Prints one JSON event per line: progress, device, segment, done, error.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def emit(kind, **payload):
    print(json.dumps({'type': kind, **payload}, ensure_ascii=False), flush=True)


# Keep FFmpeg from flashing a console window on Windows.
NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def tool(name):
    """FFmpeg tools: the copy bundled with the packaged app first, then the system ones."""
    exe = name + ('.exe' if os.name == 'nt' else '')
    if getattr(sys, 'frozen', False):
        bundled = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent), 'ffmpeg', exe)
        if bundled.is_file():
            return str(bundled)
    found = shutil.which(name)
    for folder in ('/opt/homebrew/bin', '/usr/local/bin'):
        if not found and Path(folder, name).is_file():
            found = str(Path(folder, name))
    if not found:
        hint = 'brew install ffmpeg' if sys.platform == 'darwin' else 'reinstall the app'
        raise RuntimeError(f'{name} was not found. Install FFmpeg ({hint}).')
    return found


def probe_duration(audio):
    out = subprocess.run([tool('ffprobe'), '-v', 'error', '-show_entries', 'format=duration',
                          '-of', 'csv=p=0', str(audio)], capture_output=True, text=True, **NO_WINDOW)
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise RuntimeError('Could not read the audio file.\n' + out.stderr.strip()[-400:]) from None


def transcribe(job):
    from . import offline
    offline.enable()  # Before any model library is imported.
    from . import engine, models
    from .transcript import Segment, load, save

    audio = Path(job['audio'])
    out = Path(job['out'])
    work = Path(job['workdir'])
    spec = models.get(job['model'])
    model_dir = models.local_path(spec)
    if not model_dir:
        raise RuntimeError(f'{spec.label} is not downloaded yet. Download it in Settings.')

    emit('progress', stage='Reading audio', value=-1)
    duration = probe_duration(audio)
    segments = []
    resume = 0.0
    names = {}
    if job.get('resume') and out.is_file():
        previous = load(out)
        segments = previous['segments']
        names = previous['speakers']
        resume = segments[-1].end if segments else 0.0
    emit('progress', stage='Preparing audio', value=-1)
    wav = work/'audio.wav'
    result = subprocess.run([tool('ffmpeg'), '-nostdin', '-v', 'error', '-ss', str(resume), '-i', str(audio),
                             '-vn', '-ac', '1', '-ar', '16000', '-y', str(wav)], capture_output=True, text=True, **NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Could not convert the audio.\n' + result.stderr.strip()[-400:])

    emit('progress', stage=f'Loading {spec.label}', value=-1)
    saved = time.monotonic()
    label = f'{spec.label}'

    def on_device(dev):
        nonlocal label
        label = f"{spec.label} · {'Mac GPU' if dev == 'mps' else 'CPU'}"
        emit('device', device=dev)
        emit('progress', stage='Transcribing', value=resume/duration if duration else -1)

    for start, end, text in engine.transcribe(spec, model_dir, wav, device=job.get('device', 'auto'),
                                              hint=job.get('hint', ''), on_device=on_device):
        seg = Segment(round(start+resume, 2), round(end+resume, 2), text)
        segments.append(seg)
        emit('segment', start=seg.start, end=seg.end, text=seg.text)
        emit('progress', stage='Transcribing', value=min(1.0, seg.end/duration) if duration else -1)
        if time.monotonic() - saved > 20:
            save(out, audio, segments, model=label, complete=False, duration=duration, speakers=names)
            saved = time.monotonic()
    save(out, audio, segments, model=label, complete=True, duration=duration, speakers=names)
    emit('done', path=str(out), count=len(segments), model=label)


def identify_speakers(job):
    """Label every line of saved notes with its speaker (S1, S2, …), offline, and write them back."""
    from . import offline
    offline.enable()
    from . import speakers
    from .transcript import load, save

    if not speakers.is_downloaded():
        raise RuntimeError('Speaker models are not downloaded yet. Download them in Settings.')
    audio, notes, work = Path(job['audio']), Path(job['notes']), Path(job['workdir'])
    emit('progress', stage='Preparing audio', value=-1)
    wav = work/'audio.wav'
    result = subprocess.run([tool('ffmpeg'), '-nostdin', '-v', 'error', '-i', str(audio), '-vn', '-ac', '1',
                             '-ar', str(speakers.SAMPLE_RATE), '-y', str(wav)], capture_output=True, text=True, **NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Could not convert the audio.\n' + result.stderr.strip()[-400:])
    samples = speakers.read_wav(wav)
    segments = load(notes)['segments']
    labels = speakers.identify(samples, segments,
                               on_progress=lambda v: emit('progress', stage='Identifying speakers', value=v))
    # Re-read just before writing: the app may have saved stars or notes meanwhile.
    data = load(notes)
    by_start = {(s.start, s.end): label for s, label in zip(segments, labels)}
    for seg in data['segments']:
        seg.speaker = by_start.get((seg.start, seg.end), seg.speaker)
    save(notes, audio, data['segments'], model=data.get('model', ''), complete=bool(data.get('complete')),
         duration=data.get('duration', 0.0), speakers=data['speakers'])
    emit('done', path=str(notes), labels=[[s.start, s.end, l] for s, l in zip(segments, labels)],
         count=len(set(l for l in labels if l)))


def summarize(job):
    """Summarise saved notes with a local language model, offline like transcription."""
    from . import offline
    offline.enable()
    from . import models
    from .summarize import mlx_asker, save_summary, summarize as run
    from .transcript import load

    spec = models.get(job['model'])
    model_dir = models.local_path(spec)
    if not model_dir:
        raise RuntimeError(f'{spec.label} is not downloaded yet.')
    notes = load(job['notes'])
    segments = notes['segments']
    emit('progress', stage=f'Loading {spec.label}', value=-1)
    ask = mlx_asker(model_dir)
    overview, sections = run(
        segments, ask,
        on_progress=lambda done, total: emit('progress', stage='Summarizing' if done < total - 1 else 'Writing overview',
                                             value=done/total),
        on_section=lambda s: emit('section', start=s.start, end=s.end, text=s.text), names=notes['speakers'])
    save_summary(job['out'], model=spec.label, overview=overview, sections=sections, lines=len(segments))
    emit('done', path=job['out'])


def download(job):
    if job['model'] == 'speakers':
        from . import speakers
        speakers.download(lambda name: emit('progress', stage=f'Downloading {name}', value=-1))
        emit('done', model='speakers')
        return
    from huggingface_hub import snapshot_download
    from . import models
    spec = models.get(job['model'])
    emit('progress', stage=f'Downloading {spec.label} ({spec.size})', value=-1)
    snapshot_download(spec.repo)
    emit('done', model=spec.id)


AUDIO_TYPES = ('.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg', '.opus', '.wma', '.mp4', '.mov', '.m4v', '.webm')


def link_name(url, disposition=''):
    """File name for a recording link, from Content-Disposition or the URL path."""
    import re
    from urllib.parse import unquote, urlsplit
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition or '')
    name = Path(unquote(match.group(1) if match else urlsplit(url).path)).name
    return name if name.lower().endswith(AUDIO_TYPES) else ''


def free_name(folder, name):
    path = folder/name
    n = 1
    while path.exists():
        path = folder/f'{Path(name).stem} ({n}){Path(name).suffix}'
        n += 1
    return path


def fetch(job):
    """Download one recording link into `folder` (the only time a recording comes from the internet)."""
    import ssl
    from urllib.request import Request, urlopen
    import certifi   # python.org builds ship without CA certificates
    url, folder = job['url'], Path(job['folder'])
    folder.mkdir(parents=True, exist_ok=True)
    emit('progress', stage='Connecting', value=-1)
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(Request(url, headers={'User-Agent': 'VoiceNotes/1.0'}), timeout=30, context=context) as response:
        name = link_name(response.geturl(), response.headers.get('Content-Disposition', '')) or link_name(url)
        if not name:
            raise RuntimeError('This link is not an audio file (mp3, m4a, wav, …).')
        total = int(response.headers.get('Content-Length') or 0)
        existing = folder/name
        if existing.is_file() and total and existing.stat().st_size == total:
            emit('done', path=str(existing))   # already downloaded
            return
        target = free_name(folder, name)
        part = target.with_name(target.name + '.part')
        got, shown = 0, 0.0
        try:
            with part.open('wb') as f:
                while chunk := response.read(1 << 16):
                    f.write(chunk)
                    got += len(chunk)
                    if time.monotonic() - shown > .25:
                        emit('progress', stage=name, value=got/total if total else -1, bytes=got)
                        shown = time.monotonic()
            if total and got < total:
                raise RuntimeError('The download was cut off.')
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
    emit('done', path=str(target))


def main():
    job = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(2))
    try:
        {'transcribe': transcribe, 'download': download, 'fetch': fetch, 'summarize': summarize,
         'speakers': identify_speakers}[job['mode']](job)
    except SystemExit:
        raise
    except Exception as e:
        emit('error', message=str(e) or type(e).__name__)
        sys.exit(1)
    finally:
        if job.get('workdir'):
            (Path(job['workdir'])/'audio.wav').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
