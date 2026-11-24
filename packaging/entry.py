"""Entry point of the packaged app (PyInstaller).

`Voice Notes` opens the window; `Voice Notes --worker job.json` runs one background job (the app starts
itself this way, since a packaged app has no separate Python); `--selftest` checks the bundle and exits.
"""
import os
import sys


def attach_stdout():
    """A windowed Windows build may start without sys.stdout even when the parent gave it a pipe."""
    if sys.stdout is None and os.name == 'nt':
        import ctypes
        import msvcrt
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        if handle not in (0, -1):
            sys.stdout = open(msvcrt.open_osfhandle(handle, os.O_WRONLY), 'w', encoding='utf-8', buffering=1)
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')


def selftest():
    """Import everything the jobs load lazily and find FFmpeg, so a broken bundle fails the build."""
    attach_stdout()
    import av
    import ctranslate2
    import faster_whisper
    import sherpa_onnx
    import numpy
    from PySide6 import QtMultimedia
    from voice_notes import app, worker, speakers, models, summarize
    for name in ('ffmpeg', 'ffprobe'):
        path = worker.tool(name)
        out = worker.subprocess.run([path, '-hide_banner', '-version'], capture_output=True, text=True, **worker.NO_WINDOW)
        assert out.returncode == 0, out.stderr
        print(name, path, out.stdout.splitlines()[0])
    vad = os.path.join(os.path.dirname(faster_whisper.__file__), 'assets')
    assert os.listdir(vad), 'faster-whisper VAD model missing'
    # Playback codecs: Qt Multimedia's FFmpeg backend and its libraries.
    bundle = os.path.dirname(sys.executable) if not hasattr(sys, '_MEIPASS') else sys._MEIPASS
    names = [f.lower() for _, _, files in os.walk(bundle) for f in files]
    for want in ('ffmpegmediaplugin', 'avcodec', 'avformat', 'swresample'):
        assert any(want in n for n in names), f'{want} missing from the bundle'
    if os.name == 'nt':
        from voice_notes import gpu
        cudnn = gpu.file_version(os.path.join(os.path.dirname(ctranslate2.__file__), 'cudnn64_9.dll'))
        assert cudnn and cudnn.startswith('9.'), f'cuDNN loader version not readable: {cudnn}'
        print('GPU support: cuDNN', cudnn, '| packages', gpu.packages())
    print('ctranslate2', ctranslate2.__version__, '| av', av.__version__, '| sherpa-onnx', sherpa_onnx.__version__)
    from voice_notes import update
    assert update.version(), 'build version missing'
    print('version', update.version(), '| updates', update.supported())
    print('selftest ok')


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--worker':
        attach_stdout()
        del sys.argv[1]
        from voice_notes.worker import main
        main()
    elif len(sys.argv) > 1 and sys.argv[1] == '--selftest':
        try:
            selftest()
        except BaseException:
            import traceback
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
            os._exit(1)
    else:
        from voice_notes.app import main
        main()
