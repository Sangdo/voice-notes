"""Main window: open a recording, transcribe it locally, read the notes, click a line to listen from there."""
import bisect
import html
import json
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

from PySide6.QtCore import QDir, QEvent, QProcess, QSettings, QSize, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QAbstractTextDocumentLayout, QAction, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPalette, QShortcut, QTextDocument
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QInputDialog,
                               QMenu,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QSlider, QStackedWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from . import models, theme
from .engine import apple_gpu
from .summarize import load_summary, summary_path
from .icons import icon
from . import speakers
from .transcript import (DEFAULT_GROUPING, GROUPINGS, Segment, clock, find_notes, fingerprint, group_segments, load,
                         save, speaker_name, starts_group, to_text)

AUDIO_TYPES = ('.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg', '.opus', '.wma', '.mp4', '.mov', '.m4v', '.webm')
STAR, TIME, SPEAKER, TEXT, NOTE = range(5)
LABEL_ROLE = Qt.ItemDataRole.UserRole + 2   # speaker label ('S3') on a Speaker cell
TYPING_ROLE = Qt.ItemDataRole.UserRole + 1   # set on a transcript cell while its text is being typed out
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
OFFLINE_TEXT = 'Offline'
# Summaries run on MLX, which exists only on Apple Silicon Macs.
SUMMARY_AVAILABLE = apple_gpu()
MAC = sys.platform == 'darwin'
THIS_COMPUTER = 'this Mac' if MAC else 'this computer'


def load_fonts():
    """Register the bundled fonts once (Source Sans for the transcript)."""
    if not getattr(load_fonts, 'done', False):
        for path in sorted((Path(__file__).parent/'fonts').glob('*.ttf')):
            QFontDatabase.addApplicationFont(str(path.resolve()))
        load_fonts.done = True


def lines(n):
    return f'{n:,} line' + ('' if n == 1 else 's')


def settings():
    # INI in the user config folder (tests redirect it with QSettings.setPath).
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, 'VoiceNotes', 'VoiceNotes')


def app_data():
    path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    path.mkdir(parents=True, exist_ok=True)
    return path


def size_text(n):
    return f'{n/1e9:.2f} GB' if n >= 1e9 else f'{n/1e6:.0f} MB'


def worker_process(parent, job):
    """Start `python -m voice_notes.worker` on a job file inside its own temporary folder."""
    work = app_data()/'jobs'/uuid.uuid4().hex
    work.mkdir(parents=True)
    job = {**job, 'workdir': str(work)}
    (work/'job.json').write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    proc = QProcess(parent)
    proc.setWorkingDirectory(str(PACKAGE_ROOT))
    proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
    proc.setProperty('workdir', str(work))
    if getattr(sys, 'frozen', False):
        # Packaged app: the executable runs the worker itself (see packaging/entry.py).
        proc.start(sys.executable, ['--worker', str(work/'job.json')])
    else:
        proc.start(sys.executable, ['-m', 'voice_notes.worker', str(work/'job.json')])
    return proc


def read_events(proc):
    buffer = (proc.property('buffer') or '') + bytes(proc.readAllStandardOutput()).decode('utf-8', 'replace')
    *lines, rest = buffer.split('\n')
    proc.setProperty('buffer', rest)
    for line in lines:
        try:
            yield json.loads(line)
        except ValueError:
            continue


def downloads_folder():
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or Path.home()/'Downloads')


def cleanup(proc):
    shutil.rmtree(proc.property('workdir') or '', ignore_errors=True)


