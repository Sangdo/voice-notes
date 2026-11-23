"""Transcript data: segments with times, saved as JSON next to the recording."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

VERSION = 1


@dataclass
class Segment:
    start: float
    end: float
    text: str
    starred: bool = False
    note: str = ''
    speaker: str = ''     # 'S1', 'S2', … from speaker identification; names live in the file's `speakers`


def clock(seconds):
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


def sidecar(audio):
    """`hearing.mp3` → `hearing.transcript.json` in the same folder."""
    audio = Path(audio)
    return audio.with_name(audio.stem + '.transcript.json')


def content_key(audio):
    """Short id from the file's size and first megabyte: the same recording keeps it when moved or renamed."""
    audio = Path(audio)
    digest = hashlib.sha1(str(audio.stat().st_size).encode())
    with audio.open('rb') as f:
        digest.update(f.read(1 << 20))
    return digest.hexdigest()[:8]


def notes_path(audio, folder=None):
    """Where new notes for `audio` go: next to it, or in `folder` (named so same-named recordings never clash)."""
    if not folder:
        return sidecar(audio)
    audio = Path(audio)
    return Path(folder)/f'{audio.stem}.{content_key(audio)}.transcript.json'


def find_notes(audio, folder=None):
    """Existing notes for `audio` (notes folder first, then next to the recording), else where to create them."""
    candidates = [notes_path(audio, folder), sidecar(audio)] if folder else [sidecar(audio)]
    return next((p for p in candidates if p.is_file()), candidates[0])


def fingerprint(audio):
    st = Path(audio).stat()
    return {'size': st.st_size, 'mtime_ns': st.st_mtime_ns}


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save(path, audio, segments, *, model='', complete=True, duration=0.0, speakers=None):
    """`speakers` maps labels to names the user gave them, e.g. {'S3': 'Justice Kagan'}."""
    data = {'version': VERSION, 'audio': str(Path(audio).name), 'source': fingerprint(audio),
            'model': model, 'complete': complete, 'duration': duration, 'speakers': speakers or {},
            'segments': [asdict(s) for s in segments]}
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1))


def load(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('version') != VERSION:
        raise ValueError('Unsupported transcript file format.')
    data['segments'] = [Segment(**s) for s in data['segments']]
    data.setdefault('speakers', {})
    return data


def speaker_name(label, names):
    """'S3' → the name given to it, else 'Speaker 3'."""
    if not label:
        return ''
    return names.get(label) or f'Speaker {label[1:]}'


# ---------- reading view: how saved lines are grouped on screen (the file always keeps every line) ----------
GROUPINGS = [('line', 'Lines'), ('sentence', 'Sentences'), ('paragraph', 'Paragraphs')]
DEFAULT_GROUPING = 'paragraph'
PARAGRAPH_SECONDS = 30   # close a paragraph at the first sentence end after this long
PAUSE_SECONDS = 1.0      # …or at a sentence end followed by a pause this long
LONGEST_SECONDS = 90     # never let a sentence without punctuation swallow more than this
# A period after these is not the end of a sentence ("Mr. Reeves", "Roe v. Wade", "U.S. Code").
ABBREVIATIONS = {'mr.', 'mrs.', 'ms.', 'dr.', 'v.', 'vs.', 'jr.', 'sr.', 'st.', 'no.', 'nos.', 'u.s.', 'inc.',
                 'co.', 'corp.', 'e.g.', 'i.e.', 'cf.', 'id.', 'art.', 'sec.', 'para.', 'gen.', 'gov.', 'sen.', 'rep.'}


def ends_sentence(text):
    text = text.rstrip().rstrip('"\'”’)]')
    if not text or text[-1] not in '.?!…':
        return False
    return text.split()[-1].lower() not in ABBREVIATIONS


def starts_group(group, seg, mode):
    """Whether `seg` begins a new row after the segments in `group` (the row being built)."""
    if mode == 'line' or not group:
        return True
    prev = group[-1]
    if seg.speaker and prev.speaker and seg.speaker != prev.speaker:
        return True
    if not ends_sentence(prev.text):
        return seg.end - group[0].start > LONGEST_SECONDS
    if mode == 'sentence':
        return True
    return seg.start - prev.end >= PAUSE_SECONDS or prev.end - group[0].start >= PARAGRAPH_SECONDS


def group_segments(segments, mode):
    """Rows for the reading view, as lists of segment indexes."""
    groups = []
    for i, seg in enumerate(segments):
        if not groups or starts_group([segments[j] for j in groups[-1]], seg, mode):
            groups.append([i])
        else:
            groups[-1].append(i)
    return groups


def to_text(audio_name, segments, *, starred_only=False, groups=None, speakers=None):
    """Plain text, one entry per line or per group (sentence/paragraph) when `groups` is given."""
    lines = [audio_name, '']
    for group in groups or [[i] for i in range(len(segments))]:
        segs = [segments[i] for i in group]
        starred = any(s.starred for s in segs)
        if starred_only and not starred:
            continue
        mark = '★ ' if starred else ''
        who = speaker_name(segs[0].speaker, speakers or {})
        lines.append(f'[{clock(segs[0].start)}] {mark}' + (f'{who}: ' if who else '') + ' '.join(s.text for s in segs if s.text))
        for s in segs:
            if s.note:
                lines.append(f'    Note: {s.note}')
        if groups:
            lines.append('')
    return '\n'.join(lines).rstrip('\n') + '\n'
