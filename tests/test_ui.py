import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from voice_notes import app as appmod
from voice_notes.transcript import Segment, load, save, sidecar


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path/'settings'))
    w = appmod.Window()
    yield w
    w.save_timer.stop()
    w.job = None        # a fake job would otherwise make close() ask "Stop transcribing?"
    w.diar = None
    w.close()


def make_recording(tmp_path, complete=True):
    audio = tmp_path/'hearing.mp3'
    audio.write_bytes(b'not really audio')
    segs = [Segment(0, 4, 'The court will come to order.'),
            Segment(10, 14, 'Objection, Your Honor, hearsay.'),
            Segment(20, 25, 'Exhibit twelve is admitted.')]
    save(sidecar(audio), audio, segs, model='test', complete=complete, duration=25)
    return audio


def test_open_loads_saved_notes(window, tmp_path):
    window.open(make_recording(tmp_path))
    assert window.table.rowCount() == 3
    assert window.stack.currentIndex() == 1
    assert 'Loaded saved notes' in window.status.text()
    assert window.run_button.text().strip() == 'Transcribe'


def test_search_filters_rows(window, tmp_path):
    window.open(make_recording(tmp_path))
    window.search.setText('OBJECTION')
    hidden = [window.table.isRowHidden(r) for r in range(3)]
    assert hidden == [True, False, True]
    assert window.count.text() == '1 paragraph'
    window.search.clear()
    assert not any(window.table.isRowHidden(r) for r in range(3))


def test_star_and_note_are_saved(window, tmp_path):
    audio = make_recording(tmp_path)
    window.open(audio)
    window.on_click(1, appmod.STAR)
    window.table.item(2, appmod.NOTE).setText('증거 채택')
    window.save_now()
    data = load(sidecar(audio))
    assert data['segments'][1].starred
    assert data['segments'][2].note == '증거 채택'
    window.starred_only.setChecked(True)
    assert [window.table.isRowHidden(r) for r in range(3)] == [True, False, True]


def test_click_seeks_to_line(window, tmp_path, monkeypatch):
    window.open(make_recording(tmp_path))
    calls = []
    monkeypatch.setattr(window.player, 'setPosition', calls.append)
    monkeypatch.setattr(window.player, 'play', lambda: None)
    window.on_click(2, appmod.TEXT)
    assert calls == [19500]


def test_current_line_follows_playback(window, tmp_path):
    window.open(make_recording(tmp_path))
    window.on_position(12000)
    assert window.current_row == 1
    window.on_position(500)
    assert window.current_row == 0


def test_incomplete_notes_offer_resume(window, tmp_path):
    window.open(make_recording(tmp_path, complete=False))
    assert window.run_button.text().strip() == 'Resume'
    assert 'stopped at' in window.status.text()


def test_settings_dialog_saves_choice(window, qapp):
    dialog = appmod.SettingsDialog(window)
    dialog.list.setCurrentRow(2)
    dialog.hint.setText('  Dinis,   Maxwell ')
    dialog.accept()
    prefs = appmod.settings()
    assert prefs.value('model') == appmod.models.MODELS[2].id
    assert prefs.value('hint') == 'Dinis, Maxwell'


class FakeJob:
    """Stands in for the worker QProcess: records kill(), never runs."""
    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class Signals(QObject):
            readyReadStandardOutput = Signal()
            finished = Signal(int, object)
        self.signals = Signals()
        self.readyReadStandardOutput = self.signals.readyReadStandardOutput
        self.finished = self.signals.finished
        self.killed = False

    def kill(self):
        self.killed = True

    def waitForFinished(self, _ms=0):
        return True

    def property(self, _name):
        return ''

    def setProperty(self, _name, _value):
        pass

    def readAllStandardOutput(self):
        return b''


@pytest.fixture
def fake_worker(monkeypatch):
    started = []

    def start(_parent, job):
        started.append(job)
        return FakeJob()
    monkeypatch.setattr(appmod, 'worker_process', start)
    monkeypatch.setattr(appmod.models, 'is_downloaded', lambda spec: True)
    monkeypatch.setattr(appmod.speakers, 'is_downloaded', lambda: False)   # tests opt in to speakers
    return started