class HighlightDelegate(QStyledItemDelegate):
    """Draws wrapped text with search hits marked."""

    def __init__(self, parent):
        super().__init__(parent)
        self.pattern = None

    def document(self, option, index):
        doc = QTextDocument()
        font = QFont(option.font)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, theme.current().transcript_spacing)
        doc.setDefaultFont(font)
        doc.setDocumentMargin(3)
        t = theme.current()
        text = html.escape(index.data() or '')
        if self.pattern:
            text = self.pattern.sub(lambda m: f'<span style="background:{t.match};color:{t.on_accent}">{m.group(0)}</span>',
                                    text)
        if index.data(TYPING_ROLE):
            text += f'<span style="color:{t.accent}">▍</span>'
        doc.setHtml(f'<div style="color:{t.text}">{text}</div>')
        doc.setTextWidth(max(50, option.rect.width()))
        return doc

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ''
        opt.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        doc = self.document(option, index)
        painter.save()
        painter.setClipRect(option.rect)
        painter.translate(option.rect.topLeft())
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(QPalette.ColorRole.Text, QColor(theme.current().text))
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index):
        view = self.parent()
        opt = QStyleOptionViewItem(option)
        opt.rect.setWidth(view.columnWidth(index.column()))
        doc = self.document(opt, index)
        return QSize(int(doc.idealWidth()), int(doc.size().height()))


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.setMinimumWidth(460)
        self.download = None
        prefs = settings()
        layout = QVBoxLayout(self)

        title = QLabel('Model')
        title.setObjectName('fileTitle')
        layout.addWidget(title)
        self.list = QListWidget()
        self.list.setObjectName('models')
        self.list.setMinimumHeight(280)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.list)
        current = prefs.value('model', models.DEFAULT_MODEL)
        for spec in models.MODELS:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, spec.id)
            self.list.addItem(item)
            if spec.id == current:
                self.list.setCurrentItem(item)
        self.refresh()
        if not self.list.currentItem():
            self.list.setCurrentRow(0)

        row = QHBoxLayout()
        self.download_button = QPushButton('Download')
        self.download_button.setIcon(icon('export', theme.current().text, 16))
        self.download_button.clicked.connect(self.start_download)
        self.download_status = QLabel()
        self.download_status.setObjectName('meta')
        row.addWidget(self.download_button)
        row.addWidget(self.download_status, 1)
        layout.addLayout(row)
        self.download_bar = QProgressBar()
        self.download_bar.setRange(0, 1000)
        self.download_bar.setTextVisible(False)
        self.download_bar.setFixedHeight(6)
        self.download_bar.hide()
        layout.addWidget(self.download_bar)
        self.list.currentItemChanged.connect(lambda *_: self.update_buttons())

        form = QFormLayout()
        self.device = QComboBox()
        self.device.setView(QListView(self.device))
        for key, label in models.DEVICES:
            self.device.addItem(label, key)
        self.device.setCurrentIndex(max(0, self.device.findData(prefs.value('device', models.DEFAULT_DEVICE))))
        self.hint = QLineEdit(prefs.value('hint', ''))
        self.hint.setPlaceholderText('Dinis, Maxwell, …')
        self.hint.setToolTip('Names and terms that help recognition')
        self.theme = QComboBox()
        self.theme.setView(QListView(self.theme))
        for t in theme.THEMES.values():
            self.theme.addItem(t.label, t.key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(theme.current().key)))
        self.typing = QCheckBox('Typing effect')
        self.typing.setChecked(prefs.value('typing', True, type=bool))
        form.addRow('Device', self.device)
        form.addRow('Terms', self.hint)
        self.notes_folder = prefs.value('notes_folder', '')
        folder_row = QHBoxLayout()
        self.folder_label = QLabel()
        self.folder_label.setObjectName('meta')
        choose = QPushButton('Choose…')
        choose.clicked.connect(self.choose_folder)
        self.folder_reset = QPushButton('Reset')
        self.folder_reset.setObjectName('ghost')
        self.folder_reset.clicked.connect(lambda: self.set_folder(''))
        folder_row.addWidget(self.folder_label, 1)
        folder_row.addWidget(choose)
        folder_row.addWidget(self.folder_reset)
        self.set_folder(self.notes_folder)
        self.grouping = QComboBox()
        self.grouping.setView(QListView(self.grouping))
        for key, label in GROUPINGS:
            self.grouping.addItem(label, key)
        self.grouping.setCurrentIndex(max(0, self.grouping.findData(prefs.value('grouping', DEFAULT_GROUPING))))
        self.grouping.setToolTip('How lines are grouped on screen. Saved notes keep every line.')
        self.summary_model = QComboBox()
        self.summary_model.setView(QListView(self.summary_model))
        for spec in models.SUMMARY_MODELS:
            ready = '' if models.is_downloaded(spec) else f' · {spec.size}'
            self.summary_model.addItem(f'{spec.label} — {spec.note}{ready}', spec.id)
        self.summary_model.setCurrentIndex(max(0, self.summary_model.findData(
            prefs.value('summary_model', models.DEFAULT_SUMMARY_MODEL))))
        form.addRow('Show', self.grouping)
        if SUMMARY_AVAILABLE:
            form.addRow('Summary', self.summary_model)
        else:
            self.summary_model.hide()
        speaker_row = QHBoxLayout()
        self.auto_speakers = QCheckBox('Identify after transcribing')
        self.auto_speakers.setChecked(prefs.value('speakers', False, type=bool))
        self.speaker_download = QPushButton(f'Download ({speakers.SIZE})')
        self.speaker_download.clicked.connect(self.download_speakers)
        self.speaker_download.setVisible(not speakers.is_downloaded())
        speaker_row.addWidget(self.auto_speakers, 1)
        speaker_row.addWidget(self.speaker_download)
        form.addRow('Speakers', speaker_row)
        form.addRow('Notes', folder_row)
        form.addRow('Theme', self.theme)
        form.addRow('', self.typing)
        layout.addLayout(form)

        lock = QLabel('🔒 Offline. Internet is used only to download models.')
        lock.setWordWrap(True)
        lock.setObjectName('offline')
        layout.addWidget(lock)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Save')
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName('primary')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Cancel')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_buttons()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def set_folder(self, folder):
        self.notes_folder = folder
        self.folder_label.setText(QDir.toNativeSeparators(folder).replace(str(Path.home()), '~') if folder
                                  else 'Next to recording')
        self.folder_label.setToolTip(folder)
        self.folder_reset.setVisible(bool(folder))

    def choose_folder(self):
        start = self.notes_folder or str(Path.home()/'Documents')
        folder = QFileDialog.getExistingDirectory(self, 'Notes folder', start)
        if folder:
            self.set_folder(folder)

    def refresh(self):
        width = 400
        t = theme.current()
        for i in range(self.list.count()):
            item = self.list.item(i)
            spec = models.get(item.data(Qt.ItemDataRole.UserRole))
            ready = models.is_downloaded(spec)
            state = (f'<span style="color:{t.ok}">✓</span>' if ready
                     else f'<span style="color:{t.muted}">{spec.size}</span>')
            label = QLabel(f'<b>{html.escape(spec.label)}</b>&nbsp;&nbsp;&nbsp;{state}<br>'
                           f'<span style="color:{t.text_soft}">{html.escape(spec.note)}</span>')
            label.setContentsMargins(10, 0, 10, 0)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            label.setStyleSheet('background: transparent;')
            item.setSizeHint(QSize(width, label.fontMetrics().lineSpacing()*2 + 22))
            self.list.setItemWidget(item, label)

    def download_speakers(self):
        if self.download:
            return
        self.download_status.setText('Downloading speaker models…')
        self.show_busy()
        self.speaker_download.setEnabled(False)
        self.download = worker_process(self, {'mode': 'download', 'model': 'speakers'})
        self.download.readyReadStandardOutput.connect(self.download_events)
        self.download.finished.connect(self.download_finished)
        self.update_buttons()

    def selected(self):
        item = self.list.currentItem()
        return models.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def update_buttons(self):
        spec = self.selected()
        busy = self.download is not None
        self.download_button.setEnabled(bool(spec) and not busy and not models.is_downloaded(spec))

    def start_download(self):
        spec = self.selected()
        answer = QMessageBox.question(
            self, 'Download model',
            f'Download {spec.label} ({spec.size})?\n\nOnly the model is downloaded.')
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.download_status.setText('Connecting…')
        self.show_busy()
        self.download = worker_process(self, {'mode': 'download', 'model': spec.id})
        self.download.readyReadStandardOutput.connect(self.download_events)
        self.download.finished.connect(self.download_finished)
        self.update_buttons()

    def show_busy(self):
        self.download_bar.setRange(0, 0)   # moving bar until the size is known
        self.download_bar.show()

    def download_events(self):
        for event in read_events(self.download):
            if event['type'] == 'progress':
                if event['value'] < 0:
                    self.download_bar.setRange(0, 0)
                    self.download_status.setText(event['stage'] + '…')
                else:
                    self.download_bar.setRange(0, 1000)
                    self.download_bar.setValue(int(event['value']*1000))
                    self.download_status.setText(f"{event['stage']} · {size_text(event.get('bytes', 0))} of "
                                                 f"{size_text(event.get('total', 0))} · {event['value']:.0%}")
            elif event['type'] == 'error':
                self.download_status.setText('Download failed: ' + event['message'][:200])

    def download_finished(self, code, _status):
        proc, self.download = self.download, None
        cleanup(proc)
        self.download_bar.hide()
        self.speaker_download.setVisible(not speakers.is_downloaded())
        self.speaker_download.setEnabled(True)
        if code == 0:
            self.download_status.setText('Done')
        elif not self.download_status.text().startswith('Download failed'):
            self.download_status.setText('Download stopped.')
        self.refresh()
        self.update_buttons()

    def reject(self):
        if self.download:
            self.download.kill()
            self.download.waitForFinished(2000)
        super().reject()

    def accept(self):
        if self.download:
            QMessageBox.information(self, 'Downloading', 'Wait for the download to finish.')
            return
        prefs = settings()
        prefs.setValue('model', self.selected().id)
        prefs.setValue('device', self.device.currentData())
        prefs.setValue('hint', ' '.join(self.hint.text().split())[:300])
        prefs.setValue('theme', self.theme.currentData())
        prefs.setValue('notes_folder', self.notes_folder)
        prefs.setValue('grouping', self.grouping.currentData())
        prefs.setValue('summary_model', self.summary_model.currentData())
        prefs.setValue('speakers', self.auto_speakers.isChecked())
        prefs.setValue('typing', self.typing.isChecked())
        super().accept()


HEADINGS = ('Overview', 'Key arguments', 'Questions from the bench', 'Notable concessions or statements')
TIME_REF = re.compile(r'\[(\d{1,2}(?::\d{2}){1,2})\]')


def seconds(stamp):
    total = 0
    for part in stamp.split(':'):
        total = total*60 + int(part)
    return total


