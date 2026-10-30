# VoiceTransl backend

FastAPI backend for the current Web node workflow. The default graph copies
audio bytes; optional Mock nodes generate example results. Real ASR, translation,
and media conversion are not available through the current workflow.

## Setup and start

Activate an isolated Python 3.12+ environment (venv or Conda), then run these
commands from `backend/`:

```text
python -m pip install -e ".[test]" ruff "uvicorn[standard]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Keep a single process and worker: each process has an independent in-memory
scheduler and event bus. This local development setup is not a production
deployment. No desktop requirements, local models, or external media tools are
needed. See the [root README](../README.md) for frontend setup.

## Current REST flow

Interactive documentation is available at
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
All paths below use the `/api/v1` prefix. Replace placeholders with server-returned
IDs and revisions; do not infer artifact URLs from file names.

| Step | Request and behavior |
| --- | --- |
| Discover limits and nodes | `GET /upload-policy` and `GET /capabilities` |
| Create a project | `POST /projects`, multipart `file`, `source_language`, `target_language`, optional `name`; returns `{project,job}` without starting execution |
| Select audio | Use the selected member of `project.audios` and its `id`; each audio has independent runs |
| Read the graph | `GET /projects/{pid}/workflow`; an unsaved project returns the default passthrough graph |
| Validate edits | `POST /projects/{pid}/workflow/validate` with `{nodes,edges}` |
| Save edits | `PUT /projects/{pid}/workflow` with `{expected_revision,nodes,edges}`; use the current document's `revision` for optimistic locking |
| Start a run | `POST /projects/{pid}/audios/{aid}/workflow-runs` with `{workflow_revision}` from the graph response; returns 202 and a new run |
| Poll execution | `GET /projects/{pid}/audios/{aid}/workflow-runs/{rid}`; REST is the current execution state source, not the legacy Job WebSocket |
| Read history | `GET /projects/{pid}/audios/{aid}/workflow-runs?limit=20&offset=0` |
| Cancel | `POST /projects/{pid}/audios/{aid}/workflow-runs/{rid}/cancel` |
| Download | Use a completed node's structured artifact metadata and `download_url` |

Project source and target languages must be explicit, nonempty, and different.
They do not enable real translation. Mock ASR uses its own example-language
setting and does not transcribe the source file.

The default graph can run without being saved first, using its returned
`workflow_revision`. Runs keep immutable snapshots; edits affect new runs only.
Terminal run states are `completed`, `partial`, `failed`, `cancelled`, and
`interrupted`. Poll until terminal; cancellation must finish resource cleanup.
Interrupted runs cannot resume and require a new run.

The old fixed-stage `/run`, transcript, and output routes are retired. Legacy
`/job` and configuration GET routes exist only for compatibility; they are not
the current execution flow. Configuration PUT is read-only. Profile metadata
CRUD and retained translation adapter code do not mean real providers are
integrated into the current node workflow.

## Configuration, data, and logs

The default service configuration is `config/app.yaml` at the repository root.
`VOICETRANSL_CONFIG` selects another file. Its `app.data_dir` defaults to the
repository's `data/`; `VOICETRANSL_DATA_DIR` overrides it. Relative environment
paths resolve against the launch directory; prefer an absolute path when
overriding the data root.

Projects live under `data/projects/<project-id>/`. Project metadata, source
files, workflow revisions, per-audio run snapshots, and node artifacts persist
on the filesystem. Diagnostic logs go to `data/voicetransl.log`. Legacy records
stay readable within the supported schema version and are not automatically
migrated or deleted. Data, local environment files, and secrets must not enter
Git.

See [architecture](../docs/architecture.md),
[multi-audio resources and API](../docs/voicetransl_multi_audio_project.md), and
[Profile compatibility](../docs/provider_profiles.md) for the authoritative
contracts. The [translation adapter document](../docs/translation_providers.md)
describes retained code, not a usable workflow node.

## Validation

Run from `backend/`:

```text
python -m pytest -q
ruff check app tests
ruff format --check app tests
```

CI is configured for Python 3.12 on Linux and Windows. Check actual Actions
results before claiming a remote platform is verified. On Windows, symlink
tests may skip if the account cannot create symbolic links.
