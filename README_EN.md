# VoiceTransl

[简体中文](README.md) | English

VoiceTransl is building a visual audio workflow with ComfyUI-style node connections, independent node settings, and immutable snapshots for each run.

**This is a Web development version with audio passthrough, Mock validation, Qwen cloud transcription, and LLM API translation. Media conversion is not available.** The old desktop implementation, GalTransl, plugins, dependencies, and packaging resources have been removed from the current source tree. The [roadmap](docs/roadmap.md) summarizes their history; full implementations remain in Git history.

## Current Features

- LLM translation uses a Chat Completions compatible API. LLM service, LLM model, and execution-option nodes provide reusable settings. Enter context directly in the translation node, or connect a string/join to the socket beside its editor; connected editors hide and retain their manual fallback. Preview, export, and chained translation consume immediate upstream current text while preserving originals and timing. See [translation behavior](docs/translation_providers.md).

- Simplified Chinese / English interface switching, browser language detection, and saved preferences. Switching preserves form values, node upload state, and workflow drafts.
- Paginated projects and name-only creation. Import audio with its source language in the Project Media sidebar while keeping the canvas accessible; uploading does not start processing. Ordered audio groups share static background and can reuse the same assets.
- Source languages offer searchable suggestions by Chinese or English name, native name, code, or alias, with custom codes also supported.
- [Global settings](docs/global_settings.md) control upload/run concurrency and creation templates for connection, model, composition, and option nodes. Default keys are materialized for new connection owners; existing graphs and runs do not read later defaults.
- A project-level workflow canvas with independent node settings, typed-port validation, saved revisions, and conflict detection. The backend capability API supplies the node catalog, categories, and settings; the add menu groups nodes by function.
- An Edit menu and node context menu with undo/redo, multi-selection and group dragging, copy/cut/paste, and workflow JSON import/export. Cuts can be undone in one step; audio files and run history remain separate.
- A default `audio_input -> audio_output` graph that copies the original bytes into a separate output without transcoding. Processing nodes are optional.
- Debug mode exposes Mock preprocessing and Mock ASR, which produces fixed examples. Text outputs support TXT/SRT/VTT/LRC exports.
- Qwen Filetrans uploads and transcribes audio with real timestamps. Explicit ASR service, ASR model, and option inputs can be shared across ASR consumers. ASR has no context input; LLM and ASR ports cannot be mixed. Keys belong to connection owners, are encrypted separately, and are frozen by version. No Profile or environment fallback. See [node connections](docs/node_connections.md).
- Qwen Streaming sends existing PCM16 mono WAV files over WebSocket at their real sample rate. It consumes separate reusable model/connection inputs and publishes final text for preview/export. Copying a connection clears its key. No transcoding, microphone capture, or live-caption UI.
- Multiline text, integer, number, boolean, and JSON values; ordered text joining and explicit top-level JSON merging. Backend-declared typed fields preserve manual fallbacks and record effective values. String joins support adding, removing, and reordering inputs with stable port IDs. Models and execution options remain reusable nodes.
- Per-audio runs, cancellation, history, and safe artifact downloads. Runs always use their submitted graph snapshots.
- Multiple audio inputs can each bind a group and processing path. Run This Input and Run Canvas submit once; the backend freezes and expands the work. Repeated assets retain each path without concurrent processing of the same audio. Continuous groups wait for each predecessor to complete. Group Info explicitly supplies static translation background; automatic story memory is not implemented. Batches support cancellation, explicit retries, ZIP downloads, and stable output numbering. See [audio groups](spec/project-audio-groups.md).

Select a finished historical run, then right-click a node to rerun it and its descendants using the saved graph. Configuration ancestors are rematerialized; required processing results are verified and copied without repeating reused ASR requests; invalid results require an earlier starting node. Individual batch items retain their sequence numbers. See the [rerun specification](spec/node-rerun-reuse.md).

Other cloud protocols, local models, video processing, whole-batch node reruns, automatic cache scheduling, and history cleanup are not implemented. [Product goals](docs/product_requirements.md) and [planned work](docs/roadmap.md) are not available features.

Graphs, runs, batches, and default settings use schema 4.0, the capability catalog uses 1.19, and audio groups use 1.0. Existing 4.0 input bindings retain their meaning; v1–v3 graphs, runs, and templates are not loaded or migrated. Existing audio remains usable. See the [service families and field inputs contract](spec/service-families-field-inputs.md).

## Development Requirements

- Python 3.12 or newer in an activated isolated environment, such as venv or Conda.
- Node.js 24 and npm. Install frontend dependencies using the committed lockfile.
- The current Web skeleton requires no local models, FFmpeg, llama.cpp, or desktop dependencies.

