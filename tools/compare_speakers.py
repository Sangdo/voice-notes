"""Score speaker labels against an official Supreme Court transcript (PDF), which names every speaker.

    .venv/bin/python tools/compare_speakers.py testdata/25-352.transcript.json testdata/25-352.pdf [turns.json]

Aligns the official words to our lines, gives each line its true speaker, then reports how much of the
speech carries the right label (each of our speakers counts as whichever real speaker it mostly covers).
Without turns.json the transcript's own `speaker` fields are scored. Dev-only: needs pypdf.
"""
from collections import Counter, defaultdict
import difflib
import json
import re
import sys

sys.path.insert(0, __file__.rsplit('/tools/', 1)[0])
from tools.compare_scotus import HEADING, normalize  # noqa: E402
from pypdf import PdfReader  # noqa: E402

LABEL = re.compile(r"\b((?:CHIEF JUSTICE|JUSTICE|MR\.|MS\.|MRS\.)\s+[A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-]+)?):")


def reference_words(pdf):
    """[(word, speaker)] in order."""
    pages = []
    for page in PdfReader(pdf).pages:
        lines = [l.strip() for l in (page.extract_text() or '').splitlines()]
        pages.append(' '.join(l for l in lines if l and not re.fullmatch(r'\d{1,3}', l)
                              and l not in ('Official - Subject to Final Review', 'Heritage Reporting Corporation')))
    text = ' '.join(pages).split('P R O C E E D I N G S', 1)[-1]
    text = re.split(r'\(Whereupon,', text, 1)[0]
    text = re.sub(r'\(\d{1,2}:\d{2} [ap]\.m\.\)', ' ', text)
    parts = LABEL.split(text)          # split on speaker labels first: a heading sits right before one
    words = []
    for speaker, said in zip(parts[1::2], parts[2::2]):
        words += [(w, speaker) for w in normalize(HEADING.sub(' ', said)).split()]
    return words


def truth_per_line(segments, ref):
    hyp, owner = [], []
    for i, seg in enumerate(segments):
        for w in normalize(seg['text']).split():
            hyp.append(w)
            owner.append(i)
    votes = defaultdict(Counter)
    matcher = difflib.SequenceMatcher(None, [w for w, _ in ref], hyp, autojunk=False)
    for a, b, n in matcher.get_matching_blocks():
        for k in range(n):
            votes[owner[b + k]][ref[a + k][1]] += 1
    return {i: v.most_common(1)[0][0] for i, v in votes.items()}


def predicted(segments, turns):
    out = {}
    for i, seg in enumerate(segments):
        overlap = Counter()
        for start, end, spk in turns:
            o = min(end, seg['end']) - max(start, seg['start'])
            if o > 0:
                overlap[spk] += o
        out[i] = overlap.most_common(1)[0][0] if overlap else None
    return out


def score(segments, truth, pred):
    by_cluster = defaultdict(Counter)
    for i, t in truth.items():
        if pred.get(i) is not None:
            by_cluster[pred[i]][t] += segments[i]['end'] - segments[i]['start']
    mapping = {c: v.most_common(1)[0][0] for c, v in by_cluster.items()}
    total = right = 0.0
    for i, t in truth.items():
        d = segments[i]['end'] - segments[i]['start']
        total += d
        right += d * (mapping.get(pred.get(i)) == t)
    return right/total, len(by_cluster), len(set(mapping.values())), mapping


def main(notes, pdf, turns_file=None):
    segments = json.load(open(notes, encoding='utf-8'))['segments']
    truth = truth_per_line(segments, reference_words(pdf))
    if turns_file:
        pred = predicted(segments, [tuple(t) for t in json.load(open(turns_file))])
    else:
        pred = {i: s.get('speaker') or None for i, s in enumerate(segments)}
    acc, clusters, covered, _ = score(segments, truth, pred)
    real = len(set(truth.values()))
    print(f'lines with a known speaker: {len(truth)}/{len(segments)}  real speakers: {real}')
    print(f'speaker accuracy (by time): {acc*100:.1f}%  our speakers: {clusters}  real speakers found: {covered}/{real}')


if __name__ == '__main__':
    main(*sys.argv[1:])