def recording(folder, name):
    folder.mkdir(exist_ok=True)
    path = folder/name
    path.write_bytes(b'not really audio')
    return path


def test_several_files_are_transcribed_in_turn(window, tmp_path, fake_worker):
    a, b, c = (recording(tmp_path/'rec', n) for n in ('a.mp3', 'b.mp3', 'c.mp3'))
    window.add_files([a, b, c])
    assert window.audio == a.resolve() and len(fake_worker) == 1
    assert window.queue == [b.resolve(), c.resolve()]
    assert 'Up next (2)' in window.queue_label.text()

    window.meta['complete'] = True          # worker reported "done"
    window.on_finished(0, None)
    window.run_queue()                      # normally fired by a zero-delay timer
    assert window.audio == b.resolve() and len(fake_worker) == 2
    assert fake_worker[1]['audio'] == str(b.resolve())


def test_files_dropped_while_transcribing_are_queued(window, tmp_path, fake_worker):
    a, b = (recording(tmp_path/'rec', n) for n in ('a.mp3', 'b.mp3'))
    window.open(a)
    window.start_job()
    window.add_files([b, a])                # a is already running, so only b waits
    assert window.queue == [b.resolve()]
    assert window.audio == a.resolve()


def test_queue_skips_finished_recordings(window, tmp_path, fake_worker):
    done = make_recording(tmp_path)          # hearing.mp3 with complete notes
    new = recording(tmp_path, 'new.mp3')
    window.add_files([done, new])
    assert window.audio == new.resolve() and len(fake_worker) == 1


def test_stop_keeps_the_rest_of_the_queue(window, tmp_path, fake_worker):
    a, b = (recording(tmp_path/'rec', n) for n in ('a.mp3', 'b.mp3'))
    window.add_files([a, b])
    window.stop_job()
    window.on_finished(9, None)
    assert window.queue == [b.resolve()] and len(fake_worker) == 1
    assert 'waiting in the queue' in window.status.text()


def test_new_lines_are_typed_out(window, tmp_path, fake_worker, monkeypatch):
    window.open(recording(tmp_path, 'a.mp3'))
    window.start_job()
    import json
    window.job.readAllStandardOutput = lambda: (json.dumps(
        {'type': 'segment', 'start': 0, 'end': 3, 'text': 'The witness may step down.'}) + '\n').encode()
    window.on_events()
    item = window.table.item(0, appmod.TEXT)
    assert item.text() == '' and item.data(appmod.TYPING_ROLE)
    clock = iter([100.0, 100.1, 100.5])
    monkeypatch.setattr(appmod.time, 'monotonic', lambda: next(clock))
    window.type_tick()                       # starts the line
    window.type_tick()                       # 100 ms of ~280 ms
    assert 0 < len(item.text()) < len('The witness may step down.')
    assert window.segments[0].text == 'The witness may step down.'   # saved text is never partial
    window.type_tick()                       # past 280 ms: done, caret gone
    assert item.text() == 'The witness may step down.' and not item.data(appmod.TYPING_ROLE)


def test_typing_can_be_turned_off(window, tmp_path, fake_worker):
    appmod.settings().setValue('typing', False)
    window.open(recording(tmp_path, 'a.mp3'))
    window.start_job()
    import json
    window.job.readAllStandardOutput = lambda: (json.dumps(
        {'type': 'segment', 'start': 0, 'end': 3, 'text': 'Sustained.'}) + '\n').encode()
    window.on_events()
    assert window.table.item(0, appmod.TEXT).text() == 'Sustained.'


def test_every_theme_builds_a_stylesheet():
    for t in appmod.theme.THEMES.values():
        qss = appmod.theme.stylesheet(t)
        assert '$' not in qss and t.accent in qss


