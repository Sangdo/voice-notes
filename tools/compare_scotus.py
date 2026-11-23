"""Compare our transcript against an official Supreme Court argument transcript (PDF).

    .venv/bin/python tools/compare_scotus.py testdata/25-5930.transcript.json testdata/25-5930.pdf

Reports word error rate (WER) overall and per 10-minute block, plus the most common
substitutions, so model choices can be compared on real courtroom audio.
Dev-only: needs `pip install jiwer pypdf`.
"""
from collections import Counter
import json
import re
import sys

import jiwer
from pypdf import PdfReader

SPEAKER = re.compile(r"\b(?:CHIEF JUSTICE|JUSTICE|MR\.|MS\.|MRS\.)\s+[A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-]+)?:")
HEADING = re.compile(r'(?:REBUTTAL )?(?:ORAL )?ARGUMENT OF (?:[A-Z][A-Z.,\-]*\s+)+')
NUMBER_WORDS = {'0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four', '5': 'five', '6': 'six',
                '7': 'seven', '8': 'eight', '9': 'nine', '10': 'ten'}


def reference_text(pdf):
    pages = []
    for page in PdfReader(pdf).pages:
        text = page.extract_text() or ''
        lines = [l.strip() for l in text.splitlines()]
        lines = [l for l in lines if l and not re.fullmatch(r'\d{1,3}', l)
                 and l not in ('Official - Subject to Final Review', 'Heritage Reporting Corporation')]
        pages.append(' '.join(lines))
    text = ' '.join(pages)
    text = text.split('P R O C E E D I N G S', 1)[-1]
    text = re.split(r'\(Whereupon,', text, 1)[0]
    text = re.sub(r'\(\d{1,2}:\d{2} [ap]\.m\.\)', ' ', text)
    text = SPEAKER.sub(' ', text)
    text = HEADING.sub(' ', text)
    return text


def normalize(text):
    text = text.lower().replace('’', "'")
    text = re.sub(r'\bv\.\s', ' versus ', text)
    text = re.sub(r'(\d)-(\d)', r'\1\2', text)              # docket 25-5930 vs 255930
    text = re.sub(r'(\w)\((\w{1,3})\)', r'\1\2', text)       # 3621(b) vs 3621b
    text = re.sub(r'\bpre-(?=\w)', 'pre', text)                # pre-release vs prerelease
    text = re.sub(r'(?<=\w)-(?=\w)', ' ', text)              # good-time vs good time
    text = re.sub(r'\s-+\s|--', ' ', text)                  # dashes mark restarts
    text = re.sub(r"[^\w\s']", ' ', text)
    words = [NUMBER_WORDS.get(w, w) for w in text.split()]
    words = [w for w in words if w not in ('uh', 'um', 'mm', 'hmm')]
    return ' '.join(words)


def main(ours_path, pdf):
    data = json.loads(open(ours_path, encoding='utf-8').read())
    segments = data['segments']
    hyp = normalize(' '.join(s['text'] for s in segments))
    ref = normalize(reference_text(pdf))
    out = jiwer.process_words(ref, hyp)
    print(f"model: {data.get('model')}  complete: {data.get('complete')}  segments: {len(segments)}")
    print(f'reference words: {len(ref.split())}  ours: {len(hyp.split())}')
    print(f'WER {out.wer:.1%}  (substitutions {out.substitutions}, deletions {out.deletions}, '
          f'insertions {out.insertions})')

    subs = Counter()
    for chunk in out.alignments[0]:
        if chunk.type == 'substitute':
            r = out.references[0][chunk.ref_start_idx:chunk.ref_end_idx]
            h = out.hypotheses[0][chunk.hyp_start_idx:chunk.hyp_end_idx]
            for a, b in zip(r, h):
                subs[(a, b)] += 1
    print('\nmost common substitutions (reference → ours):')
    for (a, b), n in subs.most_common(20):
        print(f'  {n:3d}  {a} → {b}')

    # Rough per-block view: align ours-only words back to segment times.
    print('\nper 10-minute block (our words / share of insert+delete issues is approximate):')
    marks, count = [], 0
    for s in segments:
        count += len(normalize(s['text']).split())
        marks.append((s['end'], count))
    bounds = []
    for limit in range(600, int(segments[-1]['end']) + 600, 600):
        n = next((c for e, c in marks if e >= limit), count)
        bounds.append((limit, n))
    errors = [0] * (count + 1)
    for chunk in out.alignments[0]:
        if chunk.type != 'equal':
            pos = min(chunk.hyp_start_idx, count)
            errors[pos] += max(chunk.ref_end_idx - chunk.ref_start_idx, chunk.hyp_end_idx - chunk.hyp_start_idx)
    start = 0
    for limit, n in bounds:
        words = n - start
        if words:
            print(f'  {(limit-600)//60:3d}-{limit//60:3d} min  {words:5d} words  ~{sum(errors[start:n])/words:.1%} errors')
        start = n


if __name__ == '__main__':
    main(*sys.argv[1:3])
