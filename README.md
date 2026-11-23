# Voice Notes

긴 영어 녹음(법원 재판 등)을 이 컴퓨터 안에서 Whisper로 전사해 타임스탬프 노트로 만드는 데스크톱 앱입니다. 노트를 훑어보고 중요한 줄을 클릭하면 그 시점부터 바로 들을 수 있습니다. 화면은 영어이며, 기본 디자인은 `translator` 앱(Still)과 같은 테마이고, 설정에서 바꿀 수 있습니다.

## 실행

```sh
make setup      # 최초 1회: .venv 생성 + 의존성 설치 (uv, Python 3.12)
make run        # 또는 Finder에서 run.command
make restart    # 실행 중인 앱을 끄고 다시 실행
```

FFmpeg가 필요합니다 (`brew install ffmpeg`).

## 사용

1. 녹음 파일을 창에 끌어다 놓거나 **Open**을 누릅니다. mp3 등 녹음 파일 링크는 **File → Open URL…**(⌘L)에 붙여넣으면(여러 줄 가능) Downloads 폴더에 받은 뒤 바로 전사합니다. 여러 개를 한꺼번에 넣으면 대기열에 쌓여 차례로 전사됩니다(전사 중에 더 넣어도 됩니다). 이미 끝난 녹음은 건너뛰고, 중간에 멈춘 녹음은 이어서 전사합니다.
2. **Transcribe**를 누릅니다. 전사되는 줄이 타이핑되듯 표에 나타나며(설정에서 끌 수 있음), 진행 중에도 읽고 재생할 수 있습니다.
3. 설정의 **Show**에서 보기 단위를 고릅니다. **Paragraphs**(기본)는 문장을 30초 안팎이나 말이 멈춘 곳에서 문단으로 묶고, **Sentences**는 중간에 잘린 문장을 이어 붙이고, **Lines**는 Whisper가 나눈 조각 그대로 보여줍니다. 저장 파일은 항상 조각 단위로 남으므로 언제든 바꿀 수 있습니다(문장 끝 판정에서 Mr. · v. · U.S. 같은 약어는 제외). 본문 글꼴은 대법원 사이트와 같은 Source Sans입니다. 본문 수정은 Lines 보기에서 합니다.
4. 줄을 클릭하면 그 시점(0.5초 앞)부터 재생합니다. 전사 중에는 클릭해도 위치만 옮기고, Space나 ▶로 재생합니다. 검색(⌘F)으로 단어를 찾고, ★로 중요한 줄을 표시하고, 메모를 적습니다(더블클릭).
5. **Export**로 전체 또는 ★ 줄만 텍스트로 내보냅니다. 문장·문단 보기에서는 그 단위로 내보냅니다.
6. **화자 구분**(기본 꺼짐): **File → Identify Speakers**(⌘⇧I)로 실행하거나, **Settings → Speakers**를 켜면 전사가 끝난 뒤 자동으로 이어서 실행됩니다(오프라인, 55분에 약 2분, 메모리 약 1.7 GB). 표에 **Speaker** 칸이 생기고 화자가 바뀌는 곳에서 문단이 나뉩니다. 이름을 더블클릭하면 그 화자 전체의 이름을 바꾸고(예: Speaker 3 → Justice Kagan), 오른쪽 클릭으로 그 줄의 화자만 고칠 수 있습니다. 처음 한 번 작은 모델(47 MB)을 받습니다. 대법원 변론 두 건에서 실제 화자 11명을 모두 찾았고 공식 속기록 대비 정확도는 98.5% · 99.1%였습니다(`tools/compare_speakers.py`). Whisper의 한 줄에 두 사람 말이 섞인 경우(약 1%)는 그 줄 전체가 한 화자로 표시됩니다. 이름과 화자 정보는 노트에 저장되어 내보내기와 요약에도 쓰입니다.
7. **Summary**(⌘⇧S)를 누르면 이 Mac에서 오프라인으로 영어 요약을 만듭니다. 약 8분 단위 구간 요약과, 그것을 모은 개요(Overview · Key arguments · Questions from the bench · Notable concessions or statements)가 나오며, 모든 항목에 붙은 시간을 클릭하면 그 부분부터 들을 수 있습니다. 55분 변론 기준 약 2분 30초(M4, Qwen3 4B). 요약은 노트 옆에 `이름.summary.json`으로 저장되고 **Copy** · **Export**로 꺼낼 수 있습니다. 요약 모델은 설정의 **Summary**에서 고르며, 처음 한 번 모델 파일(2.3 GB)을 받습니다.

요약은 로컬 AI 모델이 만든 것이라 틀릴 수 있습니다. 화자 표시가 없는 전사를 바탕으로 하므로 누가 말했는지 잘못 붙일 수 있으니, 중요한 내용은 시간을 눌러 원음으로 확인하세요.

단축키: Space 재생/일시정지 · ←/→ 10초 이동 · ⌘D 현재 줄 ★ · ⌘F 검색 · ⌘E 내보내기.

