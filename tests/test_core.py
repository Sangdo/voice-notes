import json
import subprocess
import sys
from pathlib import Path

import pytest

from voice_notes import engine, models
from voice_notes.transcript import Segment, clock, load, save, sidecar, to_text

ROOT = Path(__file__).resolve().parent.parent


def test_model_catalogue_is_consistent():
    ids = [m.id for m in models.MODELS]
    assert len(ids) == len(set(ids))
    assert models.get(models.DEFAULT_MODEL)
    assert models.DEFAULT_DEVICE in dict(models.DEVICES)
    for spec in models.MODELS:
        assert spec.backend in engine.BACKENDS


def test_unknown_model_is_an_error():
    with pytest.raises(KeyError):
        models.get('nope')


def test_offline_guard_blocks_outside_but_allows_loopback():
    # Run in a child so the socket patch does not leak into other tests.
    code = '''
import socket
from voice_notes import offline
offline.enable()
import os; assert os.environ["HF_HUB_OFFLINE"] == "1"
for target in (("93.184.216.34", 443), ("2606:2800::1", 443)):
    fam = socket.AF_INET6 if ":" in target[0] else socket.AF_INET
    try:
        socket.socket(fam).connect(target)
    except offline.NetworkBlocked:
        pass
    else:
        raise SystemExit("connected outside")
try:
    socket.create_connection(("huggingface.co", 443), timeout=1)
except offline.NetworkBlocked:
    pass
else:
    raise SystemExit("resolved outside")
server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen()
socket.create_connection(server.getsockname(), timeout=1).close()
print("ok")
'''
    out = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr + out.stdout
    assert out.stdout.strip() == 'ok'


def test_transcript_round_trip(tmp_path):
    audio = tmp_path/'hearing one.mp3'
    audio.write_bytes(b'x')
    segs = [Segment(0, 2.5, 'Court is in session.'), Segment(65, 70, 'Objection.', starred=True, note='증거')]
    path = sidecar(audio)
    assert path.name == 'hearing one.transcript.json'
    save(path, audio, segs, model='m', complete=False, duration=70)
    data = load(path)
    assert data['segments'] == segs
    assert data['complete'] is False
    assert data['source']['size'] == 1
    assert not list(tmp_path.glob('.*.tmp'))


def test_text_export():
    segs = [Segment(5, 6, 'Good morning.'), Segment(3725, 3730, 'Sustained.', starred=True, note='중요')]
    full = to_text('a.mp3', segs)
    assert '[00:05] Good morning.' in full
    assert '[1:02:05] ★ Sustained.' in full and 'Note: 중요' in full
    assert 'Good morning' not in to_text('a.mp3', segs, starred_only=True)
    assert clock(59.9) == '00:59' and clock(3600) == '1:00:00'


def test_worker_reports_missing_model(tmp_path, monkeypatch):
    audio = tmp_path/'a.wav'
    audio.write_bytes(b'')
    job = tmp_path/'job.json'
    job.write_text(json.dumps({'mode': 'transcribe', 'audio': str(audio), 'out': str(tmp_path/'o.json'),
                               'workdir': str(tmp_path), 'model': 'turbo'}))
    env = {'HF_HUB_CACHE': str(tmp_path/'empty'), 'PATH': '/usr/bin:/bin', 'VOICE_NOTES_LOG_DIR': str(tmp_path)}
    out = subprocess.run([sys.executable, '-m', 'voice_notes.worker', str(job)], cwd=ROOT,
                         capture_output=True, text=True, env=env)
    assert out.returncode == 1
    event = json.loads(out.stdout.strip().splitlines()[-1])
    assert event['type'] == 'error' and 'Download it in Settings' in event['message']
    logged = (tmp_path/'voice-notes.log').read_text()
    assert 'Start transcribe' in logged and 'not downloaded yet' in logged


