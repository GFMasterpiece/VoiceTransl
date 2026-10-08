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

Local model discovery is available in the ASR configuration modal. Language
remains manual. The global directory check only tests path validity/readability;
neither that check nor model discovery runs inference or proves ASR readiness.

Set a project's ASR provider to `crispasr`, choose a model filename already in
that directory, and select a supported backend. The provider invokes the CLI
without a shell using the verified flags `--backend`, `--model`, `--language`,
`--output-srt`, `--output-file`, `--file`, and (when VAD is enabled)
`--vad --vad-max-speech-duration-s`. The chunk duration maps to the latter VAD
flag. Client values are identifiers only; executable and model paths remain
server configuration.

## Local model discovery

`GET /api/v1/settings/local-asr/models` uses the same local-only access rules as
global settings and takes no user-supplied path. Any query parameters are rejected
with `422 MODEL_DISCOVERY_ARGUMENTS_NOT_ALLOWED`. It scans the **active runtime**
model directory, not a newly saved directory awaiting backend restart. Paths in
the response use config-relative or authorized-root alias representations.

The response contains:

| Field | Meaning |
| --- | --- |
| `provider` | Always `crispasr` |
| `executable` | CLI probe result: `ok`, nullable `version`, and `message` |
| `model_dir` | Active directory in privacy-preserving form |
| `restart_required` | Whether saved settings differ from the active runtime |
| `backends` | Intersection of VoiceTransl-supported and CLI-compiled backends |
| `items` | `model`, nullable `backend`, `size_bytes`, nullable `architecture`, `selectable`, and nullable `reason` for each discovered file |
| `truncated`, `warnings` | Scan limits or diagnostics; do not assume the list is exhaustive when truncated |

Discovery reads shallow `.gguf` metadata to identify a compatible backend. It
does not recurse, load weights, access the GPU, run inference, download models,
or detect language. Unknown, damaged, or incompatible files remain visible but
disabled with a reason. `selectable` indicates metadata and CLI compatibility,
not successful model loading or GPU/inference readiness.

The initial metadata mapping is `general.architecture = qwen3asr` → `qwen3`.
Other architectures remain disabled when their mapping cannot be confirmed;
this does not remove existing manually configured backend support. The CLI is
probed only with fixed `--version` and `--list-backends-json` commands, not once
per model. Discovery excludes symlinks, non-regular files and unsafe filenames.
The directory scan is bounded to 4,096 entries, 128 model files and five seconds;
each GGUF parse is limited to two seconds and 16 MiB of metadata/descriptors.
Additional parser bounds cap metadata entries, tensors, arrays, strings and
nesting. These are scan budgets; CLI probes have their own time and output limits.
Cancelled scans receive a stop signal and are joined before another scan starts.
Limits produce diagnostics instead of unbounded reads. Tensor offsets are checked
against file size without reading weights; this is not full model integrity validation.

The CrispASR ASR modal fetches once when opened and offers an explicit refresh.
Explicitly choosing a
selectable file sets its model filename and matching backend in the draft. It
never selects the first result automatically, saves the configuration
automatically, or changes language. Existing and manually entered model values
remain available; a refreshed list does not replace them. Manual model input
must remain a single trusted filename. Running or queued audio allows viewing
and refreshing discovery while model fields and selection remain read-only.
Directory, CLI, timeout, malformed-response and network failures have separate
diagnostics and point users to global settings; pending-restart notices show
the active directory in privacy-preserving form.

The supported backend set includes `qwen3` for the general 0.6B family. Existing
`qwen3-1.7b` parameters remain supported rather than being rewritten to `qwen3`.

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
