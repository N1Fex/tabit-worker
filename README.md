# Audio to Guitar Tab MusicXML

CLI utility and background worker that take a monophonic WAV or MP3 file, transcribe it to notes, place them on a standard-tuned guitar fretboard, and export `MusicXML` with tabulature.

## Capabilities

- Default transcription engine: `pyin` for monophonic melody extraction.
- Optional engine: `Basic Pitch` via `--backend basic-pitch`.
- Automatic BPM detection is enabled by default.
- You can still override tempo manually with `--bpm 95`.
- Input format: PCM WAV (`mono` or `stereo`, 16-bit or 32-bit integer) and MP3.
- MP3 decoding uses local audio loading and falls back to local `ffmpeg.exe` when needed.
- `ffmpeg.exe` is auto-detected in standard Windows locations and in `tools/ffmpeg/bin/ffmpeg.exe`.
- Short note artifacts are merged before tablature generation.
- Measures are always padded to full `4/4` duration in the generated `MusicXML`.
- RabbitMQ worker can consume conversion jobs, fetch audio from MinIO, upload generated `MusicXML`, and publish result messages.
- Worker automatically reads variables from `.env` if `python-dotenv` is installed.

## Run CLI

```bash
python -m tabit_worker input.wav output.musicxml
python -m tabit_worker input.mp3 output.musicxml
python -m tabit_worker input.wav output.musicxml --bpm 100
python -m tabit_worker input.wav output.musicxml --backend basic-pitch
```

## Run Worker

Incoming queue: `queue.convert.job`

Expected job payload:

```json
{"jobId":"...","userId":"...","objectKey":"...","uuid":"..."}
```

Result queue: `queue.result`

Published statuses:
- immediately after taking the job: `PROCESSING`
- after successful upload: `COMPLETED`
- on any failure: `FAILED`

Result payload format:

```json
{"jobId":"...","userId":"...","resultObjectKey":"userId/uuid.musixml","status":"COMPLETED","timestamp":"2026-03-08T12:00:00Z"}
```

For `PROCESSING` and `FAILED`, `resultObjectKey` is `null`.

### Variant 1: `.env`

Copy [.env.example](/D:/Projects/Pycharm/tabit-ai-worker/.env.example) to `.env` and fill in your real values.

Then run without arguments:

```bash
python -m tabit_worker.worker
```

Or directly:

```bash
python tabit_worker/worker.py
```

### Variant 2: explicit arguments

```bash
python -m tabit_worker.worker \
  --rabbitmq-url amqp://guest:guest@localhost:5672/%2F \
  --minio-endpoint localhost:9000 \
  --minio-access-key minioadmin \
  --minio-secret-key minioadmin
```

The worker uses these defaults if you do not override them:

- source bucket: `audio-temp`
- result bucket: `musicxml`
- job queue: `queue.convert.job`
- result queue: `queue.result`
- BPM: `auto`
- backend: `pyin`

The same settings can also be provided through environment variables:

```text
RABBITMQ_URL
MINIO_ENDPOINT
MINIO_ACCESS_KEY
MINIO_SECRET_KEY
MINIO_SECURE
MINIO_BUCKET_AUDIO_TEMP
MINIO_BUCKET_MUSICXML
RABBITMQ_JOB_QUEUE
RABBITMQ_RESULT_QUEUE
```

## Requirements

Install dependencies from `pyproject.toml`.
If MP3 decoding fails on your machine, place `ffmpeg.exe` here:

```text
D:\Projects\Pycharm\tabit-ai-worker\tools\ffmpeg\bin\ffmpeg.exe
```