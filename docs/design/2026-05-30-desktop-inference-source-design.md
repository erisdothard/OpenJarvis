# Desktop inference-source selection — design

**Date:** 2026-05-30
**Status:** Approved (brainstorming)
**Component:** `frontend/` (Tauri desktop app), boot sequence in `frontend/src-tauri/src/lib.rs`

## Problem

A user reported two defects in the macOS desktop app:

1. **Runaway model downloads.** The app "started with just a tiny qwen and carries
   on downloading larger and larger models in the background ... had to delete the
   app as there was no stopping it." The boot sequence's "Phase 4" walked the entire
   Qwen3.5 ladder that fit in RAM (up to `qwen3.5:122b` ≈ 81 GB) and pulled every
   model in an un-cancellable background task.
2. **No custom OpenAI-compatible endpoints.** "Does not work with openai compatible
   endpoints (no LM Studio or custom servers)." The desktop boot path hardwires a
   bundled Ollama; there is no way to point the app at a self-hosted server, even
   though the engine layer already supports OpenAI-compatible backends.

The runaway loop itself was removed in PR #446. This spec covers the remaining,
related work: pulling exactly **one** sensible default model, and adding **custom
endpoint** support while keeping Ollama the default.

## Goals

1. On the default (Ollama) path, download exactly **one** model — the second-largest
   that fits in RAM — and nothing else.
2. Let a user point the desktop app at a custom OpenAI-compatible server (LM Studio
   or any base URL), which **skips Ollama entirely** (no launch, no download).
3. Keep Ollama the zero-config default: a fresh install just works with no choice
   screen.

## Non-goals (YAGNI)

- Cloud-provider selection (OpenAI / Anthropic / Gemini / OpenRouter) and API-key
  onboarding. Cloud keys remain handled by the existing Settings flow
  (`save_cloud_key` → `~/.openjarvis/cloud-keys.env`).
- A blocking first-run source picker.
- Live engine switching without an app restart.
- LM Studio / server auto-discovery.

## Background — current behavior

`boot_backend` in `frontend/src-tauri/src/lib.rs` runs at app launch:

- **Phase 1:** start the bundled Ollama, wait for `:11434` to answer.
- **Phase 2:** pull `STARTUP_MODEL` (`qwen3.5:4b`), falling back to `FALLBACK_MODEL`
  (`qwen3:0.6b`) if that fails.
