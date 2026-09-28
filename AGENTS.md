# Repository Instructions

## Repository layout

This repository contains:

- The existing desktop translation application in `app.py`, `GalTransl/`, and related scripts.
- The VoiceTransl v0.1 backend in `backend/`.
- Backend design and acceptance requirements in `docs/voicetransl_v0.1_minimum_implementation.md`.

Confirm which area a task targets before editing. Do not refactor the legacy desktop application while working on the backend unless explicitly requested.

## General rules

- Preserve existing behavior outside the requested scope.
- Do not add a database, Redis, Celery, or external worker system for the v0.1 backend.
- Do not commit secrets, API keys, authorization headers, local environment files, or machine credentials.
- Use UTF-8 for source files and JSON.
- Prefer small, reviewable changes.
- Do not use destructive Git commands unless explicitly requested.
- Do not modify frontend files unless the task explicitly includes frontend work.

## Backend source of truth

For backend work, follow `docs/voicetransl_v0.1_minimum_implementation.md`.

The backend keeps these architecture decisions:

- The filesystem is the only persistent data source.
- One project maps to one independent project directory.
- FastAPI and Pydantic are used for the HTTP service and domain models.
- The pipeline consists of `preprocess`, `asr`, `alignment`, `translation`, and `export`.
- Stage results and job state are persisted after each stage.
- WebSocket events notify clients; REST and files remain the source of truth.
- v0.1 uses Mock providers and does not require real ASR, alignment, or external translation.

## Backend invariants

- Project and job JSON files include `schema_version`.
- Stored paths are project-relative POSIX paths.
- JSON state is written atomically.
- Providers do not modify project or job state directly.
- Translation providers do not modify segment IDs, source text, or time ranges.
- Source files and manually edited transcript files are not silently overwritten.
- Interrupted jobs remain visible and resumable from the first incomplete stage.
- The same project cannot run two jobs concurrently.

## Development and validation

- Use the `asmr` environment for backend development.
- Backend code requires Python 3.12 or newer.
- Run backend commands from `backend/`.
- Run the relevant existing tests when changing the legacy application.
- Run the backend test suite before reporting backend work as complete:

```bash
cd backend
PYTHONPATH=. python -m pytest -q
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

- Treat `docs/voicetransl_v0.1_minimum_implementation.md` as the backend design source of truth.
- Update relevant documentation when changing a frozen architecture decision, file format, or API contract.
- Keep detailed implementation information in code or dedicated documentation instead of expanding this file unnecessarily.

## Agent workflow

- When implementation work is requested, delegate code changes to a sub-agent using the `gpt-6-sol` model.
- The coordinating agent owns task decomposition, design decisions, integration, code review, and validation.
- The coordinating agent should not directly write implementation code when a suitable sub-agent is available.
- Choose the sub-agent reasoning effort according to task complexity.
- Pure discussion, planning, read-only inspection, review, and final verification do not need to be delegated.
