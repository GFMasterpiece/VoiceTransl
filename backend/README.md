# VoiceTransl backend

## Setup and start

Use the `asmr` Python 3.12+ environment. If `conda` or `mamba` has not been
initialized in the current shell, initialize its shell hook first:

```bash
# bash (use `conda shell.zsh hook` for zsh)
eval "$(conda shell.bash hook)"
conda activate asmr
```

For micromamba/mamba, use its equivalent hook and activation command:

```bash
eval "$(mamba shell hook --shell bash)"
mamba activate asmr
```

Install the backend and test dependencies, then start one Uvicorn worker:

```bash
cd backend
python -m pip install -e '.[test]'
PYTHONPATH=. VOICETRANSL_DATA_DIR=../data \
  uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

The service is intentionally an in-process v0.1 service. Keep `--workers 1`:
multiple workers would each have an independent job manager and event bus.

## Minimal REST flow

The API is under `/api/v1`. Upload an audio file (the response contains the
generated project ID), start its job, poll the persisted job state, and list or
download the generated outputs:

```bash
UPLOAD=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/projects \
  -F 'file=@sample.wav' -F 'name=sample')
PROJECT_ID=$(printf '%s' "$UPLOAD" | python -c \
  'import json,sys; print(json.load(sys.stdin)["project"]["id"])')

curl -sS -X POST "http://127.0.0.1:8000/api/v1/projects/$PROJECT_ID/run" \
  -H 'content-type: application/json' -d '{"resume":true}'
curl -sS "http://127.0.0.1:8000/api/v1/projects/$PROJECT_ID/job"
curl -sS "http://127.0.0.1:8000/api/v1/projects/$PROJECT_ID/outputs"
curl -fL "http://127.0.0.1:8000/api/v1/projects/$PROJECT_ID/outputs/zh.srt" \
  -o zh.srt
```

Jobs run asynchronously. Continue polling `/job` until `status` is
`completed`, `failed`, or `cancelled`; WebSocket events are notifications and
the REST job document remains the source of truth.

## Data and logs

Projects are stored in `data/projects/<project-id>/` (or the directory set by
`VOICETRANSL_DATA_DIR`). Each project contains `project.json`, `job.json`,
source/transcript/output artifacts, and `logs/pipeline.log`. The server
diagnostic log is `data/voicetransl.log` beside the `projects/` directory.

The detailed backend design, state transitions, storage invariants, event
semantics, and frozen v0.1 decisions are documented in
[`docs/voicetransl_v0.1_minimum_implementation.md`](../docs/voicetransl_v0.1_minimum_implementation.md),
especially section 23.
