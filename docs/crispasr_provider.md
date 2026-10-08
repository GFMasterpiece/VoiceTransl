# CrispASR Provider

VoiceTransl keeps `mock` ASR as the default. To enable local CrispASR, install a
tested CrispASR CLI and place the GGUF model in a server owned directory. No
model is downloaded by VoiceTransl.

Version support is capability based rather than pinned to a bundled release:
use a CrispASR release whose CLI exposes the flags listed below. The local
runtime package is kept outside Git; the command shape follows
`crispasr/param.txt` and the existing local workflow.

Configure the server in the YAML selected by `VOICETRANSL_CONFIG`, normally
`config/app.yaml`, or with the equivalent environment variables. Relative YAML
paths use the configuration loader's root (the YAML directory's parent, which
is the repository root for `config/app.yaml`):

```yaml
app:
  crispasr_executable: ./crispasr/linux-v0.8.38/crispasr-linux-x86_64-cuda13/crispasr
  crispasr_model_dir: ./crispasr/models
  crispasr_timeout_seconds: 300
```

The topbar **设置** modal can check and save the executable and model directory
globally, subject to local-management and allowed-root restrictions. Saving
requires a manual backend restart to apply; the current process and its running
or queued jobs keep their active settings. Environment-managed fields are read-only
(`VOICETRANSL_CRISPASR_EXECUTABLE` and `VOICETRANSL_CRISPASR_MODEL_DIR`). See
[Global local-ASR settings](voicetransl_global_settings.md) for the API, path rules,
WSL deployment guidance, and restart procedure.
Settings responses use config-relative paths or authorized-root aliases such as
`@root/1/models` to keep private absolute directories on the backend. These
representations preserve the actual runtime paths and their allowed-root limits.

This phase does not discover or list models, add an ASR model dropdown, or detect
language automatically. Language remains manual. The directory check only tests
path validity/readability; it does not run model inference or prove ASR readiness.

Set a project's ASR provider to `crispasr`, choose a model filename already in
that directory, and select a supported backend. The provider invokes the CLI
without a shell using the verified flags `--backend`, `--model`, `--language`,
`--output-srt`, `--output-file`, `--file`, and (when VAD is enabled)
`--vad --vad-max-speech-duration-s`. The chunk duration maps to the latter VAD
flag. Client values are identifiers only; executable and model paths remain
server configuration.

The executable must write an SRT (or JSON transcript) to the requested output
base. SRT cues and JSON segments are validated for non-empty text, finite
non-negative timestamps, and positive time ranges. Temporary output files are
unique per attempt and removed after parsing. Missing executable/model, process
failure, timeout, cancellation, missing output, and malformed output produce
stable job error codes.

The local v0.8.38 package is expected at the paths above. Optional smoke test:
upload a short normalized WAV, select `crispasr`, and run the project. If the
executable or model is missing, the job reports a stable diagnostic instead of
silently falling back to Mock. Unit tests use fake subprocesses and do not
require the runtime package or model.