def test_theme_switches_live(window, tmp_path):
    window.open(make_recording(tmp_path))
    window.apply_theme('classic')
    assert appmod.theme.current().key == 'classic'
    assert appmod.theme.CLASSIC.accent in window.styleSheet()
    window.apply_theme('still')
    assert appmod.theme.STILL.accent in window.styleSheet()
    assert window.table.rowCount() == 3


def test_settings_dialog_saves_theme(window):
    dialog = appmod.SettingsDialog(window)
    dialog.theme.setCurrentIndex(dialog.theme.findData('classic'))
    dialog.typing.setChecked(False)
    dialog.accept()
    assert appmod.settings().value('theme') == 'classic'
    assert appmod.settings().value('typing', True, type=bool) is False
    appmod.theme.use(appmod.theme.DEFAULT_THEME)


def test_click_while_transcribing_does_not_play(window, tmp_path, fake_worker, monkeypatch):
    window.open(make_recording(tmp_path, complete=False))
    plays = []
    monkeypatch.setattr(window.player, 'play', lambda: plays.append(1))
    monkeypatch.setattr(window.player, 'setPosition', lambda ms: None)
    window.job = FakeJob()
    window.on_click(1, appmod.TEXT)
    assert plays == []
    window.job = None
    window.on_click(1, appmod.TEXT)
    assert plays == [1]


def test_notes_go_to_the_chosen_folder(window, tmp_path, fake_worker):
    folder = tmp_path/'All notes'
    appmod.settings().setValue('notes_folder', str(folder))
    audio = recording(tmp_path/'rec', 'a.mp3')
    window.open(audio)
    window.start_job()
    assert fake_worker[0]['out'].startswith(str(folder))
    window.segments = [Segment(0, 1, 'Hello.')]
    window.save_now()
    assert len(list(folder.glob('a.*.transcript.json'))) == 1
    window.on_finished(0, None)
    window.open(audio)                                    # reopening finds them again
    assert window.table.rowCount() == 1


def test_recent_recordings_are_remembered(window, tmp_path):
    a = make_recording(tmp_path)
    window.open(a)
    window.fill_recent()
    assert [act.text() for act in window.recent_menu.actions()] == ['hearing.mp3']


def test_downloaded_link_is_transcribed(window, tmp_path, fake_worker):
    window.add_links(['https://example.com/a.mp3'])
    assert fake_worker[0]['mode'] == 'fetch' and fake_worker[0]['url'] == 'https://example.com/a.mp3'
    assert window.queue_row.isVisibleTo(window)
    audio = recording(tmp_path, 'a.mp3')
    window.fetch.property = {'path': str(audio), 'url': 'https://example.com/a.mp3'}.get
    window.on_fetch_finished(0, None)
    assert window.fetch is None
    assert window.audio == audio and fake_worker[1]['mode'] == 'transcribe'


def test_highlighting_a_line_being_typed_keeps_its_text(window, tmp_path, fake_worker):
    """Regression: the playback highlight fired itemChanged and saved the half-typed (empty) text."""
    window.open(recording(tmp_path, 'a.mp3'))
    window.start_job()
    import json
    window.job.readAllStandardOutput = lambda: (json.dumps(
        {'type': 'segment', 'start': 0, 'end': 3, 'text': 'Next, in case 25-581.'}) + '\n').encode()
    window.on_events()
    window.mark_current(0)                     # playback reaches the line while it is still blank
    assert window.segments[0].text == 'Next, in case 25-581.'
    assert window.typing                       # still typing, not stuck
    window.stop_typing()
    assert window.table.item(0, appmod.TEXT).text() == 'Next, in case 25-581.'


def test_blank_lines_are_restored_from_the_worker_file(window, tmp_path):
    audio = make_recording(tmp_path)
    window.open(audio)
    window.segments[1].text = ''
    window.restore_blank_lines(sidecar(audio))
    assert window.segments[1].text == 'Objection, Your Honor, hearsay.'
    assert window.table.item(1, appmod.TEXT).text() == 'Objection, Your Honor, hearsay.'


