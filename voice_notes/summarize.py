"""Offline summaries of a transcript with a local language model.

The transcript is cut into sections of about SECTION_SECONDS (at sentence ends), each section is summarised
on its own, and an overview is written from those section summaries. Every section keeps its start time,
so the summary can be checked against the recording. Generation is a plain function `ask(messages, max_tokens)`
so the logic can be tested without a model; `mlx_asker` builds the real one.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import time

from .transcript import atomic_write, clock, ends_sentence, speaker_name

SECTION_SECONDS = 8*60
VERSION = 1

SYSTEM = (
    'You summarize machine-generated transcripts of court proceedings (for example U.S. Supreme Court oral '
    'arguments) for a lawyer. Be accurate, neutral and specific. Use only what the transcript says: never invent '
    'names, holdings, citations or facts. The transcript has no speaker labels and may contain recognition errors; '
    'attribute a statement to a Justice or to counsel only when the text makes it clear (for example, '
    '"Mr. Chief Justice, and may it please the Court" opens an attorney\'s argument, and questions usually come '
    'from the bench). Lines may be marked with who is speaking ("Speaker 2:" or a name the user gave, '
    'such as "Justice Kagan:"); speaker labels come from automatic voice matching and are usually but not '
    'always right. Use given names; for generic labels, describe the role (a Justice, counsel) when clear. '
    'Write in English.'
)


@dataclass
class Section:
    start: float
    end: float
    text: str        # the summary of this part


def summary_path(notes_file):
    """`hearing.transcript.json` → `hearing.summary.json` next to it."""
    notes_file = Path(notes_file)
    name = notes_file.name
    stem = name[:-len('.transcript.json')] if name.endswith('.transcript.json') else notes_file.stem
    return notes_file.with_name(stem + '.summary.json')


def save_summary(path, *, model, overview, sections, lines):
    data = {'version': VERSION, 'model': model, 'created': time.strftime('%Y-%m-%d %H:%M'), 'lines': lines,
            'overview': overview, 'sections': [asdict(s) for s in sections]}
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1))


def load_summary(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('version') != VERSION:
        raise ValueError('Unsupported summary file format.')
    data['sections'] = [Section(**s) for s in data['sections']]
    return data


def split_sections(segments, seconds=SECTION_SECONDS, names=None):
    """[(start, end, text)] of about `seconds` each, closed at a sentence end when possible.
    When lines have speakers, the text marks each change of speaker ("Justice Kagan: …")."""
    sections, current = [], []
    for seg in segments:
        if not seg.text:
            continue
        current.append(seg)
        long_enough = current[-1].end - current[0].start >= seconds
        if long_enough and (ends_sentence(seg.text) or current[-1].end - current[0].start >= seconds*1.5):
            sections.append(current)
            current = []
    if current:
        # A short tail joins the previous section rather than standing alone.
        if sections and current[-1].end - current[0].start < seconds/3:
            sections[-1].extend(current)
        else:
            sections.append(current)
    def text(part):
        out, last = [], None
        for x in part:
            if x.speaker and x.speaker != last:
                out.append(('\n' if out else '') + speaker_name(x.speaker, names or {}) + ':')
                last = x.speaker
            out.append(x.text)
        return ' '.join(out)
    return [(s[0].start, s[-1].end, text(s)) for s in sections]


def section_messages(start, end, text):
    return [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content':
                f'Transcript excerpt ({clock(start)}–{clock(end)}):\n\n{text}\n\n'
                'Summarize this excerpt in 3 to 6 bullet points. Cover the arguments made, the questions asked from '
                'the bench and how counsel answered, and any concession or notable statement. When a bullet is about '
                'a question from the bench, begin it with "Question:". Start each bullet with "- ". '
                'Do not add a heading or an introduction.'}]


def overview_messages(sections):
    body = '\n\n'.join(f'[{clock(s.start)}]\n{s.text}' for s in sections)
    return [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content':
                f'Below are summaries of consecutive parts of one proceeding, each with its start time.\n\n{body}\n\n'
                'Write an overall summary with exactly these four headings, each on its own line:\n'
                'Overview\nKey arguments\nQuestions from the bench\nNotable concessions or statements\n'
                'Under Overview write 2 or 3 sentences. Under the other headings write bullets starting with "- " '
                'and end each bullet with the start time of the part it comes from in square brackets, like [12:30]. '
                'Questions from the bench are the bullets marked "Question:" and anything described as asked, '
                'pressed or questioned by a Justice or the Court; list the most important ones and the answers given. '
                'Write "- None noted." only when the parts truly contain nothing for a heading.'}]


def clean(text):
    """Drop any reasoning block and stray whitespace a model may produce."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S)
    return text.strip()


def summarize(segments, ask, *, on_progress=None, on_section=None, seconds=SECTION_SECONDS, names=None):
    """Returns (overview, sections). on_progress(done, total) and on_section(Section) report as it goes."""
    parts = split_sections(segments, seconds, names)
    total = len(parts) + 1
    sections = []
    for i, (start, end, text) in enumerate(parts):
        if on_progress:
            on_progress(i, total)
        section = Section(start, end, clean(ask(section_messages(start, end, text), 400)))
        sections.append(section)
        if on_section:
            on_section(section)
    if on_progress:
        on_progress(len(parts), total)
    overview = clean(ask(overview_messages(sections), 1600)) if sections else ''
    return overview, sections


def mlx_asker(model_dir):
    """`ask(messages, max_tokens)` backed by an MLX model folder (loaded once)."""
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler
    model, tokenizer = load(str(model_dir))
    sampler = make_sampler(temp=0.2, top_p=0.9)

    def ask(messages, max_tokens):
        prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False,
                                               enable_thinking=False)
        return generate(model, tokenizer, prompt, max_tokens=max_tokens, sampler=sampler, verbose=False)
    return ask