class SummaryWindow(QDialog):
    """Offline summary of the open recording: an overview plus one summary per ~8 minutes, each with a time
    that plays the recording from there, so every point can be checked against the audio."""

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.proc = None
        self.download = None
        self.live = []           # sections arriving while a summary is being written
        self.data = None
        self.setWindowTitle('Summary')
        self.resize(760, 820)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        names = QVBoxLayout()
        self.title = QLabel('Summary')
        self.title.setObjectName('fileTitle')
        self.meta = QLabel()
        self.meta.setObjectName('meta')
        names.addWidget(self.title)
        names.addWidget(self.meta)
        top.addLayout(names, 1)
        self.copy_button = QPushButton('Copy')
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.plain_text()))
        self.export_button = QPushButton('Export')
        self.export_button.clicked.connect(self.export)
        self.run_button = QPushButton(' Summarize')
        self.run_button.setObjectName('primary')
        self.run_button.clicked.connect(self.toggle)
        for b in (self.copy_button, self.export_button, self.run_button):
            top.addWidget(b)
        layout.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.status = QLabel()
        self.status.setObjectName('terminalStatus')
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        self.view = QTextBrowser()
        self.view.setObjectName('summary')
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self.play_from)
        layout.addWidget(self.view, 1)
        note = QLabel('🔒 Made offline on ' + THIS_COMPUTER + '. Click a time to check it against the recording.')
        note.setObjectName('offline')
        layout.addWidget(note)
        self.refresh()

    # ----- state
    def path(self):
        return summary_path(self.main.notes_file) if self.main.notes_file else None

    def refresh(self):
        self.title.setText(f'Summary · {self.main.audio.name}' if self.main.audio else 'Summary')
        self.data = None
        path = self.path()
        if path and path.is_file():
            try:
                self.data = load_summary(path)
            except (OSError, ValueError, TypeError, KeyError) as e:
                self.status.setText(f'Could not read the saved summary: {e}')
        self.render()
        self.update_controls()

    def update_controls(self):
        running = self.proc is not None or self.download is not None
        self.run_button.setText(' Stop' if running else (' Summarize again' if self.data else ' Summarize'))
        self.run_button.setObjectName('ghost' if running else 'primary')
        self.run_button.style().unpolish(self.run_button)
        self.run_button.style().polish(self.run_button)
        self.run_button.setEnabled(running or bool(self.main.segments))
        self.copy_button.setEnabled(bool(self.data) and not running)
        self.export_button.setEnabled(bool(self.data) and not running)
        self.progress.setVisible(running)
        if self.data and not running:
            lines_now = len(self.main.segments)
            stale = ' · transcript changed since' if self.data.get('lines') != lines_now else ''
            self.meta.setText(f"{self.data.get('model', '')} · {self.data.get('created', '')}{stale}")
        elif not running:
            self.meta.setText('Not summarized yet' if self.main.segments else 'Transcribe first')

    # ----- display
    def linked(self, text):
        return TIME_REF.sub(lambda m: f'[[{m.group(1)}]](t:{seconds(m.group(1))})', text)

    def markdown(self, overview, sections):
        out = []
        for line in overview.splitlines():
            bare = line.strip().strip('#*: ').strip()
            out.append(f'### {bare}' if bare in HEADINGS else self.linked(line))
        if sections:
            out += ['', '---', '', '### By section', '']
            for sec in sections:
                out += [f'**[{clock(sec.start)}](t:{int(sec.start)}) – {clock(sec.end)}**', '', self.linked(sec.text), '']
        return '\n'.join(out)

    def render(self):
        t = theme.current()
        palette = self.view.palette()
        palette.setColor(QPalette.ColorRole.Link, QColor(t.accent))   # times are links in the theme accent
        self.view.setPalette(palette)
        self.view.document().setDefaultStyleSheet(f'a {{ color: {t.accent}; text-decoration: none; }}')
        if self.proc:
            text = self.markdown('', self.live) if self.live else ''
        elif self.data:
            text = self.markdown(self.data['overview'], self.data['sections'])
        else:
            text = ('Summarize writes an overview and a short summary of every ~8 minutes, on this Mac, '
                    'with times you can click to listen.')
        self.view.setMarkdown(text)

    def plain_text(self):
        if not self.data:
            return ''
        out = [self.main.audio.name if self.main.audio else '', '', self.data['overview'].strip(), '', 'By section', '']
        for sec in self.data['sections']:
            out += [f'[{clock(sec.start)}–{clock(sec.end)}]', sec.text.strip(), '']
        return '\n'.join(out).strip() + '\n'

    def play_from(self, url):
        if url.scheme() == 't':
            self.main.player.setPosition(max(0, int(url.path())*1000 - 500))
            if not self.main.job:
                self.main.player.play()

    def export(self):
        default = self.path().with_suffix('.txt')
        path, _ = QFileDialog.getSaveFileName(self, 'Export summary', str(default), 'Text (*.txt)')
        if path:
            Path(path).write_text(self.plain_text(), encoding='utf-8')
            self.status.setText(f'Exported: {path}')

    # ----- running
    def toggle(self):
        if self.proc or self.download:
            for proc in (self.proc, self.download):
                if proc:
                    proc.kill()
            return
        self.start()

    def start(self):
        spec = models.get(settings().value('summary_model', models.DEFAULT_SUMMARY_MODEL))
        if not models.is_downloaded(spec):
            answer = QMessageBox.question(self, 'Download model',
                                          f'Summaries need {spec.label} ({spec.size}), downloaded once.\n\n'
                                          'Only the model is downloaded. Notes stay on this Mac.')
            if answer == QMessageBox.StandardButton.Yes:
                self.start_download(spec)
            return
        if not self.main.meta.get('complete'):
            answer = QMessageBox.question(self, 'Transcript not finished',
                                          'The transcript is not finished. Summarize what is there so far?')
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.main.save_now()
        self.live = []
        self.proc = worker_process(self, {'mode': 'summarize', 'notes': str(self.main.notes_file),
                                          'out': str(self.path()), 'model': spec.id})
        self.proc.readyReadStandardOutput.connect(self.on_events)
        self.proc.finished.connect(self.on_finished)
        self.error = ''
        self.started = time.monotonic()
        self.progress.setRange(0, 0)
        self.status.setText('Getting ready…')
        self.render()
        self.update_controls()

    def on_events(self):
        for event in read_events(self.proc):
            kind = event['type']
            if kind == 'progress':
                if event['value'] < 0:
                    self.progress.setRange(0, 0)
                else:
                    self.progress.setRange(0, 1000)
                    self.progress.setValue(int(event['value']*1000))
                self.status.setText(event['stage'] + (f" · {event['value']*100:.0f}%" if event['value'] >= 0 else ''))
            elif kind == 'section':
                from .summarize import Section
                self.live.append(Section(event['start'], event['end'], event['text']))
                self.render()
            elif kind == 'error':
                self.error = event['message']

    def on_finished(self, code, _status):
        proc = self.proc
        self.on_events()
        self.proc = None
        cleanup(proc)
        minutes = (time.monotonic() - self.started)/60
        if code == 0:
            self.status.setText(f'Done · took {minutes:.1f} min')
        elif self.error:
            self.status.setText('Summary failed: ' + self.error[:300])
        else:
            self.status.setText('Stopped.')
        self.refresh()

    def start_download(self, spec):
        self.status.setText(f'Downloading {spec.label}…')
        self.progress.setRange(0, 0)
        self.download = worker_process(self, {'mode': 'download', 'model': spec.id})
        self.download.finished.connect(lambda code, _s: self.download_finished(code))
        self.update_controls()

    def download_finished(self, code):
        proc, self.download = self.download, None
        cleanup(proc)
        self.update_controls()
        if code == 0:
            self.start()
        else:
            self.status.setText('Download stopped.')

    def stop_all(self):
        for proc in (self.proc, self.download):
            if proc:
                proc.kill()
                proc.waitForFinished(2000)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        load_fonts()
        self.setWindowTitle('Voice Notes')
        self.resize(1100, 760)
        self.setMinimumSize(720, 480)
        self.setAcceptDrops(True)
        self.audio = None
        self.notes_file = None   # where this recording's notes are read from and saved to
        self.segments = []
        self.groups = []         # rows on screen: each a list of segment indexes (a line, sentence or paragraph)
        self.starts = []
        self.meta = {}
        self.job = None
        self.job_started = 0.0
        self.current_row = -1
        self.loading = False
        self.queue = []          # recordings waiting to be transcribed, in order
        self.batch_log = []      # (name, result) for the current run of the queue
        self.job_auto = False
        self.job_stopped = False
        self.summary = None      # SummaryWindow, created on first use
        self.diar = None         # worker labelling speakers
        self.diar_queue = []     # (audio, notes) waiting for speaker identification
        self.diar_status = ''
        self.fetch = None        # worker downloading a recording link
        self.fetch_status = ''
        self.links = []          # links waiting to be downloaded
        self.themed_icons = []   # (widget, icon name, Theme colour field, size): redrawn when the theme changes
        theme.use(settings().value('theme', theme.DEFAULT_THEME))

        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.playbackStateChanged.connect(self.on_state)

        self.build()
        QApplication.instance().setStyle('Fusion')
        self.save_timer = QTimer(self, singleShot=True, interval=600, timeout=self.save_now)
        # Typing effect: new lines from the transcriber are revealed a few characters at a time.
        self.typing = []         # [row, started] still being typed, oldest first
        self.type_timer = QTimer(self, interval=16, timeout=self.type_tick)
        self.apply_theme()

    # ---------- layout ----------
    def build(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 14)
        outer.setSpacing(10)

        top = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(2)
        self.title = QLabel('Voice Notes')
        self.title.setObjectName('fileTitle')
        self.info = QLabel(OFFLINE_TEXT)
        self.info.setObjectName('offline')
        names.addWidget(self.title)
        names.addWidget(self.info)
        top.addLayout(names, 1)
        self.open_button = QPushButton(' Open')
        self.set_icon(self.open_button, 'folder', 'text', 18)
        self.open_button.clicked.connect(self.choose)
        self.run_button = QPushButton(' Transcribe')
        self.run_button.setObjectName('primary')
        self.set_icon(self.run_button, 'sparkle', 'on_accent', 16)
        self.run_button.clicked.connect(self.toggle_job)
        self.summary_button = QPushButton(' Summary')
        self.summary_button.clicked.connect(self.show_summary)
        self.export_button = QPushButton(' Export')
        self.set_icon(self.export_button, 'export', 'text', 16)
        self.set_icon(self.summary_button, 'list', 'text', 16)
        self.export_button.clicked.connect(self.export)
        self.settings_button = QPushButton()
        self.settings_button.setObjectName('iconButton')
        self.set_icon(self.settings_button, 'gear', 'text', 18)
        self.settings_button.setToolTip('Settings')
        self.settings_button.clicked.connect(self.show_settings)
        for b in (self.open_button, self.summary_button, self.export_button, self.run_button, self.settings_button):
            top.addWidget(b)
        self.summary_button.setVisible(SUMMARY_AVAILABLE)
        outer.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.status = QLabel()
        self.status.setObjectName('terminalStatus')
        outer.addWidget(self.progress)
        outer.addWidget(self.status)
        self.queue_row = QWidget()
        self.queue_row.setObjectName('queueRow')
        queue_layout = QHBoxLayout(self.queue_row)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_label = QLabel()
        self.queue_label.setObjectName('meta')
        clear = QPushButton('Clear')
        clear.setObjectName('ghost')
        clear.clicked.connect(self.clear_queue)
        queue_layout.addWidget(self.queue_label, 1)
        queue_layout.addWidget(clear)
        self.queue_row.hide()
        outer.addWidget(self.queue_row)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName('search')
        self.search.setPlaceholderText('Search   ⌘F')
        self.set_icon(self.search.addAction(QIcon(), QLineEdit.ActionPosition.LeadingPosition), 'search', 'muted', 16)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        self.search.returnPressed.connect(self.next_match)
        self.starred_only = QCheckBox('★ only')
        self.starred_only.toggled.connect(self.apply_filter)
        self.follow = QCheckBox('Follow')
        self.follow.setChecked(True)
        self.count = QLabel()
        self.count.setObjectName('meta')
        # Quick switch between grouped reading and the original time-ordered lines (same as Settings → Show).
        self.view_mode = QComboBox()
        self.view_mode.setView(QListView(self.view_mode))
        for key, label in GROUPINGS:
            self.view_mode.addItem(label, key)
        self.view_mode.setCurrentIndex(max(0, self.view_mode.findData(self.grouping())))
        self.view_mode.setToolTip('Show as paragraphs, sentences, or the original lines in time order')
        self.view_mode.currentIndexChanged.connect(self.change_view)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.view_mode)
        search_row.addWidget(self.count)
        search_row.addWidget(self.starred_only)
        search_row.addWidget(self.follow)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['★', 'Time', 'Speaker', 'Transcript', 'Note'])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                   QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(STAR, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(TIME, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(SPEAKER, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(TEXT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(NOTE, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(STAR, 36)
        self.table.setColumnWidth(TIME, 76)
        self.table.setColumnWidth(SPEAKER, 130)
        self.table.setColumnHidden(SPEAKER, True)
        self.table.cellDoubleClicked.connect(self.on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.speaker_menu)
        self.table.setColumnWidth(NOTE, 200)
        self.delegate = HighlightDelegate(self.table)
        self.table.setItemDelegateForColumn(TEXT, self.delegate)
        self.relayout_timer = QTimer(self, singleShot=True, interval=60, timeout=self.relayout_rows)
        header.sectionResized.connect(lambda *_: self.relayout_timer.start())
        self.table.viewport().installEventFilter(self)
        self.table.cellClicked.connect(self.on_click)
        self.table.itemChanged.connect(self.on_edit)

        notes = QWidget()
        notes_layout = QVBoxLayout(notes)
        notes_layout.setContentsMargins(0, 0, 0, 0)
        notes_layout.addLayout(search_row)
        notes_layout.addWidget(self.table, 1)

        empty = QFrame()
        empty.setObjectName('emptyState')
        empty_layout = QVBoxLayout(empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        big = QLabel('Drop recordings here')
        big.setObjectName('title')
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        small = QLabel('MP3 · M4A · WAV · transcribed offline')
        small.setObjectName('meta')
        small.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pick = QPushButton(' Open')
        pick.setObjectName('primary')
        self.set_icon(pick, 'folder', 'on_accent', 16)
        pick.clicked.connect(self.choose)
        empty_layout.addWidget(big)
        empty_layout.addWidget(small)
        empty_layout.addSpacing(12)
        empty_layout.addWidget(pick, 0, Qt.AlignmentFlag.AlignCenter)

        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(notes)
        outer.addWidget(self.stack, 1)

        bar = QFrame()
        bar.setObjectName('playerBar')
        player = QHBoxLayout(bar)
        player.setContentsMargins(10, 6, 12, 6)
        self.back = self.icon_button('back10', '−10 s (←)', lambda: self.skip(-10))
        self.play = self.icon_button('play', 'Play (Space)', self.toggle_play)
        self.forward = self.icon_button('forward10', '+10 s (→)', lambda: self.skip(10))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.slider.sliderPressed.connect(lambda: self.player.setPosition(self.slider.value()))
        self.clock = QLabel('00:00 / 00:00')
        self.clock.setObjectName('time')
        self.clock.setMinimumWidth(self.clock.fontMetrics().horizontalAdvance('0:00:00 / 0:00:00') + 24)
        self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speed = QComboBox()
        self.speed.setView(QListView(self.speed))
        for rate in (0.75, 1.0, 1.25, 1.5, 1.75, 2.0):
            self.speed.addItem(f'{rate:g}×', rate)
        self.speed.setCurrentIndex(1)
        self.speed.setToolTip('Speed')
        self.speed.currentIndexChanged.connect(lambda: self.player.setPlaybackRate(self.speed.currentData()))
        for w in (self.back, self.play, self.forward):
            player.addWidget(w)
        player.addWidget(self.slider, 1)
        player.addWidget(self.clock)
        player.addWidget(self.speed)
        outer.addWidget(bar)
        self.setCentralWidget(root)

        file_menu = self.menuBar().addMenu('File')
        for text, key, slot in (('Open…', QKeySequence.StandardKey.Open, self.choose),
                                ('Summary…', QKeySequence('Ctrl+Shift+S'), self.show_summary),
                                ('Identify Speakers', QKeySequence('Ctrl+Shift+I'), self.identify_speakers),
                                ('Export…', QKeySequence('Ctrl+E'), self.export),
                                ('Settings…', QKeySequence.StandardKey.Preferences, self.show_settings)):
            if text == 'Summary…' and not SUMMARY_AVAILABLE:
                continue
            action = QAction(text, self)
            action.setShortcut(key)
            action.triggered.connect(slot)
            file_menu.addAction(action)
            if text == 'Open…':
                link = QAction('Open URL…', self)
                link.setShortcut(QKeySequence('Ctrl+L'))
                link.triggered.connect(self.ask_links)
                file_menu.addAction(link)
                self.recent_menu = file_menu.addMenu('Open Recent')
                self.recent_menu.aboutToShow.connect(self.fill_recent)
        self.reveal_action = QAction('Show Notes in Finder' if MAC else 'Show Notes in Folder', self)
        self.reveal_action.triggered.connect(self.reveal_notes)
        file_menu.addAction(self.reveal_action)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=lambda: (self.search.setFocus(), self.search.selectAll()))
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self.skip(-10))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self.skip(10))
        QShortcut(QKeySequence('Ctrl+D'), self, activated=self.star_current)
        self.update_controls()

    def set_icon(self, widget, name, role, size):
        """Give a button or action an icon in the theme colour `role`, and redraw it when the theme changes."""
        self.themed_icons = [entry for entry in self.themed_icons if entry[0] is not widget]
        self.themed_icons.append((widget, name, role, size))
        widget.setIcon(icon(name, getattr(theme.current(), role), size))

    @staticmethod
    def tabular(font):
        font = QFont(font)
        font.setFeature(QFont.Tag('tnum'), 1)
        return font

    def apply_theme(self, key=None):
        if key:
            theme.use(key)
        self.setStyleSheet(theme.stylesheet())
        if getattr(self, 'summary', None):
            self.summary.setStyleSheet(theme.stylesheet())
            self.summary.render()
        self.clock.ensurePolished()
        self.clock.setFont(self.tabular(self.clock.font()))
        self.table.ensurePolished()
        self.time_font = self.tabular(self.table.font())
        for widget, name, role, size in self.themed_icons:
            widget.setIcon(icon(name, getattr(theme.current(), role), size))
        self.rebuild_table()      # star icons, time colour, current row

    def eventFilter(self, obj, event):
        if obj is self.table.viewport() and event.type() == QEvent.Type.Resize \
                and event.size().width() != event.oldSize().width():
            self.relayout_timer.start()
        return super().eventFilter(obj, event)

    def relayout_rows(self):
        """Wrapped text needs new row heights whenever the column width changes."""
        self.table.resizeRowsToContents()

    def icon_button(self, name, tip, slot):
        b = QPushButton()
        b.setObjectName('iconButton')
        self.set_icon(b, name, 'text', 20)
        b.setIconSize(QSize(20, 20))
        b.setToolTip(tip)
        b.clicked.connect(slot)
        return b

    # ---------- files ----------
    def choose(self):
        start = settings().value('last_folder', str(Path.home()))
        paths, _ = QFileDialog.getOpenFileNames(self, 'Open recordings', start,
                                                'Audio (' + ' '.join('*'+e for e in AUDIO_TYPES) + ')')
        self.add_files(paths)

    @staticmethod
    def dropped(event):
        return [u.toLocalFile() for u in event.mimeData().urls()
                if u.isLocalFile() and u.toLocalFile().lower().endswith(AUDIO_TYPES)]

    def dragEnterEvent(self, event):
        if self.dropped(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_files(self.dropped(event))

    def add_files(self, paths):
        """One file while idle: open it. Several, or any while transcribing: queue them and transcribe in turn."""
        paths = list(dict.fromkeys(Path(p).resolve() for p in paths if str(p).lower().endswith(AUDIO_TYPES)))
        if not paths:
            return
        if len(paths) == 1 and not self.job:
            self.open(paths[0])
            return
        busy = {*self.queue, *([self.audio] if self.job else [])}
        self.queue += [p for p in paths if p not in busy]
        self.update_queue()
        if not self.job:
            self.run_queue()

    def run_queue(self):
        """Open and transcribe the next waiting recording; skip ones that are missing or already done."""
        while self.queue:
            path = self.queue.pop(0)
            self.update_queue()
            if not self.open(path, quiet=True):
                self.batch_log.append((path.name, 'file not found'))
                continue
            if self.meta.get('complete'):
                self.batch_log.append((path.name, 'already transcribed'))
                continue
            self.start_job(auto=True)
            return
        self.finish_batch()

    def finish_batch(self):
        log, self.batch_log = self.batch_log, []
        if not log:
            return
        done = sum(result == 'done' for _, result in log)
        problems = [f'{name}: {result}' for name, result in log if result not in ('done', 'already transcribed')]
        self.status.setText(f'Queue finished · {done} of {len(log)} recordings transcribed'
                            + (f' · {len(problems)} failed' if problems else ''))
        if problems:
            QMessageBox.warning(self, 'Some recordings failed', '\n'.join(problems))

    def clear_queue(self):
        self.queue.clear()
        self.links.clear()
        if self.fetch:
            self.fetch.kill()
        self.update_queue()

    def update_queue(self):
        names = [p.name for p in self.queue]
        parts = []
        if self.fetch:
            waiting = f' · {len(self.links)} more links' if self.links else ''
            parts.append(f'⬇ {self.fetch_status}{waiting}')
        if self.diar_status:
            waiting = f' · {len(self.diar_queue)} more' if self.diar_queue else ''
            parts.append(f'👥 {self.diar_status}{waiting}')
        if names:
            more = f' · +{len(names) - 3} more' if len(names) > 3 else ''
            parts.append(f'Up next ({len(names)}): ' + ' · '.join(names[:3]) + more)
        self.queue_row.setVisible(bool(parts))
        self.queue_label.setText('   '.join(parts))
        self.queue_label.setToolTip('\n'.join(names + self.links))

    # ---------- links ----------
    def ask_links(self):
        text, ok = QInputDialog.getMultiLineText(self, 'Open URL', 'Recording links (one per line):')
        if ok:
            self.add_links(text.split())

    def add_links(self, links):
        bad = [u for u in links if not u.lower().startswith(('http://', 'https://'))]
        if bad:
            QMessageBox.warning(self, 'Open URL', 'Not a link:\n' + '\n'.join(bad[:5]))
        self.links += [u for u in links if u not in bad and u not in self.links]
        if not self.fetch:
            self.next_link()
        self.update_queue()

    def next_link(self):
        if not self.links:
            return
        url = self.links.pop(0)
        self.fetch_status = 'Connecting…'
        self.fetch = worker_process(self, {'mode': 'fetch', 'url': url, 'folder': str(downloads_folder())})
        self.fetch.setProperty('url', url)
        self.fetch.readyReadStandardOutput.connect(self.on_fetch_events)
        self.fetch.finished.connect(self.on_fetch_finished)
        self.update_queue()

    def on_fetch_events(self):
        for event in read_events(self.fetch):
            if event['type'] == 'progress':
                size = f"{event.get('bytes', 0)/1e6:.0f} MB"
                self.fetch_status = (f"{event['stage']} · {event['value']*100:.0f}%" if event['value'] >= 0
                                     else f"{event['stage']} · {size}" if event.get('bytes') else event['stage'])
            elif event['type'] == 'done':
                self.fetch.setProperty('path', event['path'])
            elif event['type'] == 'error':
                self.fetch.setProperty('error', event['message'])
        self.update_queue()

    def on_fetch_finished(self, _code, _status):
        proc = self.fetch
        self.on_fetch_events()
        self.fetch = None
        cleanup(proc)
        path, error = proc.property('path'), proc.property('error')
        if path:
            self.queue.append(Path(path))
            if not self.job:
                self.run_queue()
        elif error:
            QMessageBox.warning(self, 'Download failed', f"{proc.property('url')}\n\n{error}")
        self.next_link()
        self.update_queue()

    def show_summary(self):
        if not self.audio or not SUMMARY_AVAILABLE:
            return
        if not self.summary:
            self.summary = SummaryWindow(self)
            self.summary.setStyleSheet(theme.stylesheet())
        self.summary.refresh()
        self.summary.show()
        self.summary.raise_()
        self.summary.activateWindow()

    def remember_recent(self, path):
        prefs = settings()
        recent = [p for p in prefs.value('recent', [], type=list) if p != str(path)]
        prefs.setValue('recent', [str(path)] + recent[:9])

    def fill_recent(self):
        self.recent_menu.clear()
        recent = [p for p in settings().value('recent', [], type=list) if Path(p).is_file()]
        for p in recent:
            self.recent_menu.addAction(Path(p).name, lambda p=p: self.add_files([p]))
        if not recent:
            self.recent_menu.addAction('None').setEnabled(False)

    def reveal_notes(self):
        target = self.notes_file if self.notes_file and self.notes_file.is_file() else self.audio
        if target:
            if MAC:
                QProcess.startDetached('open', ['-R', str(target)])
            elif sys.platform == 'win32':
                QProcess.startDetached('explorer', ['/select,', str(target)])
            else:
                QProcess.startDetached('xdg-open', [str(target.parent)])

    def open(self, path, quiet=False):
        if self.job:
            QMessageBox.information(self, 'Transcribing', 'Stop the current transcription first.')
            return False
        path = Path(path)
        if not path.is_file():
            if not quiet:
                QMessageBox.warning(self, 'File not found', f'Could not find the file.\n{path}')
            return False
        self.save_now()
        settings().setValue('last_folder', str(path.parent))
        self.audio = path
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.title.setText(path.name)
        self.segments, self.meta = [], {}
        notes = self.notes_file = find_notes(path, settings().value('notes_folder', ''))
        self.title.setToolTip(f'Notes: {notes}')
        if self.summary and not self.summary.proc:
            self.summary.refresh()
        self.remember_recent(path)
        if notes.is_file():
            try:
                self.meta = load(notes)
                self.segments = self.meta['segments']
            except (OSError, ValueError, TypeError, KeyError) as e:
                QMessageBox.warning(self, 'Could not read notes', f'The saved notes could not be read. You can transcribe again.\n{e}')
                self.meta = {}
        self.rebuild_table()
        if self.meta and self.meta.get('source') != fingerprint(path):
            self.status.setText('⚠ The recording changed after these notes were made. Transcribe again if times look off.')
        elif self.meta and not self.meta.get('complete'):
            self.status.setText(f'Transcription stopped at {clock(self.segments[-1].end if self.segments else 0)}. '
                                'Click “Resume” to continue.')
        elif self.meta:
            self.status.setText(f"Loaded saved notes · {lines(len(self.segments))} · {self.meta.get('model', '')}")
        else:
            self.status.setText('Ready to transcribe.')
        self.update_controls()
        return True

    # ---------- table ----------
    # Rows are groups of saved lines (see transcript.group_segments). Stars, notes and times live on the
    # lines themselves, so switching between Lines / Sentences / Paragraphs never changes the saved notes.
    def grouping(self):
        return settings().value('grouping', DEFAULT_GROUPING)

    def row_segments(self, row):
        return [self.segments[i] for i in self.groups[row]]

    def row_text(self, row):
        return ' '.join(s.text for s in self.row_segments(row) if s.text)

    def row_note(self, row):
        return ' · '.join(s.note for s in self.row_segments(row) if s.note)

    def rebuild_table(self):
        self.stop_typing()
        self.loading = True
        self.table.setRowCount(0)
        self.current_row = -1
        self.groups = group_segments(self.segments, self.grouping())
        for row in range(len(self.groups)):
            self.add_row(row)
        self.starts = [self.segments[g[0]].start for g in self.groups]
        self.table.setColumnHidden(SPEAKER, not any(s.speaker for s in self.segments))
        self.loading = False
        self.apply_filter()

    def add_row(self, row):
        self.table.insertRow(row)
        star = QTableWidgetItem()
        star.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        star.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        when = QTableWidgetItem(clock(self.segments[self.groups[row][0]].start))
        when.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        when.setForeground(QColor(theme.current().muted))
        when.setFont(self.time_font)
        text = QTableWidgetItem(self.row_text(row))
        if self.grouping() != 'line':
            # Several lines share this cell, so its text is read-only here; edit it in the Lines view.
            text.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        who = QTableWidgetItem()
        who.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(row, STAR, star)
        self.table.setItem(row, TIME, when)
        self.table.setItem(row, SPEAKER, who)
        self.paint_speaker(row)
        self.table.setItem(row, TEXT, text)
        self.table.setItem(row, NOTE, QTableWidgetItem(self.row_note(row)))
        self.paint_star(row)

    def names(self):
        return self.meta.setdefault('speakers', {})

    def speaker_color(self, label):
        colors = theme.current().speaker_colors
        return colors[(int(label[1:]) - 1) % len(colors)] if label[1:].isdigit() else theme.current().muted

    def paint_speaker(self, row):
        item = self.table.item(row, SPEAKER)
        label = self.row_segments(row)[0].speaker
        item.setData(LABEL_ROLE, label)
        item.setText(speaker_name(label, self.names()))
        item.setForeground(QColor(self.speaker_color(label)))

    def labelled(self):
        return sorted({s.speaker for s in self.segments if s.speaker}, key=lambda l: int(l[1:]) if l[1:].isdigit() else 0)

    def on_double_click(self, row, column):
        if column == SPEAKER:
            self.rename_speaker(self.table.item(row, SPEAKER).data(LABEL_ROLE))

    def rename_speaker(self, label):
        if not label:
            return
        current = self.names().get(label, '')
        name, ok = QInputDialog.getText(self, 'Rename speaker', f'Name for {speaker_name(label, {})}:', text=current)
        if not ok:
            return
        name = ' '.join(name.split())[:60]
        if name:
            self.names()[label] = name
        else:
            self.names().pop(label, None)
        self.loading = True
        for row in range(self.table.rowCount()):
            if self.table.item(row, SPEAKER).data(LABEL_ROLE) == label:
                self.paint_speaker(row)
        self.loading = False
        self.schedule_save()

    def speaker_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid() or index.column() != SPEAKER or not self.labelled():
            return
        row = index.row()
        menu = QMenu(self)
        menu.addAction('Rename this speaker…', lambda: self.rename_speaker(self.row_segments(row)[0].speaker))
        menu.addSeparator()
        current = self.row_segments(row)[0].speaker
        for label in self.labelled():
            action = menu.addAction(f'Said by {speaker_name(label, self.names())}', lambda l=label: self.set_speaker(row, l))
            action.setEnabled(label != current)
        new = f'S{max(int(l[1:]) for l in self.labelled() if l[1:].isdigit()) + 1}'
        menu.addAction('Said by a new speaker', lambda: self.set_speaker(row, new))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def set_speaker(self, row, label):
        for seg in self.row_segments(row):
            seg.speaker = label
        first = self.groups[row][0]
        self.rebuild_table()                     # rows may split or join around a new speaker
        self.schedule_save()
        for r, group in enumerate(self.groups):
            if first in group:
                self.table.selectRow(r)
                self.table.scrollToItem(self.table.item(r, TEXT))
                break

    def paint_star(self, row):
        item = self.table.item(row, STAR)
        on = any(s.starred for s in self.row_segments(row))
        t = theme.current()
        item.setIcon(icon('starFilled' if on else 'star', t.accent if on else t.star_off, 16))

    def on_click(self, row, column):
        if column == STAR:
            self.toggle_star(row)
            return
        if column in (TEXT, NOTE) and self.table.state() == QAbstractItemView.State.EditingState:
            return
        # Start a moment early so the first word is not clipped.
        self.player.setPosition(max(0, int(self.starts[row]*1000) - 500))
        # While transcribing, a click only moves the playhead; press Space or ▶ to listen.
        if not self.job:
            self.player.play()

    def toggle_star(self, row):
        on = not any(s.starred for s in self.row_segments(row))
        for seg in self.row_segments(row):
            seg.starred = on
        self.paint_star(row)
        self.schedule_save()
        if self.starred_only.isChecked():
            self.apply_filter()

    def star_current(self):
        row = self.table.currentRow()
        if row < 0:
            row = self.current_row
        if row >= 0:
            self.toggle_star(row)

    def on_edit(self, item):
        if self.loading:
            return
        # itemChanged also fires for colour changes; only a real change of text or note counts as an edit.
        row, column = item.row(), item.column()
        if column not in (TEXT, NOTE) or item.data(TYPING_ROLE):   # partial text being typed out is not an edit
            return
        value = item.text().strip()
        segs = self.row_segments(row)
        if column == TEXT:
            if len(segs) != 1 or value == segs[0].text:
                return
            segs[0].text = value
        else:
            if value == self.row_note(row):
                return
            segs[0].note = value                 # a group's note is kept on its first line
            for seg in segs[1:]:
                seg.note = ''
        self.table.resizeRowToContents(row)
        self.schedule_save()

    def row_hidden(self, row, query, stars):
        segs = self.row_segments(row)
        who = speaker_name(segs[0].speaker, self.names()).lower() if segs else ''
        hit = (not query or query in self.row_text(row).lower() or query in self.row_note(row).lower()
               or query in who)
        return not hit or (stars and not any(s.starred for s in segs))

    def apply_filter(self):
        query = self.search.text().strip()
        self.delegate.pattern = re.compile(re.escape(query), re.IGNORECASE) if query else None
        stars = self.starred_only.isChecked()
        shown = 0
        for row in range(len(self.groups)):
            hidden = self.row_hidden(row, query.lower(), stars)
            self.table.setRowHidden(row, hidden)
            shown += not hidden
        self.table.viewport().update()
        self.table.resizeRowsToContents()
        unit = {'sentence': 'sentence', 'paragraph': 'paragraph'}.get(self.grouping())
        count = shown if (query or stars) else len(self.groups)
        text = (f'{count:,} {unit}' + ('' if count == 1 else 's')) if unit else lines(count)
        self.count.setText(text if self.groups else '')

    def next_match(self):
        start = self.table.currentRow()
        rows = [r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)]
        if not rows:
            return
        after = [r for r in rows if r > start]
        row = after[0] if after else rows[0]
        self.table.selectRow(row)
        self.table.scrollToItem(self.table.item(row, TEXT), QAbstractItemView.ScrollHint.PositionAtCenter)

    # ---------- saving ----------
    def schedule_save(self):
        self.save_timer.start()

    def save_now(self):
        self.save_timer.stop()
        if not self.audio or not self.segments:
            return
        try:
            save(self.notes_file, self.audio, self.segments, model=self.meta.get('model', ''),
                 complete=bool(self.meta.get('complete')), duration=self.meta.get('duration', 0.0),
                 speakers=self.meta.get('speakers', {}))
        except OSError as e:
            self.status.setText(f'Could not save notes: {e}')

    def export(self):
        if not self.segments:
            return
        box = QMessageBox(self)
        box.setWindowTitle('Export as text')
        box.setText('Which lines do you want to export?')
        everything = box.addButton('All lines', QMessageBox.ButtonRole.AcceptRole)
        stars = box.addButton('★ lines only', QMessageBox.ButtonRole.AcceptRole)
        box.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() not in (everything, stars):
            return
        only = box.clickedButton() is stars
        default = self.notes_file.parent/(self.audio.stem + ('.starred' if only else '') + '.txt')
        path, _ = QFileDialog.getSaveFileName(self, 'Export as text', str(default), 'Text (*.txt)')
        if path:
            groups = self.groups if self.grouping() != 'line' else None
            Path(path).write_text(to_text(self.audio.name, self.segments, starred_only=only, groups=groups,
                                          speakers=self.names()),
                                  encoding='utf-8')
            self.status.setText(f'Exported: {path}')

    # ---------- transcription ----------
    def change_view(self):
        key = self.view_mode.currentData()
        if key == self.grouping():
            return
        settings().setValue('grouping', key)
        # Keep the line being read in view across the switch.
        row = self.table.currentRow() if self.table.currentRow() >= 0 else self.current_row
        first = self.groups[row][0] if 0 <= row < len(self.groups) else None
        self.rebuild_table()
        if first is not None:
            target = next(r for r, g in enumerate(self.groups) if first in g)
            self.table.selectRow(target)
            self.table.scrollToItem(self.table.item(target, TEXT), QAbstractItemView.ScrollHint.PositionAtTop)

    def show_settings(self):
        if SettingsDialog(self).exec():
            self.view_mode.blockSignals(True)
            self.view_mode.setCurrentIndex(max(0, self.view_mode.findData(self.grouping())))
            self.view_mode.blockSignals(False)
            self.apply_theme(settings().value('theme', theme.DEFAULT_THEME))
        self.update_controls()

    def toggle_job(self):
        if self.job:
            self.stop_job()
        else:
            self.start_job()

    def start_job(self, auto=False):
        """auto: started from the queue, so resume unfinished notes without asking."""
        if not self.audio:
            return
        prefs = settings()
        spec = models.get(prefs.value('model', models.DEFAULT_MODEL))
        if not models.is_downloaded(spec):
            QMessageBox.information(self, 'Model needed', f'Download {spec.label} in Settings.')
            self.show_settings()
            return
        resume = False
        if auto and self.segments:
            resume = True
        elif self.segments:
            box = QMessageBox(self)
            box.setWindowTitle('Existing notes')
            box.setText('This recording already has notes.')
            cont = box.addButton('Resume', QMessageBox.ButtonRole.AcceptRole) if not self.meta.get('complete') else None
            again = box.addButton('Start over (clears ★ and notes)', QMessageBox.ButtonRole.DestructiveRole)
            box.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if cont is not None and box.clickedButton() is cont:
                resume = True
                self.save_now()
            elif box.clickedButton() is not again:
                return
        if not resume:
            self.segments = []
            self.meta = {}
            self.rebuild_table()
        self.meta['complete'] = False
        self.job_started = time.monotonic()
        self.job_from = self.segments[-1].end if self.segments else 0.0
        self.job = worker_process(self, {'mode': 'transcribe', 'audio': str(self.audio), 'out': str(self.notes_file),
                                         'model': spec.id, 'device': prefs.value('device', models.DEFAULT_DEVICE),
                                         'hint': prefs.value('hint', ''), 'resume': resume})
        self.job.readyReadStandardOutput.connect(self.on_events)
        self.job.finished.connect(self.on_finished)
        self.job_error = ''
        self.job_model = spec.label
        self.job_auto = auto or bool(self.queue)
        self.job_stopped = False
        self.stack.setCurrentIndex(1)
        self.set_progress(-1, 'Getting ready…')
        self.update_controls()

    def stop_job(self):
        if self.job:
            self.job_stopped = True
            self.job.kill()

    def on_events(self):
        for event in read_events(self.job):
            kind = event['type']
            if kind == 'segment':
                self.add_segment(Segment(event['start'], event['end'], event['text']))
                self.meta['duration'] = self.meta.get('duration') or 0
            elif kind == 'progress':
                self.set_progress(event['value'], event['stage'])
            elif kind == 'device':
                self.job_model = f"{self.job_model} · {'Mac GPU' if event['device'] == 'mps' else 'CPU'}"
            elif kind == 'done':
                self.meta['model'] = event['model']
                self.meta['complete'] = True
                self.restore_blank_lines(event.get('path'))
            elif kind == 'error':
                self.job_error = event['message']

    def add_segment(self, seg):
        """A new line from the transcriber: it joins the last row (same sentence/paragraph) or starts one."""
        self.segments.append(seg)
        index = len(self.segments) - 1
        self.loading = True
        if self.groups and not starts_group(self.row_segments(len(self.groups) - 1), seg, self.grouping()):
            row = len(self.groups) - 1
            self.groups[row].append(index)
            item = self.table.item(row, TEXT)
            shown = len(item.text())
        else:
            self.groups.append([index])
            self.starts.append(seg.start)
            row = len(self.groups) - 1
            self.add_row(row)
            item = self.table.item(row, TEXT)
            shown = 0
        if settings().value('typing', True, type=bool):
            item.setText(self.row_text(row)[:shown])
            item.setData(TYPING_ROLE, True)
            if not any(entry[0] == row for entry in self.typing):
                self.typing.append([row, None, shown])
            self.type_timer.start()
        else:
            item.setText(self.row_text(row))
        self.loading = False
        self.table.resizeRowToContents(row)
        self.table.setRowHidden(row, self.row_hidden(row, self.search.text().strip().lower(),
                                                     self.starred_only.isChecked()))

    def type_tick(self):
        """Reveal waiting text like the translator app: 14 ms a character, at most 280 ms a line."""
        if not self.typing:
            self.type_timer.stop()
            return
        entry = self.typing[0]
        now = time.monotonic()
        if entry[1] is None:
            entry[1] = now
        row, start, text = entry[0], entry[2], self.row_text(entry[0])
        new = len(text) - start
        # When lines pile up, type each one faster so the display keeps pace with the transcriber.
        duration = min(.28, new*.014) / len(self.typing)
        shown = len(text) if duration <= 0 else min(len(text), start + int(new*(now - entry[1])/duration))
        item = self.table.item(row, TEXT)
        self.loading = True
        item.setText(text[:shown])
        if shown >= len(text):
            item.setData(TYPING_ROLE, None)
            self.typing.pop(0)
        self.loading = False
        self.table.resizeRowToContents(row)

    def stop_typing(self):
        """Show all text in full at once (when the job ends or the table is rebuilt)."""
        self.type_timer.stop()
        self.loading = True
        for row, *_ in self.typing:
            if row < self.table.rowCount():
                self.table.item(row, TEXT).setText(self.row_text(row))
                self.table.item(row, TEXT).setData(TYPING_ROLE, None)
                self.table.resizeRowToContents(row)
        self.loading = False
        self.typing = []

    def restore_blank_lines(self, path):
        """Fill any line whose text went missing on screen from the worker's saved file."""
        if not path or not any(not s.text for s in self.segments):
            return
        try:
            saved = {(s.start, s.end): s.text for s in load(path)['segments']}
        except (OSError, ValueError, TypeError, KeyError):
            return
        for seg in self.segments:
            if not seg.text:
                seg.text = saved.get((seg.start, seg.end), '')
        self.loading = True
        for row in range(len(self.groups)):
            self.table.item(row, TEXT).setText(self.row_text(row))
        self.loading = False

    def set_progress(self, value, stage):
        if value < 0:
            self.progress.setRange(0, 0)
            self.status.setText(stage)
            return
        self.progress.setRange(0, 1000)
        self.progress.setValue(int(value*1000))
        text = f'{stage} · {value*100:.0f}% · {self.job_model}'
        done = value - (self.job_from / self.player_seconds() if self.player_seconds() else 0)
        elapsed = time.monotonic() - self.job_started
        if done > .03 and elapsed > 20:
            left = elapsed * (1 - value) / done
            text += f' · about {max(1, round(left/60))} min left'
        self.status.setText(text)

    def player_seconds(self):
        return self.player.duration() / 1000

    def on_finished(self, code, _status):
        proc, self.job = self.job, None
        self.on_events_from(proc)
        cleanup(proc)
        self.stop_typing()
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000 if self.meta.get('complete') else 0)
        minutes = (time.monotonic() - self.job_started) / 60
        # In a queue, a failure is reported at the end instead of stopping the rest.
        go_on = ((self.job_auto or bool(self.queue)) and not self.job_stopped
                 and (self.meta.get('complete') or self.job_error))
        if self.job_auto and self.audio:
            self.batch_log.append((self.audio.name, 'done' if self.meta.get('complete')
                                   else 'failed: ' + self.job_error[:200] if self.job_error else 'stopped'))
        if self.meta.get('complete'):
            self.status.setText(f'Done · {lines(len(self.segments))} · took {minutes:.1f} min · {self.meta.get("model", "")}')
        elif self.job_error:
            self.status.setText('Transcription failed: ' + self.job_error)
            if not go_on:
                QMessageBox.warning(self, 'Transcription failed', self.job_error)
        else:
            self.status.setText(f'Stopped · {lines(len(self.segments))} saved · click “Resume” to continue.')
        if self.audio:
            self.meta['duration'] = self.player_seconds()
            self.meta.setdefault('model', self.job_model)
            self.save_now()
        self.apply_filter()
        self.update_controls()
        if self.meta.get('complete') and self.audio and settings().value('speakers', False, type=bool):
            if speakers.is_downloaded():
                self.queue_speakers(self.audio, self.notes_file)
            else:
                self.status.setText(self.status.text() + ' · download speaker models in Settings to label speakers')
        if go_on:
            QTimer.singleShot(0, self.run_queue)
        elif self.job_stopped and self.queue:
            self.status.setText(self.status.text() + f' · {len(self.queue)} waiting in the queue')

    # ---------- speakers ----------
    def identify_speakers(self):
        """Menu: label the open recording's lines with speakers (downloads the small models first if needed)."""
        if not self.audio or not self.segments:
            return
        if self.job:
            QMessageBox.information(self, 'Speakers', 'Speakers are identified when transcription finishes.')
            return
        if not speakers.is_downloaded():
            answer = QMessageBox.question(self, 'Download speaker models',
                                          f'Identifying speakers needs two small models ({speakers.SIZE}), '
                                          'downloaded once.\n\nOnly the models are downloaded. Recordings stay on ' + THIS_COMPUTER + '.')
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.diar_status = 'Downloading speaker models…'
            fetch = worker_process(self, {'mode': 'download', 'model': 'speakers'})
            audio, notes = self.audio, self.notes_file
            fetch.finished.connect(lambda code, _s: self.speaker_models_ready(fetch, code, audio, notes))
            self.diar_fetch = fetch
            self.update_queue()
            return
        self.save_now()
        self.queue_speakers(self.audio, self.notes_file)

    def speaker_models_ready(self, proc, code, audio, notes):
        cleanup(proc)
        self.diar_fetch = None
        self.diar_status = ''
        if code == 0 and speakers.is_downloaded():
            self.queue_speakers(audio, notes)
        else:
            self.status.setText('Speaker model download failed.')
        self.update_queue()

    def queue_speakers(self, audio, notes):
        if (audio, notes) not in self.diar_queue:
            self.diar_queue.append((audio, notes))
        if not self.diar:
            self.next_speakers()

    def next_speakers(self):
        if not self.diar_queue:
            self.diar_status = ''
            self.update_queue()
            return
        audio, notes = self.diar_queue.pop(0)
        self.diar_status = f'Speakers · {audio.name}'
        self.diar = worker_process(self, {'mode': 'speakers', 'audio': str(audio), 'notes': str(notes)})
        self.diar.setProperty('notes', str(notes))
        self.diar.setProperty('name', audio.name)
        self.diar.readyReadStandardOutput.connect(self.on_speaker_events)
        self.diar.finished.connect(self.on_speakers_finished)
        self.update_queue()

    def on_speaker_events(self):
        for event in read_events(self.diar):
            if event['type'] == 'progress':
                value = f" · {event['value']*100:.0f}%" if event['value'] >= 0 else ''
                self.diar_status = f"Speakers · {self.diar.property('name')}{value}"
            elif event['type'] == 'done':
                self.diar.setProperty('labels', json.dumps(event['labels']))
                self.diar.setProperty('count', event['count'])
            elif event['type'] == 'error':
                self.diar.setProperty('error', event['message'])
        self.update_queue()

    def on_speakers_finished(self, _code, _status):
        proc = self.diar
        self.on_speaker_events()
        self.diar = None
        cleanup(proc)
        labels, error = proc.property('labels'), proc.property('error')
        if labels and self.notes_file and str(self.notes_file) == proc.property('notes'):
            by_time = {(a, b): l for a, b, l in json.loads(labels)}
            for seg in self.segments:
                seg.speaker = by_time.get((seg.start, seg.end), seg.speaker)
            self.rebuild_table()
            self.save_now()
            self.status.setText(f"{proc.property('count')} speakers · double-click a name to rename it")
        elif error:
            self.status.setText(f"Speakers failed for {proc.property('name')}: {error[:200]}")
        self.next_speakers()

    def on_events_from(self, proc):
        self.job = proc
        self.on_events()
        self.job = None

    def update_controls(self):
        running = self.job is not None
        has = self.audio is not None
        self.stack.setCurrentIndex(1 if has else 0)
        self.run_button.setText(' Stop' if running else (' Resume' if has and self.segments and not self.meta.get('complete')
                                                         else ' Transcribe'))
        self.run_button.setObjectName('ghost' if running else 'primary')
        self.run_button.style().unpolish(self.run_button)
        self.run_button.style().polish(self.run_button)
        self.run_button.setEnabled(has)
        self.export_button.setEnabled(bool(self.segments))
        self.summary_button.setEnabled(bool(self.segments))
        self.reveal_action.setEnabled(has)
        self.open_button.setToolTip('Add to queue' if running else 'Open recordings')
        self.progress.setVisible(running or self.progress.value() > 0)
        spec = models.get(settings().value('model', models.DEFAULT_MODEL))
        device = dict(models.DEVICES).get(settings().value('device', models.DEFAULT_DEVICE), '')
        self.settings_button.setToolTip(f'Settings · {spec.label} · {device}')
        for w in (self.back, self.play, self.forward, self.slider, self.speed):
            w.setEnabled(has)

    # ---------- playback ----------
    def toggle_play(self):
        if not self.audio:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def skip(self, seconds):
        if self.audio:
            self.player.setPosition(max(0, self.player.position() + seconds*1000))

    def on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.set_icon(self.play, 'pause' if playing else 'play', 'text', 20)

    def on_duration(self, ms):
        self.slider.setRange(0, ms)
        self.on_position(self.player.position())

    def on_position(self, ms):
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.clock.setText(f'{clock(ms/1000)} / {clock(self.player.duration()/1000)}')
        row = bisect.bisect_right(self.starts, ms/1000 + .05) - 1
        if row != self.current_row:
            self.mark_current(row)

    def mark_current(self, row):
        old, self.current_row = self.current_row, row
        self.loading = True
        for r, color in ((old, None), (row, QColor(theme.current().current_row))):
            if 0 <= r < self.table.rowCount():
                for c in range(5):
                    item = self.table.item(r, c)
                    if item:
                        item.setBackground(color if color else QColor(0, 0, 0, 0))
        self.loading = False
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if (playing and self.follow.isChecked() and 0 <= row < self.table.rowCount()
                and not self.table.isRowHidden(row) and self.table.state() != QAbstractItemView.State.EditingState):
            self.table.scrollToItem(self.table.item(row, TEXT), QAbstractItemView.ScrollHint.EnsureVisible)

    def closeEvent(self, event):
        if self.job:
            waiting = f' {len(self.queue)} queued recordings will not be transcribed.' if self.queue else ''
            answer = QMessageBox.question(self, 'Transcribing', 'Stop transcribing and quit? Notes so far are saved.' + waiting)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.job.kill()
            self.job.waitForFinished(3000)
        if self.fetch:
            self.fetch.kill()
            self.fetch.waitForFinished(2000)
        if self.summary:
            self.summary.stop_all()
        for proc in (self.diar, getattr(self, 'diar_fetch', None)):
            if proc:
                proc.kill()
                proc.waitForFinished(2000)
        self.save_now()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Voice Notes')
    window = Window()
    window.show()
    if len(sys.argv) > 1:
        window.add_files(sys.argv[1:])
    sys.exit(app.exec())
