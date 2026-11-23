PY ?= .venv/bin/python
CASE ?= 25-5930
YEAR ?= 2026
MODEL ?= turbo
JOB := .job

.PHONY: run restart setup test testdata transcribe compare

run:
	$(PY) -m voice_notes

# Quit any running app (and its worker), then start it again.
restart:
	-pkill -f '[-]m voice_notes' ; sleep 1
	$(PY) -m voice_notes

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -r requirements-dev.txt

test:
	$(PY) -m pytest -q

# Public Supreme Court oral argument: audio + official transcript, for accuracy checks.
testdata:
	mkdir -p testdata
	curl -fL -o testdata/$(CASE).mp3 https://www.supremecourt.gov/media/audio/mp3files/$(CASE).mp3
	curl -fL -o testdata/$(CASE).pdf "https://www.supremecourt.gov$$(curl -fsL https://www.supremecourt.gov/oral_arguments/audio/$(YEAR)/$(CASE) \
		| grep -o '/oral_arguments/argument_transcripts/[^"]*\.pdf' | head -1)"

# Headless transcription with the same worker the app uses: make transcribe MODEL=small.en
transcribe:
	mkdir -p testdata/$(JOB)
	printf '{"mode":"transcribe","audio":"%s","out":"%s","workdir":"%s","model":"$(MODEL)","device":"auto"}' \
		"$(CURDIR)/testdata/$(CASE).mp3" "$(CURDIR)/testdata/$(CASE).$(MODEL).transcript.json" \
		"$(CURDIR)/testdata/$(JOB)" > testdata/$(JOB)/job.json
	time $(PY) -m voice_notes.worker testdata/$(JOB)/job.json | grep -v '"progress"\|"segment"'

compare:
	$(PY) tools/compare_scotus.py testdata/$(CASE).$(MODEL).transcript.json testdata/$(CASE).pdf
