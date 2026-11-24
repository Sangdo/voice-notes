"""Who is speaking: offline speaker diarization with sherpa-onnx (pyannote segmentation + speaker embeddings).

1. Diarize the 16 kHz audio into turns (start, end, cluster).
2. Clustering splits one voice into several pieces and short interjections into tiny "speakers": pieces
   that sound alike are joined, and what is left that is tiny goes to the closest speaker.
3. Every transcript line gets the speaker it overlaps most, numbered S1, S2, … by first appearance.

Models live in ~/.cache/voice-notes/speakers and are fetched once by `download()` (public GitHub releases).
"""
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FOLDER = Path.home()/'.cache'/'voice-notes'/'speakers'
RELEASES = 'https://github.com/k2-fsa/sherpa-onnx/releases/download'
SEGMENTATION = 'sherpa-onnx-pyannote-segmentation-3-0/model.onnx'
EMBEDDING = 'nemo_en_titanet_small.onnx'
FILES = {SEGMENTATION: f'{RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2',
         EMBEDDING: f'{RELEASES}/speaker-recongition-models/{EMBEDDING}'}
SIZE = '47 MB'

THRESHOLD = 0.7          # clustering distance; lower splits more
WINDOW_SHIFT = 0.1       # segmentation window step (fraction of 10 s); 0.25 is 2× faster but ~0.5% less accurate
BLOCK_SECONDS = 15*60    # diarize in blocks so memory stays flat however long the recording is
SAME_VOICE = 0.55        # clusters whose voiceprints are this similar are one person
MIN_SPEAKER_SECONDS = 8   # a speaker with less speech than this is folded into the closest one


def is_downloaded():
    return all((FOLDER/name).is_file() for name in FILES)


def download(on_progress=None):
    """Fetch the two models (the only network use; called from the worker's download mode)."""
    import ssl
    import tarfile
    import tempfile
    from urllib.request import Request, urlopen
    import certifi
    FOLDER.mkdir(parents=True, exist_ok=True)
    context = ssl.create_default_context(cafile=certifi.where())
    todo = {name: url for name, url in FILES.items() if not (FOLDER/name).is_file()}
    if not todo:
        return
    responses = [urlopen(Request(url, headers={'User-Agent': 'VoiceNotes/1.0'}), timeout=60, context=context)
                 for url in todo.values()]
    total = sum(int(r.headers.get('Content-Length') or 0) for r in responses)
    for (name, url), r in zip(todo.items(), responses):
        with r, tempfile.TemporaryDirectory(dir=FOLDER) as tmp:
            target = Path(tmp)/Path(url).name
            with target.open('wb') as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
                    if on_progress:
                        on_progress(len(chunk), total)
            if target.suffix == '.bz2':
                with tarfile.open(target) as tar:
                    tar.extractall(FOLDER, filter='data')
            else:
                target.replace(FOLDER/name)


def read_wav(path):
    import wave
    with wave.open(str(path)) as w:
        assert w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1 and w.getsampwidth() == 2
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    samples *= 1/32768
    return samples


def diarize(samples, *, threads=4, on_progress=None):
    """[(start, end, cluster)] sorted by time."""
    import sherpa_onnx as so
    config = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(FOLDER/SEGMENTATION),
                                                                    window_shift_ratio=WINDOW_SHIFT),
            num_threads=threads),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(FOLDER/EMBEDDING), num_threads=threads),
        clustering=so.FastClusteringConfig(num_clusters=-1, threshold=THRESHOLD),
        min_duration_on=0.3, min_duration_off=0.5)
    engine = so.OfflineSpeakerDiarization(config)

    def report(done, total):
        if on_progress:
            on_progress(done/total if total else 0)
        return 0
    result = engine.process(samples, callback=report).sort_by_start_time()
    return [(s.start, s.end, s.speaker) for s in result]


