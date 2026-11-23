import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from voice_notes.summarize import (Section, clean, load_summary, save_summary, split_sections, summarize,
                                   summary_path)
from voice_notes.transcript import Segment


def talk(minutes):
    return [Segment(i*10, i*10 + 10, f'Statement number {i}.') for i in range(minutes*6)]


def test_sections_are_about_eight_minutes_and_cover_everything():
    parts = split_sections(talk(30))
    assert len(parts) == 4
    assert parts[0][0] == 0 and parts[-1][1] == 1800
    assert all(b[0] == a[1] for a, b in zip(parts, parts[1:]))          # no gaps, no overlap
    assert sum(p[2].count('Statement') for p in parts) == 180           # every line is used once


def test_short_tail_joins_the_last_section():
    parts = split_sections(talk(17))                                     # 8 + 8 + 1 minutes
    assert len(parts) == 2 and parts[-1][1] == 1020


def test_summarize_uses_section_times_and_reports_progress():
    calls, progress, live = [], [], []

    def ask(messages, max_tokens):
        calls.append(messages[-1]['content'])
        return '<think>hidden</think>- point' if len(calls) < 3 else 'Overview\nIt was argued. [00:00]'
    overview, sections = summarize(talk(16), ask, on_progress=lambda d, t: progress.append((d, t)),
                                   on_section=live.append)
    assert [s.start for s in sections] == [0, 480]
    assert sections[0].text == '- point'                                  # reasoning block removed
    assert '(00:00–08:00)' in calls[0] and '[08:00]' in calls[-1]         # overview sees each section's time
    assert overview.startswith('Overview')
    assert progress[-1] == (2, 3) and len(live) == 2


def test_summary_file_round_trip(tmp_path):
    path = summary_path(tmp_path/'hearing.transcript.json')
    assert path.name == 'hearing.summary.json'
    save_summary(path, model='Qwen3 4B', overview='Overview', sections=[Section(0, 60, '- a')], lines=12)
    data = load_summary(path)
    assert data['sections'][0].text == '- a' and data['lines'] == 12


def test_clean_strips_whitespace():
    assert clean('  \n- a \n') == '- a'


def test_sections_mark_speaker_changes():
    segs = [Segment(0, 2, 'May it please the Court.', speaker='S1'), Segment(2, 4, 'Why?', speaker='S2'),
            Segment(4, 6, 'Because.', speaker='S1')]
    (_, _, text), = split_sections(segs, names={'S2': 'Justice Kagan'})
    assert text == 'Speaker 1: May it please the Court. \nJustice Kagan: Why? \nSpeaker 1: Because.'