From the repository root, with the Python environment activated:

```text
cd backend
python -m pip install -e ".[test]" ruff "uvicorn[standard]"
```

From `frontend/`:

```text
npm ci
```

On Windows PowerShell, use `npm.cmd` for these npm commands if script execution policy blocks `npm`.

## Start

On Windows, double-click `scripts/start-dev.cmd` to restart the backend and frontend in separate windows and open the local UI. The local launcher uses backend port `8000` and frontend port `5180`: it stops the existing project services, waits for their ports to be released, and starts them again. Unrelated listeners or insufficient permissions produce an error; the script never terminates all Python/Node processes by name. The `scripts/` directory is ignored by Git as a local script directory; after cloning the repository, place the launcher and its helper files there manually. The script uses the `asmr` Conda environment by default. Set `VOICETRANSL_CONDA_ENV` for another environment, or set `VOICETRANSL_CONDA_COMMAND` to the path of `conda.bat` or `conda.exe` when Conda is not on `PATH`.

Use `scripts/start-dev.cmd --stop` to stop these development services without restarting. Restarting interrupts local tasks; the launcher first attempts a normal shutdown, then terminates remaining project processes.

Alternatively, use two terminals to start services manually. Start the backend from `backend/`, keeping a single process and worker:

```text
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Start the frontend from `frontend/`:

```text
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

- UI: [http://127.0.0.1:5173](http://127.0.0.1:5173/)
- Health check: [http://127.0.0.1:8000/api/v1/health](http://127.0.0.1:8000/api/v1/health)
- Interactive API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

Click the connection status on the home page to select another backend. The address accepts a service root or a prefixed `/api/v1` URL, is saved in this browser after a successful check, and takes effect on reload. Reset restores the deployment default or Vite proxy. Loopback frontend origins work across origins by default; configure other frontend origins through backend `app.cors_origins` or `VOICETRANSL_CORS_ORIGINS`, such as `https://ui.example.com`. See the [connection specification](spec/backend-connection.md).

Vite proxies `/api` to the local backend. This is a local development setup, not a production deployment; do not expose it directly to the public internet. Multiple workers would have independent in-memory schedulers and are unsupported.

## Configuration and Data

Service settings live in `config/app.yaml`. Set `VOICETRANSL_CONFIG` to use another file. Data defaults to `data/` at the repository root; `VOICETRANSL_DATA_DIR` overrides it. Relative data paths in environment variables resolve against the launch directory, so absolute paths are recommended.

The backend stores projects, audio, graph revisions, run snapshots, and artifacts on the filesystem without a database. Source cleanup does not delete or automatically migrate existing data. Keep `data/`, `scripts/`, personal `.codex/` settings, secrets, and local environment files out of Git.

## Validation

From `backend/`:

```text
python -m pytest -q
ruff check app tests
ruff format --check app tests
```

From `frontend/`:

```text
npm test -- --run
npm run build
```

Web CI is configured for backend tests and Ruff on Linux/Windows, plus frontend tests and builds on Linux. It no longer packages or releases the desktop app. Configuration does not prove a successful remote run; check Actions for results. Symlink tests may skip when the Windows account lacks symlink privileges, leaving that scenario unverified.

## Structure and Documentation

| Path | Purpose |
| --- | --- |
| `backend/` | FastAPI service, node registry, storage, execution, and tests |
| `frontend/` | Vue 3 UI, workflow canvas, and tests |
| `config/` | Service settings |
| `docs/` | Current architecture, behavior, product goals, and roadmap |
| `spec/` | Feature scope, status, and acceptance criteria; Draft is not approval or implementation |

- [Architecture](docs/architecture.md) / [Backend development](backend/README.md)
- [Multi-audio resources and API](docs/voicetransl_multi_audio_project.md) / [Frontend workflow](docs/frontend_workflow.md)
- [Roadmap](docs/roadmap.md) / [Specification index](spec/README.md)
- [Repository collaboration rules](AGENTS.md)

## Origins and License

This project historically derives from [shinnpuru/VoiceTransl](https://github.com/shinnpuru/VoiceTransl) and [GalTransl](https://github.com/xd2333/GalTransl). Thanks to [shinnpuru](https://github.com/shinnpuru), [MurthiNext](https://github.com/MurthiNext), and other original contributors. Earlier implementations remain traceable through Git history; the current Web version does not promise the upstream desktop features.

The project retains the [GNU GPLv4](LICENSE). Respect the license, media copyright, and applicable laws. The license text is unchanged.
