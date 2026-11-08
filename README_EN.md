# VoiceTransl

[简体中文](README.md) | English

VoiceTransl is building a visual audio workflow with ComfyUI-style node connections, independent node settings, and immutable snapshots for each run.

**This is a Web development version with audio passthrough, Mock validation, and Qwen cloud transcription. Translation and media conversion are not available.** The old desktop implementation, GalTransl, plugins, dependencies, and packaging resources have been removed from the current source tree. The [roadmap](docs/roadmap.md) summarizes their history; full implementations remain in Git history.

## Current Features

- Simplified Chinese / English interface switching, browser language detection, and saved preferences. Switching preserves form values, node upload state, and workflow drafts.
- Paginated projects and name-only empty project creation. Select or upload audio and provide its source language in the audio input node; failed uploads can be retried and do not start a run.
- A project-level workflow canvas with independent node settings, typed-port validation, saved revisions, and conflict detection. The backend capability API supplies the node catalog, categories, and settings; the add menu groups nodes by function.
- An Edit menu with undo/redo, multi-selection and group dragging, copy/paste, and workflow JSON import/export. Imports become undoable drafts; audio files and run history remain separate.
- A default `audio_input -> audio_output` graph that copies the original bytes into a separate output without transcoding. Processing nodes are optional.
- Optional Mock preprocessing, Mock ASR, and TXT/SRT/VTT/LRC text exports. ASR produces fixed examples, not recognition of the uploaded audio.
- A separate Qwen ASR node uploads local audio, runs asynchronous transcription, and returns real text and timestamps. Model names are configurable; text previews, subtitle exports, and batches reuse the existing workflow. Edit the connection URL, timeout, and API key directly in the node. Keys are encrypted separately, while runs freeze connection settings and credential versions; existing environment-based connections remain compatible. See [connection setup](docs/provider_profiles.md).
- A separate Qwen Streaming ASR node streams existing PCM16 mono WAV files over WebSocket and reads each file's sample rate. Completed results reuse text previews, subtitle exports, and batches. Each instance owns its endpoint and key; copies require new credentials and never inherit global connections. Microphone capture and live captions are outside this version. See the [streaming specification](spec/cloud-asr-qwen-streaming.md).
- String, integer, number, and boolean value nodes. Backend-declared parameter inputs initially cover ASR language, export filenames, and text format. Connections preserve manual fallback values, while run details record parameter sources and effective values.
- Per-audio runs, cancellation, history, and safe artifact downloads. Runs always use their submitted graph snapshots.
- The audio input node imports files or multiple folders, validates uploads and source languages, and saves an ordered resource list. The shared Run action applies one frozen workflow independently to each file. The batch panel manages progress, cancellation, retries, and ZIP results. New outputs use a prefix and stable sequence, such as `name_00001.wav`; retries retain the original numbers, and existing graphs keep their filenames. Current File Info supplies per-file naming and language parameters.

Other cloud protocols, local models, video processing, node-level resume, cache reuse, and history cleanup are not implemented. [Product goals](docs/product_requirements.md) and [planned work](docs/roadmap.md) are not available features.

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

The project retains the [GNU GPLv3](LICENSE). Respect the license, media copyright, and applicable laws. The license text is unchanged.
