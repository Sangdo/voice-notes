import os
import tempfile

# Keep test runs (and the worker processes they start) out of the real log file.
os.environ['VOICE_NOTES_LOG_DIR'] = tempfile.mkdtemp(prefix='voice-notes-test-logs-')