def test_notes_folder_keeps_same_named_recordings_apart(tmp_path):
    from voice_notes.transcript import find_notes, notes_path
    a, b = tmp_path/'day1'/'hearing.mp3', tmp_path/'day2'/'hearing.mp3'
    for path, body in ((a, b'first'), (b, b'second')):
        path.parent.mkdir()
        path.write_bytes(body)
    folder = tmp_path/'notes'
    assert find_notes(a) == sidecar(a)                       # no folder: next to the recording
    assert find_notes(a, folder).parent == folder
    assert notes_path(a, folder) != notes_path(b, folder)    # same name, different recording
    save(sidecar(b), b, [Segment(0, 1, 'old')])
    assert find_notes(b, folder) == sidecar(b)               # notes made before the folder was set still open


def test_link_names():
    from voice_notes.worker import link_name
    assert link_name('https://www.supremecourt.gov/media/audio/mp3files/25-352.mp3') == '25-352.mp3'
    assert link_name('https://example.com/a%20hearing.m4a?x=1') == 'a hearing.m4a'
    assert link_name('https://example.com/get?id=7', 'attachment; filename="day 2.mp3"') == 'day 2.mp3'
    assert link_name('https://example.com/page.html') == ''


def test_fetch_downloads_a_link(tmp_path, capsys):
    import http.server
    import threading
    from functools import partial
    from voice_notes import worker
    served = tmp_path/'site'
    served.mkdir()
    (served/'hearing.mp3').write_bytes(b'x' * 200_000)
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/hearing.mp3'
    try:
        worker.fetch({'url': url, 'folder': str(tmp_path/'dl')})
        worker.fetch({'url': url, 'folder': str(tmp_path/'dl')})     # same file again: reused, not duplicated
        try:
            worker.fetch({'url': url.replace('hearing.mp3', ''), 'folder': str(tmp_path/'dl')})
            assert False, 'a page is not a recording'
        except RuntimeError as e:
            assert 'not an audio file' in str(e)
    finally:
        server.shutdown()
    assert [p.name for p in (tmp_path/'dl').iterdir()] == ['hearing.mp3']
    assert (tmp_path/'dl'/'hearing.mp3').stat().st_size == 200_000
    assert capsys.readouterr().out.count('"done"') == 2


def test_sentences_join_lines_split_mid_sentence():
    from voice_notes.transcript import group_segments
    segs = [Segment(0, 3, 'Next, in case 25-581, St. Mary Catholic Parish v.'),
            Segment(3, 5, 'Roy.'),
            Segment(6, 8, 'Mr. Reeves.'),
            Segment(8, 12, 'The state has denied Colorado families the ability to use universal preschool funding at Catholic'),
            Segment(12, 15, 'schools. Under this court\'s cases, that triggers strict scrutiny.')]
    assert group_segments(segs, 'line') == [[0], [1], [2], [3], [4]]
    assert group_segments(segs, 'sentence') == [[0, 1], [2], [3, 4]]     # "v." and "Mr." do not end a sentence


def test_paragraphs_break_at_pauses_and_length():
    from voice_notes.transcript import group_segments
    talk = [Segment(i*5, i*5 + 5, f'Point {i}.') for i in range(10)]          # 50 s without a pause
    assert group_segments(talk, 'paragraph') == [[0, 1, 2, 3, 4, 5], [6, 7, 8, 9]]   # closes at 30 s
    paused = [Segment(0, 4, 'Thank you, counsel.'), Segment(6, 9, 'Mr. Chief Justice.')]
    assert group_segments(paused, 'paragraph') == [[0], [1]]                    # 2 s pause
    runon = [Segment(i*10, i*10 + 10, 'and then') for i in range(12)]          # no punctuation at all
    assert len(group_segments(runon, 'paragraph')) > 1


def test_export_by_paragraph():
    segs = [Segment(0, 2, 'Good morning.'), Segment(2, 4, 'May it please the Court.', starred=True, note='opening')]
    text = to_text('h.mp3', segs, groups=[[0, 1]])
    assert '[00:00] ★ Good morning. May it please the Court.' in text
    assert '    Note: opening' in text


def test_log_hides_recording_names(tmp_path):
    from voice_notes import log as logfile
    audio = tmp_path/'Smith v Jones sealed.m4a'
    logfile.redact(audio=str(audio))
    text = logfile.scrub(f"{audio}: Invalid data · Smith v Jones sealed.transcript.json")
    assert 'Smith' not in text and '<audio>' in text
    logfile._redactions.clear()
