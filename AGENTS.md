# Repository Instructions

## Repository layout

This repository contains:

- The existing desktop translation application in `app.py`, `GalTransl/`, and related scripts.
- The VoiceTransl backend in `backend/`.
- The Vue 3 frontend in `frontend/`.
- Runtime configuration and provider presets in `config/`.
- Backend and workflow contracts in `docs/`.

Confirm which area a task targets before editing. Do not refactor the legacy desktop application while working on the backend unless explicitly requested.

## General rules

- Preserve existing behavior outside the requested scope.
- Do not add a database, Redis, Celery, or external worker system for the v0.1 backend.
- Do not commit secrets, API keys, authorization headers, local environment files, or machine credentials.
- Use UTF-8 for source files and JSON.
- Prefer small, reviewable changes.
- Do not use destructive Git commands unless explicitly requested.
- Do not modify frontend files unless the task explicitly includes frontend work.

## VoiceTransl source of truth

For the current project/audio resource, filesystem, API, and persistence contract,
follow `docs/voicetransl_multi_audio_project.md`. The v0.1 contract in
`docs/voicetransl_v0.1_minimum_implementation.md` remains the legacy single-audio
baseline; the multi-audio contract supersedes its resource granularity. For the current
configuration-first frontend workflow, follow
`docs/voicetransl_v0.2_frontend_workflow.md`. Provider-specific behavior is
documented in `docs/crispasr_provider.md` and
`docs/translation_providers.md`; reusable connection profiles are described
in `docs/provider_profiles.md`.

The VoiceTransl architecture keeps these decisions:

- The filesystem is the only persistent data source.
- One project maps to one independent project directory.
- One project contains one or more audio resources. Each audio owns its job,
  progress, transcripts, outputs, and immutable run configuration snapshot.
- A project has a common default workflow configuration; selected audios may
  receive individual overrides. Sharing configuration does not merge timelines.
- FastAPI and Pydantic are used for the HTTP service and domain models.
- The pipeline consists of `preprocess`, `asr`, `alignment`, `translation`, and `export`.
- Stage results and job state are persisted after each stage.
- WebSocket events notify clients; REST and files remain the source of truth.
- New projects default to Mock providers, while real providers are available when
  the server is configured:
  - preprocessing: `mock` or local `ffmpeg`;
  - ASR: `mock` or local `crispasr`;
  - alignment: currently Mock/fallback behavior;
  - translation: `mock`, OpenAI-compatible Chat Completions
    (`openai_chat`), OpenAI Responses (`openai_responses`), or Anthropic
    Messages (`anthropic_messages`);
  - export: SRT, VTT, and LRC.
- CrispASR executables and models are local runtime dependencies, not downloaded
  or committed by the repository.
- HTTP translation API keys are read only from environment variables named by
  `translation.api_key_env`; never store key values in project configuration.

### Current frontend scope

- The frontend already provides project listing, project detail/workspace,
  pipeline progress and stage inspection, configuration validation, deletion,
  cancellation, and completed-project reprocessing.
- Multiple-audio selection/drop and the validated upload queue create one project
  with multiple audio resources. Failed uploads may be retried individually in
  the same project without recreating successful resources. Uploading never
  starts the pipeline automatically.
- In a project workspace, select one, several, or all audios, configure workflow
  nodes, apply the configuration to the selection, and start independent jobs.
  Each audio remains individually inspectable, resumable, and cancellable.
- The node configuration modal exposes HTTP translation `base_url` and
  `api_key_env`; the real API key remains backend-only and is read from the
  named environment variable.

## Backend invariants

- Project and job JSON files include `schema_version`.
- Stored paths are project-relative POSIX paths.
- JSON state is written atomically.
- Providers do not modify project or job state directly.
- Translation providers do not modify segment IDs, source text, or time ranges.
- Source files and manually edited transcript files are not silently overwritten.
- Interrupted jobs remain visible and resumable from the first incomplete stage.
- The same audio cannot run two jobs concurrently; different audios in a project
  may run or queue independently under the global concurrency limit.
- Deleting a project with ANY running or queued audio is rejected as `PROJECT_BUSY`; completed,
  failed, cancelled, and interrupted projects may be deleted with their stored
  artifacts.
- Reprocessing a completed audio creates a new Job ID and configuration
  snapshot; it must not mutate or resume the old completed job.
- A running job uses its immutable configuration snapshot even if the editable
  project configuration changes later.

## Development and validation

- Use the `asmr` environment for backend development.
- Backend code requires Python 3.12 or newer.
- Run backend commands from `backend/`.
- For a local full-stack smoke run, keep the personal convenience script
  `scripts/start-dev.sh` untracked and run it after `mamba activate asmr` and
  installing frontend dependencies. It starts one backend worker on port 8000
  and Vite on port 5173; do not add this personal script to commits unless the
  user explicitly asks.
- Run the relevant existing tests when changing the legacy application or
  frontend.
- Run the backend test suite before reporting backend work as complete:

```bash
cd backend
PYTHONPATH=. python -m pytest -q
```

For backend linting/format verification, run `ruff check app tests` and
`ruff format --check app tests`. For frontend changes, run from `frontend/`:

```bash
npm test -- --run
npm run build
```

A change is complete only when affected tests pass, the API behavior matches the documented contract, persistent files remain recoverable after restart, and the final report names the validation command and result.

## Safety and compatibility

- Never accept arbitrary local paths or shell commands from API clients.
- Reject path traversal in project and output paths.
- Do not commit generated runtime data or local environment files.
- Keep user-facing errors understandable; keep detailed tracebacks in server logs.
- Preserve behavior outside the requested scope and avoid unrelated refactors.

## Git workflow

- Follow the minimal commit principle.
- Create a commit only for a coherent, reviewable change or a requested checkpoint.
- Do not create separate commits for trivial intermediate edits.
- Keep unrelated changes out of commits.
- Local commits are allowed.
- Never proactively run `git push`.
- Only push when the user explicitly requests it in the current task.

## Documentation

- Treat `docs/voicetransl_multi_audio_project.md` as the current project/audio
  contract; retain `docs/voicetransl_v0.1_minimum_implementation.md` for legacy
  pipeline and single-audio compatibility details.
- Treat `docs/voicetransl_v0.2_frontend_workflow.md` as the source of truth for
  configuration-before-run, project lifecycle, and multi-audio selection/upload
  behavior.
- Keep CrispASR setup and HTTP translation protocol details in their dedicated
  provider documents instead of duplicating them here.
- Update relevant documentation when changing a frozen architecture decision, file format, or API contract.
- 工作结束前检查是否需要同步文档：若改动影响用户可见行为、API、配置、数据格式、架构约定、限制或开发流程，必须更新对应文档；纯内部重构或不改变行为的测试修复无需改文档。若确认无需更新，在最终报告中简要说明。
- Keep detailed implementation information in code or dedicated documentation instead of expanding this file unnecessarily.

## Agent workflow

- When implementation work is requested, delegate code changes to a sub-agent using the `gpt-6.1-sol` model.
- The coordinating agent owns task decomposition, design decisions, integration, code review, and validation.
- The coordinating agent should not directly write implementation code when a suitable sub-agent is available.
- Choose the sub-agent reasoning effort according to task complexity.
- Pure discussion, planning, read-only inspection, review, and final verification do not need to be delegated.
