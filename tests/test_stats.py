"""transcriptions.csv: one row per job, readable by Excel, never the recording's name."""
import csv

from voice_notes import log as logfile, stats


def rows(tmp_path):
    return list(csv.DictReader(open(tmp_path/stats.NAME, encoding='utf-8-sig', newline='')))


def test_record_appends_rows_with_speed(tmp_path, monkeypatch):
    monkeypatch.setenv('VOICE_NOTES_LOG_DIR', str(tmp_path))
    stats.record('transcribe', 'done', seconds=600, audio_sec=3600, device='NVIDIA GPU', model='Turbo', lines=900)
    stats.record('transcribe', 'stopped', seconds=60, audio_sec=3600, from_sec=1800)
    first, second = rows(tmp_path)
    assert (first['audio_min'], first['processed_min'], first['took_min'], first['speed_x']) == ('60.0', '60.0', '10.0', '6.0')
    assert first['device'] == 'NVIDIA GPU' and first['lines'] == '900' and first['cpu']
    assert second['processed_min'] == '30.0' and second['result'] == 'stopped'
    assert (tmp_path/stats.NAME).read_bytes().startswith(b'\xef\xbb\xbfdate,')   # one BOM, at the top


def test_record_hides_file_names_in_errors(tmp_path, monkeypatch):
    monkeypatch.setenv('VOICE_NOTES_LOG_DIR', str(tmp_path))
    logfile.redact(audio=str(tmp_path/'Smith v Jones sealed.m4a'))
    stats.record('transcribe', 'failed', seconds=5, error=f'Could not read {tmp_path}/Smith v Jones sealed.m4a')
    text = (tmp_path/stats.NAME).read_text(encoding='utf-8-sig')
    assert 'Smith' not in text and '<audio>' in text


def test_record_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv('VOICE_NOTES_LOG_DIR', str(tmp_path/'file'))
    (tmp_path/'file').write_text('not a folder')
    stats.record('transcribe', 'done', seconds=1)