- **Phase 3:** `uv sync`, then `uv run jarvis serve --port 8000 --model <m> --agent simple`.
- **Phase 4 (removed in #446):** background-pulled every other model that fit in RAM.

Relevant existing machinery this design reuses:

- `models_that_fit()` → ascending list of Qwen3.5 tags whose `min_ram` ≤ system RAM.
- `preferred_model()` → currently "third-largest that fits" (to be replaced).
- `pull_model()` / `ollama_has_model()` / Tauri `pull_ollama_model`, `delete_ollama_model`.
- `save_cloud_key(key_name, key_value)` Tauri command → `~/.openjarvis/cloud-keys.env`,
  injected into the `jarvis serve` child via `read_cloud_keys()`.
- `SetupStatus` struct (`phase`, `ollama_ready`, `model_ready`, `detail`, `error`)
  drives `frontend/src/components/SetupScreen.tsx`.
- `jarvis serve` accepts `--engine/-e` and `--model/-m`; OpenAI-compatible engines
  (`lmstudio` @ `http://localhost:1234/v1`, `vllm`, `sglang`, `llamacpp`, `mlx`, …)
  read their base host from `JarvisConfig` (`[engine.<key>] host=`).

## Design

### 1. Source config — `~/.openjarvis/inference.json`

A small JSON file, owned by the desktop app, is the single source of truth for the
chosen inference source. It is read by the boot sequence and written by Settings.
**API keys are never stored here** — they stay in `~/.openjarvis/cloud-keys.env`.

```json
{
  "kind": "ollama",                          // "ollama" | "custom"
  "model": "qwen3.5:9b",                     // ollama tag, or model id for custom
  "base_url": "http://localhost:1234/v1",    // custom only
  "engine": "lmstudio"                        // custom only; an OpenAI-compat engine key
}
```

- **Absent** or `kind == "ollama"` → default Ollama path. A fresh install has no file,
  so first launch is the zero-config Ollama experience with no choice screen.
- `kind == "custom"` → custom-endpoint path.
- For `ollama`, `model` is optional; when omitted, boot computes the default
  (Section 2) and writes it back so the selection is stable and visible in Settings.

### 2. Fix #1 — single default model

Replace `preferred_model()`'s "third-largest" rule with **second-largest that fits**:

```
default_local_model(ram):
    fitting = models_that_fit(ram)        // ascending
    if fitting.len() >= 2: return fitting[fitting.len() - 2]
    if fitting.len() == 1: return fitting[0]
    return FALLBACK_MODEL
```

Phase 2 pulls **only** the result of `default_local_model(...)` (or the model named in
`inference.json` if the user pinned one). `STARTUP_MODEL` is retained only as a floor /
fallback constant. The ladder-walk is already gone (#446); no other model is pulled.

Worked examples (using the existing `QWEN35_MODELS` table):

| System RAM | Fits (ascending)              | Second-largest (default) |
|-----------:|-------------------------------|--------------------------|
| 8 GB       | 0.8b, 2b, 4b                  | `qwen3.5:2b`             |
| 16 GB      | 0.8b, 2b, 4b, 9b              | `qwen3.5:4b`             |
| 32 GB      | 0.8b, 2b, 4b, 9b, 27b         | `qwen3.5:9b`             |
| 96 GB+     | … up to 122b                  | `qwen3.5:35b`            |

### 3. Fix #2 — boot branching

`boot_backend` reads `inference.json` first and branches:

- **ollama (default):**
  1. Launch Ollama, wait for `:11434` (Phase 1, unchanged).
  2. Resolve the model: pinned `inference.json.model` else `default_local_model(ram)`.
     Pull it if absent (Phase 2). Persist the resolved tag back to `inference.json`.
  3. `jarvis serve --engine ollama --model <m> --agent simple` (Phase 3, unchanged).

- **custom:**
  1. **Skip Phases 1 & 2 entirely** — Ollama is neither launched nor pulled.
  2. Health-check `base_url` (HTTP GET on the OpenAI-compatible `/models` or `/`
     endpoint, short timeout). On failure, set `status.error` with an actionable
     message naming the URL; do not proceed to serve.
  3. Apply the base-URL override for the chosen engine (Section 5).
  4. `uv sync`, then `jarvis serve --engine <engine> --model <model> --agent simple`.

The custom path must still drive the progress UI to completion. `SetupStatus` gains an
**`engine_ready`** boolean; on the custom path, the "model download" step is presented
as "Connecting to <engine>" and marked done once the health check passes, so the
existing `SetupScreen` step list still completes. (Step labels in `SetupScreen.tsx`
become source-aware.)

### 4. Settings — "Inference source"

`frontend/src/pages/SettingsPage.tsx` gains an **Inference source** section:

- Radio / segmented control: **Ollama (default)** vs **Custom endpoint**.
- Custom fields: base URL (prefilled `http://localhost:1234/v1`), model name, engine
  type (select; default `lmstudio`, plus `vllm`/`sglang`/`llamacpp`/`mlx`/generic),
  optional API key.
- Validation: custom requires a non-empty base URL and model.
- Save calls a new Tauri command **`set_inference_source(payload)`** that writes
  `inference.json` (and `save_cloud_key` if a key was entered). The change applies on
  **restart**; the UI shows a "Restart to apply" note after saving.

### 5. Custom `base_url` → engine wiring

Engines read their host from `JarvisConfig` (`[engine.<key>] host=`), not from a serve
flag. **To confirm during planning:** the exact config-layering mechanism — whether
`JarvisConfig` merges a user-level `~/.openjarvis/config.toml`, or whether an env var
is the cleaner override. Target approach (pending that confirmation):

- Boot writes/updates a minimal `~/.openjarvis/config.toml` with
  `[engine.<engine>] host = "<base_url-without-/v1-suffix>"`, then runs
  `jarvis serve --engine <engine> --model <model>`.
- Fallback if user-level config is not merged: pass the host via the environment
  variable the OpenAI-compatible engine already honors, set on the serve child
  (alongside `read_cloud_keys()` injection).

Either way the override is owned by the desktop app and never edits the cloned repo's
tracked `configs/openjarvis/config.toml` (avoids self-update conflicts).

### 6. Error handling

- **Custom URL unreachable:** health check fails → `status.error` naming the URL and
  suggesting the server be started / the URL corrected. No serve attempt.
- **Custom model not served by the endpoint:** surfaced via the normal serve health
  check / first-request error (out of scope to pre-validate the model list).
- **Ollama path:** unchanged from today (pull failure → fallback model → error).

## Testing & isolation

Extract pure, side-effect-free functions so boot logic is testable without spawning
processes, and add them to the existing `#[cfg(test)]` module in `lib.rs`:

- `default_local_model(ram_gb: f64) -> &'static str` — covers the RAM thresholds in
  the Section 2 table, including the 0/1-fitting edge cases.
- `parse_inference_config(json: &str) -> InferenceConfig` and its serialization —
  round-trip and malformed-input handling (malformed → treated as `ollama` default).
- `boot_plan(cfg: &InferenceConfig, ram_gb: f64) -> BootPlan` where `BootPlan`
  expresses `{ launch_ollama: bool, model_to_pull: Option<String>, serve_args:
  Vec<String> }`. Assert: ollama plan launches Ollama and pulls one model; custom plan
  does **not** launch Ollama, pulls nothing, and emits `--engine <key> --model <m>`.

Frontend: unit-test the Settings custom-endpoint form validation (URL + model
required; "restart to apply" shown after save).

## Affected files (anticipated)

- `frontend/src-tauri/src/lib.rs` — `InferenceConfig` type, `inference.json`
  read/write, `default_local_model`, `boot_plan`, `boot_backend` branching,
  `set_inference_source` command, `SetupStatus.engine_ready`.
- `frontend/src/components/SetupScreen.tsx` — source-aware step labels.
- `frontend/src/pages/SettingsPage.tsx` — "Inference source" section.
- `frontend/src/lib/api.ts` — `set_inference_source` / `get_inference_source` bindings.

## Rollout

Independent of, and complementary to, PR #446 (which removed the runaway ladder).
This spec pins the single default model and adds the custom-endpoint path.