def voiceprints(samples, turns, *, threads=4, per_cluster=60.0):
    """Average embedding per cluster from up to `per_cluster` seconds of its longest turns."""
    import sherpa_onnx as so
    extractor = so.SpeakerEmbeddingExtractor(
        so.SpeakerEmbeddingExtractorConfig(model=str(FOLDER/EMBEDDING), num_threads=threads))
    by_cluster = defaultdict(list)
    for start, end, cluster in turns:
        by_cluster[cluster].append((end - start, start, end))
    prints = {}
    for cluster, items in by_cluster.items():
        vectors, used = [], 0.0
        for length, start, end in sorted(items, reverse=True):
            if used >= per_cluster or (length < 0.5 and vectors):
                break
            stream = extractor.create_stream()
            stream.accept_waveform(sample_rate=SAMPLE_RATE,
                                   waveform=samples[int(start*SAMPLE_RATE):int(end*SAMPLE_RATE)])
            stream.input_finished()
            vectors.append(np.asarray(extractor.compute(stream)) * length)
            used += length
        v = np.sum(vectors, axis=0)
        prints[cluster] = v/(np.linalg.norm(v) or 1)
    return prints


def merge_clusters(turns, prints, *, same_voice=SAME_VOICE, min_seconds=MIN_SPEAKER_SECONDS):
    """Join clusters that are one person, then fold tiny leftovers into the closest speaker.

    Clustering splits a voice in several pieces; on court audio, pieces of the same person score well above
    `same_voice` (cosine of averaged embeddings) and different people well below it.
    """
    talk = Counter()
    for start, end, cluster in turns:
        talk[cluster] += end - start
    prints = {c: np.asarray(v) for c, v in prints.items()}
    target = {c: c for c in talk}

    def root(c):
        while target[c] != c:
            c = target[c]
        return c
    while len(prints) > 1:
        keys = list(prints)
        sim, a, b = max((float(prints[a] @ prints[b]), a, b) for i, a in enumerate(keys) for b in keys[i+1:])
        if sim < same_voice:
            break
        keep, gone = (a, b) if talk[a] >= talk[b] else (b, a)
        v = prints[keep]*talk[keep] + prints[gone]*talk[gone]
        prints[keep] = v/(np.linalg.norm(v) or 1)
        talk[keep] += talk.pop(gone)
        del prints[gone]
        target[gone] = keep
    large = [c for c in prints if talk[c] >= min_seconds] or [max(prints, key=talk.get)]
    for c in list(prints):
        if c not in large:
            target[c] = max(large, key=lambda d: float(prints[c] @ prints[d]))
    return [(s, e, root(c)) for s, e, c in turns]


def label_lines(segments, turns):
    """Speaker name ('S1', 'S2', …) for each transcript line, numbered by first appearance."""
    names, out, i = {}, [], 0
    turns = sorted(turns)
    for seg in segments:
        while i < len(turns) and turns[i][1] <= seg.start:
            i += 1
        overlap = Counter()
        j = i
        while j < len(turns) and turns[j][0] < seg.end:
            o = min(turns[j][1], seg.end) - max(turns[j][0], seg.start)
            if o > 0:
                overlap[turns[j][2]] += o
            j += 1
        if not overlap:     # silence inside the diarization: take the nearest turn
            near = min(turns, key=lambda t: min(abs(t[0] - seg.end), abs(t[1] - seg.start)), default=None)
            if near is None:
                out.append('')
                continue
            overlap[near[2]] = 1
        cluster = overlap.most_common(1)[0][0]
        names.setdefault(cluster, f'S{len(names) + 1}')
        out.append(names[cluster])
    return out


def diarize_blocks(samples, *, block_seconds=BLOCK_SECONDS, on_progress=None):
    """diarize() block by block; each block's clusters are distinct and are joined later by voice."""
    step = int(block_seconds*SAMPLE_RATE)
    starts = list(range(0, len(samples), step)) or [0]
    # A short tail joins the previous block (too little speech to cluster on its own).
    if len(starts) > 1 and len(samples) - starts[-1] < step//3:
        starts.pop()
    turns = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(samples)
        offset = start/SAMPLE_RATE
        report = (lambda v, i=i: on_progress((i + v)/len(starts))) if on_progress else None
        turns += [(s + offset, e + offset, (i, c)) for s, e, c in diarize(samples[start:end], on_progress=report)]
    return turns


def identify(samples, segments, *, on_progress=None):
    """Speaker label per segment for audio `samples` (float32, 16 kHz)."""
    turns = diarize_blocks(samples, on_progress=on_progress)
    if not turns:
        return [''] * len(segments)
    turns = merge_clusters(turns, voiceprints(samples, turns))
    return label_lines(segments, turns)