def split_recording(tmp_path):
    audio = tmp_path/'split.mp3'
    audio.write_bytes(b'not really audio')
    segs = [Segment(0, 3, 'The state has denied families the ability to use funding at Catholic'),
            Segment(3, 6, 'schools.'),
            Segment(9, 12, 'Thank you, counsel.')]
    save(sidecar(audio), audio, segs, model='test', complete=True, duration=12)
    return audio


def test_switching_views_keeps_stars_and_notes(window, tmp_path):
    audio = split_recording(tmp_path)
    appmod.settings().setValue('grouping', 'sentence')
    window.open(audio)
    assert window.table.rowCount() == 2
    assert window.table.item(0, appmod.TEXT).text().endswith('at Catholic schools.')
    window.toggle_star(0)                                    # stars both lines of the sentence
    window.table.item(0, appmod.NOTE).setText('key admission')
    window.save_now()
    appmod.settings().setValue('grouping', 'line')
    window.rebuild_table()
    assert window.table.rowCount() == 3
    assert [s.starred for s in window.segments] == [True, True, False]
    assert window.segments[0].note == 'key admission'
    window.on_position(4000)                                 # 4 s is inside line 2
    assert window.current_row == 1


def test_grouped_text_is_read_only(window, tmp_path):
    appmod.settings().setValue('grouping', 'sentence')
    window.open(split_recording(tmp_path))
    assert not window.table.item(0, appmod.TEXT).flags() & appmod.Qt.ItemFlag.ItemIsEditable


def test_live_lines_join_the_open_sentence(window, tmp_path, fake_worker):
    appmod.settings().setValue('grouping', 'sentence')
    appmod.settings().setValue('typing', False)
    window.open(recording(tmp_path, 'a.mp3'))
    window.start_job()
    window.add_segment(Segment(0, 3, 'funding at Catholic'))
    window.add_segment(Segment(3, 5, 'schools.'))
    window.add_segment(Segment(5, 7, 'Thank you.'))
    assert window.table.rowCount() == 2
    assert window.table.item(0, appmod.TEXT).text() == 'funding at Catholic schools.'
    assert window.starts == [0, 5]


def test_summary_window_shows_saved_summary_with_clickable_times(window, tmp_path, monkeypatch):
    from voice_notes.summarize import Section, save_summary, summary_path
    audio = make_recording(tmp_path)
    window.open(audio)
    save_summary(summary_path(sidecar(audio)), model='Qwen3 4B', lines=3,
                 overview='Overview\nCounsel objected. [00:10]\nKey arguments\n- Hearsay. [00:10]',
                 sections=[Section(0, 25, '- Objection to hearsay.')])
    window.show_summary()
    s = window.summary
    html = s.view.toHtml()
    assert 'Counsel objected' in html and 't:10' in html and 'Objection to hearsay' in html
    assert 'transcript changed' not in s.meta.text()
    seeks = []
    monkeypatch.setattr(window.player, 'setPosition', seeks.append)
    monkeypatch.setattr(window.player, 'play', lambda: None)
    s.play_from(appmod.QUrl('t:10'))
    assert seeks == [9500]
    assert 'By section' in s.plain_text() and '[00:00–00:25]' in s.plain_text()
    s.close()


def test_summarize_starts_offline_worker(window, tmp_path, fake_worker, monkeypatch):
    window.open(make_recording(tmp_path))
    window.show_summary()
    window.summary.start()
    job = fake_worker[-1]
    assert job['mode'] == 'summarize' and job['out'].endswith('hearing.summary.json')
    assert job['model'] == appmod.models.DEFAULT_SUMMARY_MODEL
    window.summary.proc = None
    window.summary.close()


def speaker_recording(tmp_path):
    audio = tmp_path/'arg.mp3'
    audio.write_bytes(b'not really audio')
    segs = [Segment(0, 3, 'Mr. Chief Justice, and may it please the Court.', speaker='S1'),
            Segment(3, 6, 'The rule is clear.', speaker='S1'),
            Segment(6, 8, 'Is it, counsel?', speaker='S2'),
            Segment(8, 11, 'Yes, Your Honor.', speaker='S1')]
    save(sidecar(audio), audio, segs, model='test', complete=True, duration=11)
    return audio