노트는 기본으로 녹음 파일과 같은 폴더에 `이름.transcript.json`으로 자동 저장됩니다(예: `Downloads/25-352.mp3` → `Downloads/25-352.transcript.json`). 설정의 **Notes → Choose…**로 폴더를 지정하면 새 노트는 모두 그 폴더에 `이름.<구분값>.transcript.json`으로 모입니다. 구분값은 녹음 내용으로 만들기 때문에 다른 폴더에 같은 이름의 녹음이 있어도 섞이지 않습니다. 녹음을 열면 지정 폴더를 먼저 찾고, 없으면 녹음 옆을 찾으므로 전에 만든 노트도 그대로 열립니다. **Reset**을 누르면 다시 녹음 옆에 저장합니다.

나중에 다시 볼 때는 **File → Open Recent**(최근 10개)로 엽니다. **File → Show Notes in Finder**는 지금 노트 파일의 위치를 보여 줍니다. 중지하거나 앱을 닫아도 그때까지의 노트가 남고 **Resume**으로 이어서 전사합니다.

## 테마

색·글꼴·모서리 반경은 모두 `voice_notes/theme.py`의 `Theme` 하나에 있고, 스타일시트는 여기서 생성됩니다. 테마를 추가하려면 `Theme(...)`를 만들어 `THEMES`에 넣으면 설정의 **Theme** 목록에 나타나고, 저장하면 다시 시작하지 않아도 바로 바뀝니다. 앱 코드는 색을 직접 쓰지 않고 `theme.current()`에서 읽습니다.

- **Still (yellow)**: 기본. `translator` 앱과 같은 시스템 글꼴, 노란 강조색, 알약 모양 버튼.
- **Classic (orange)**: 이전 디자인(`video` 앱과 동일).

## 개인정보 · 오프라인

- 전사와 요약은 별도 프로세스에서 실행되며, 시작 시 `voice_notes/offline.py`가 루프백 외의 모든 네트워크 연결과 DNS 조회를 차단합니다. Hugging Face도 오프라인 모드로 고정됩니다.
- 인터넷은 설정에서 **Download**를 누를 때 모델 파일을, **Open URL**에 링크를 넣을 때 그 녹음 파일을 받는 데만 쓰입니다. 녹음이나 노트는 전송하지 않습니다.
- `.gitignore`가 녹음과 노트 파일을 제외합니다.

## 모델 선택 구조

설정(톱니바퀴)에서 모델과 실행 장치를 고릅니다. 모델 목록은 `voice_notes/models.py`의 `MODELS` 한 곳에만 있습니다. 모델을 추가하려면 `ModelSpec` 한 줄을 넣으면 화면과 작업 프로세스에 자동으로 반영됩니다.

```python
ModelSpec('distil', 'Distil-Whisper large-v3', 'Systran/faster-distil-whisper-large-v3', '1.5 GB', '…설명…'),
```

다른 실행 방식(예: mlx-whisper)은 `voice_notes/engine.py`에 백엔드 함수를 추가해 `BACKENDS`에 등록하고 `ModelSpec(backend=...)`로 연결합니다.

실행 장치 기본값은 **Automatic**입니다. Apple Silicon에서는 ctranslate2의 Metal(MPS) 백엔드를 먼저 시도하고, 실패하면 CPU로 실행합니다.

## 정확도 측정

공개된 미국 대법원 구두변론(녹음 + 공식 속기록 PDF)으로 비교합니다.

```sh
make testdata CASE=25-5930              # mp3 + 속기록 PDF 다운로드
make transcribe CASE=25-5930 MODEL=turbo
make compare CASE=25-5930 MODEL=turbo   # WER, 자주 틀린 단어, 10분 단위 오류율
```

측정 결과 (2026-11-21, M4 Mac, Maxwell v. Dinis 64분): large-v3-turbo + Mac GPU로 **6분 33초, WER 4.1%**. 대부분의 오류는 더듬은 반복과 대법관들이 끼어드는 겹친 발언이었고, 문장 전체가 누락된 구간은 없었습니다(최장 누락 6단어). 인명 오인식(Preiser → Pryser, Dinis → Dennis)은 설정의 **Terms**로 보완할 수 있습니다.

## 구조

| 파일 | 역할 |
|---|---|
| `voice_notes/app.py` | 화면: 노트 표, 검색, ★/메모, 플레이어, 설정 창 |
| `voice_notes/worker.py` | 작업 프로세스: 오디오 변환 → 전사 → 줄 단위 JSON 이벤트, 20초마다 중간 저장 |
| `voice_notes/engine.py` | 인식 백엔드 (faster-whisper) |
| `voice_notes/speakers.py` | 화자 구분 (sherpa-onnx 분할·임베딩, 같은 목소리 합치기, 줄마다 화자) |
| `voice_notes/summarize.py` | 오프라인 요약 (구간 나누기 · 프롬프트 · MLX 모델) |
| `voice_notes/models.py` | 모델 목록, 다운로드 여부 확인 |
| `voice_notes/offline.py` | 네트워크 차단 |
| `voice_notes/transcript.py` | 노트 JSON 저장/불러오기, 텍스트 내보내기 |
| `voice_notes/theme.py`, `icons.py` | 테마(디자인 토큰 → 스타일시트), 아이콘 |
| `voice_notes/fonts/` | Source Sans 3 (Adobe, SIL Open Font License — 라이선스 파일 포함) |
| `tools/compare_scotus.py` | 대법원 속기록 대비 WER 측정 |
| `tools/compare_speakers.py` | 대법원 속기록 대비 화자 정확도 측정 |
