# VoiceTransl backend

From the repository root, activate the `asmr` environment and run:

```bash
cd backend
PYTHONPATH=. VOICETRANSL_DATA_DIR=../data uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

The service stores projects under `data/projects` and exposes the REST API under `/api/v1`.

The detailed backend design, state transitions, storage invariants, event semantics, and frozen v0.1 decisions are documented in [`docs/voicetransl_v0.1_minimum_implementation.md`](../docs/voicetransl_v0.1_minimum_implementation.md), especially section 23.