def test_speakers_split_paragraphs_and_can_be_renamed(window, tmp_path, monkeypatch):
    audio = speaker_recording(tmp_path)
    window.open(audio)
    assert not window.table.isColumnHidden(appmod.SPEAKER)
    assert window.table.rowCount() == 3                        # paragraphs break where the speaker changes
    assert [window.table.item(r, appmod.SPEAKER).text() for r in range(3)] == ['Speaker 1', 'Speaker 2', 'Speaker 1']
    monkeypatch.setattr(appmod.QInputDialog, 'getText', lambda *a, **k: ('Justice Kagan', True))
    window.on_double_click(1, appmod.SPEAKER)
    assert window.table.item(1, appmod.SPEAKER).text() == 'Justice Kagan'
    window.save_now()
    assert load(sidecar(audio))['speakers'] == {'S2': 'Justice Kagan'}
    window.search.setText('kagan')                             # search finds speaker names
    assert [window.table.isRowHidden(r) for r in range(3)] == [True, False, True]


def test_change_who_said_a_line(window, tmp_path):
    window.open(speaker_recording(tmp_path))
    window.set_speaker(1, 'S1')                                # it was counsel after all
    assert window.table.rowCount() == 1
    assert {s.speaker for s in window.segments} == {'S1'}


def test_transcripts_without_speakers_hide_the_column(window, tmp_path):
    window.open(make_recording(tmp_path))
    assert window.table.isColumnHidden(appmod.SPEAKER)


def test_finished_transcription_queues_speaker_identification(window, tmp_path, fake_worker, monkeypatch):
    monkeypatch.setattr(appmod.speakers, 'is_downloaded', lambda: True)
    appmod.settings().setValue('speakers', True)
    audio = recording(tmp_path, 'a.mp3')
    window.open(audio)
    window.start_job()
    window.segments = [Segment(0, 2, 'Hello.')]
    window.meta['complete'] = True
    window.on_finished(0, None)
    job = fake_worker[-1]
    assert job['mode'] == 'speakers' and job['audio'] == str(audio.resolve())
    import json
    window.diar.property = {'labels': json.dumps([[0, 2, 'S1']]), 'count': 1, 'notes': str(window.notes_file),
                            'name': 'a.mp3'}.get
    window.on_speakers_finished(0, None)
    assert window.segments[0].speaker == 'S1'
    assert load(window.notes_file)['segments'][0].speaker == 'S1'


def test_speaker_identification_is_off_by_default(window, tmp_path, fake_worker, monkeypatch):
    monkeypatch.setattr(appmod.speakers, 'is_downloaded', lambda: True)
    window.open(recording(tmp_path, 'a.mp3'))
    window.start_job()
    window.segments = [Segment(0, 2, 'Hello.')]
    window.meta['complete'] = True
    window.on_finished(0, None)
    assert all(job['mode'] != 'speakers' for job in fake_worker)


def test_export_names_speakers(window, tmp_path):
    from voice_notes.transcript import to_text
    window.open(speaker_recording(tmp_path))
    window.names()['S2'] = 'Justice Kagan'
    text = to_text('arg.mp3', window.segments, groups=window.groups, speakers=window.names())
    assert '[00:06] Justice Kagan: Is it, counsel?' in text
    assert '[00:00] Speaker 1: Mr. Chief Justice' in text


def test_quick_view_switch_keeps_the_selected_line(window, tmp_path):
    window.open(speaker_recording(tmp_path))
    assert window.table.rowCount() == 3                    # paragraphs
    window.table.selectRow(2)                              # "Yes, Your Honor." (line 4)
    window.view_mode.setCurrentIndex(window.view_mode.findData('line'))
    assert window.table.rowCount() == 4                    # original lines in time order
    assert window.table.currentRow() == 3
    assert appmod.settings().value('grouping') == 'line'
