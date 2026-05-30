# Desktop Inference-Source Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the macOS/desktop app download exactly one sensible Ollama model by default, and let a user point it at a custom OpenAI-compatible server (LM Studio / custom URL) that skips Ollama entirely.

**Architecture:** A small `~/.openjarvis/inference.json` (written by Settings, read by boot) selects the source. The boot sequence in `frontend/src-tauri/src/lib.rs` branches on it via a pure `boot_plan()` function. The default Ollama path pulls only `default_local_model()` (second-largest model that fits in RAM). The custom path skips Ollama and feeds the server URL to `jarvis serve` through the `<ENGINE_ID>_HOST` environment variable that every OpenAI-compatible engine already honors (`src/openjarvis/engine/_openai_compat.py:34-35`).

**Tech Stack:** Rust (Tauri v2, `serde`, `serde_json`, `reqwest`, `tokio`), TypeScript/React (Tauri `invoke`), Python (`jarvis serve` CLI — read-only here, no changes).

**Spec:** `docs/design/2026-05-30-desktop-inference-source-design.md`

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `frontend/src-tauri/src/lib.rs` | Boot sequence, source config I/O, pure planning fns, Tauri commands | Modify |
| `frontend/src/lib/api.ts` | Tauri command bindings | Modify |
| `frontend/src/pages/SettingsPage.tsx` | "Inference source" settings UI | Modify |
| `frontend/src/components/SetupScreen.tsx` | Source-aware boot step labels | Modify |

All Rust logic lives in `lib.rs` to match the existing single-file desktop backend (do not split it — the codebase keeps the Tauri backend in one file).

## Prerequisites (do once before running any Rust test)

The Tauri crate's `run()` calls `tauri::generate_context!()`, which fails to compile unless the built frontend assets exist at `frontend/dist`. Build them once so `cargo test` can compile the crate:

- [ ] **Build the frontend so the Rust crate compiles**

```bash
cd frontend
npm ci
npm run build            # produces frontend/dist
ls frontend/dist/index.html   # confirm it exists
```

Expected: `frontend/dist/index.html` exists. If `npm run build` is unavailable in your environment, create a placeholder so the macro resolves: `mkdir -p frontend/dist && printf '<!doctype html><title>dev</title>' > frontend/dist/index.html`.

From here, all Rust tests run from `frontend/src-tauri`:
```bash
cd frontend/src-tauri && cargo test <name> -- --nocapture
```

---

## Task 1: Single default model + remove the runaway ladder-walk

**Files:**
- Modify: `frontend/src-tauri/src/lib.rs` (the `preferred_model()` region ~89-103; Phase 4 region ~1041-1053)
- Test: same file, `#[cfg(test)] mod tests` (~2016)

- [ ] **Step 1: Write the failing test**

Add to `mod tests` (extend the `use super::{...}` line to include `default_local_model`):

```rust
    use super::default_local_model;

    #[test]
    fn default_local_model_picks_second_largest_that_fits() {
        // QWEN35_MODELS min_ram ladder: 4,6,8,12,24,32,96 GB
        assert_eq!(default_local_model(4.0), "qwen3.5:0.8b");  // only one fits
        assert_eq!(default_local_model(8.0), "qwen3.5:2b");    // fits 0.8/2/4 → 2nd-largest
        assert_eq!(default_local_model(16.0), "qwen3.5:4b");   // fits ..9b → 2nd-largest
        assert_eq!(default_local_model(32.0), "qwen3.5:27b");  // fits ..35b → 2nd-largest
        assert_eq!(default_local_model(128.0), "qwen3.5:35b"); // fits all → 2nd-largest
    }

    #[test]
    fn default_local_model_falls_back_when_nothing_fits() {
        assert_eq!(default_local_model(1.0), super::FALLBACK_MODEL);
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend/src-tauri && cargo test default_local_model`
Expected: FAIL — `cannot find function default_local_model in module super`.

- [ ] **Step 3: Add `default_local_model` and make `models_that_fit` RAM-parameterized**

Replace the existing `models_that_fit()` (lines ~79-87) and `preferred_model()` (lines ~89-103) with:

```rust
/// Return the Qwen3.5 models that fit in `ram_gb`, smallest first.
fn models_that_fit_in(ram_gb: f64) -> Vec<&'static str> {
    QWEN35_MODELS
        .iter()
        .filter(|(_, _, min_ram)| ram_gb >= *min_ram)
        .map(|(tag, _, _)| *tag)
        .collect()
}

/// The default local model: the second-largest Qwen3.5 model that fits in
/// `ram_gb`. Falls back to the only fitting model, or FALLBACK_MODEL if none
/// fit. Deliberately NOT the largest — leaves RAM headroom for the OS/app.
fn default_local_model(ram_gb: f64) -> &'static str {
    let fitting = models_that_fit_in(ram_gb);
    match fitting.len() {
        0 => FALLBACK_MODEL,
        1 => fitting[0],
        n => fitting[n - 2],
    }
}
```

Then delete the now-unused `preferred_model()` and the Phase 4 background-pull block. Replace the Phase 4 block (lines ~1041-1053) with:

```rust
    // No further model downloads. The single default model pulled above is
    // enough to make the app usable; pulling the rest of the ladder
    // unprompted (up to qwen3.5:122b ≈ 81 GB) was a reported defect.
```

- [ ] **Step 4: Update the serve-time model resolution to use the new default**

In `boot_backend`, the block that computes `startup_model` (lines ~851-859, currently using `preferred_model()`) is replaced wholesale in Task 4. For now, to keep the crate compiling, change line ~852 from `let pref = preferred_model();` to `let pref = default_local_model(total_ram_gb());`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend/src-tauri && cargo test default_local_model`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src-tauri/src/lib.rs
git commit -m "fix(desktop): pull only the second-largest fitting model"
```

---

## Task 2: Inference-source config type + I/O

**Files:**
- Modify: `frontend/src-tauri/src/lib.rs` (add near the cloud-keys helpers ~1356)
- Test: `#[cfg(test)] mod tests`

- [ ] **Step 1: Write the failing tests**

```rust
    use super::{normalize_host, parse_inference_config, InferenceConfig, SourceKind};

    #[test]
    fn parse_defaults_to_ollama_when_file_missing_or_garbage() {
        assert!(matches!(parse_inference_config("").kind, SourceKind::Ollama));
        assert!(matches!(parse_inference_config("not json").kind, SourceKind::Ollama));
    }

    #[test]
    fn parse_reads_custom_endpoint() {
        let cfg = parse_inference_config(
            r#"{"kind":"custom","model":"qwen2.5-7b","host":"http://localhost:1234","engine":"lmstudio"}"#,
        );
        assert!(matches!(cfg.kind, SourceKind::Custom));
        assert_eq!(cfg.model.as_deref(), Some("qwen2.5-7b"));
        assert_eq!(cfg.host.as_deref(), Some("http://localhost:1234"));
        assert_eq!(cfg.engine.as_deref(), Some("lmstudio"));
    }

    #[test]
    fn normalize_host_strips_trailing_slash_and_v1() {
        assert_eq!(normalize_host("http://localhost:1234/v1"), "http://localhost:1234");
        assert_eq!(normalize_host("http://localhost:1234/v1/"), "http://localhost:1234");
        assert_eq!(normalize_host("http://localhost:1234/"), "http://localhost:1234");
        assert_eq!(normalize_host("http://host:8000"), "http://host:8000");
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend/src-tauri && cargo test inference_config`  (and `cargo test normalize_host`)
Expected: FAIL — unresolved imports.

- [ ] **Step 3: Implement the type and helpers**

Add near the cloud-keys section (after `cloud_keys_path`, ~1367):

```rust
// ---------------------------------------------------------------------------
// Inference-source selection (~/.openjarvis/inference.json)
// ---------------------------------------------------------------------------

#[derive(serde::Serialize, serde::Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum SourceKind {
    Ollama,
    Custom,
}

impl Default for SourceKind {
    fn default() -> Self {
        SourceKind::Ollama
    }
}

#[derive(serde::Serialize, serde::Deserialize, Clone, Debug, Default)]
struct InferenceConfig {
    #[serde(default)]
    kind: SourceKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    model: Option<String>,
    /// Bare base URL (no trailing `/v1`), custom only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    host: Option<String>,
    /// OpenAI-compatible engine key (e.g. "lmstudio"), custom only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    engine: Option<String>,
}

/// Path to the inference-source config (~/.openjarvis/inference.json).
fn inference_config_path() -> std::path::PathBuf {
    std::path::PathBuf::from(home_dir())
        .join(".openjarvis")
        .join("inference.json")
}

/// Parse config text. Any error (missing/garbage) yields the Ollama default —
/// a broken file must never strand the user with no working inference source.
fn parse_inference_config(text: &str) -> InferenceConfig {
    serde_json::from_str::<InferenceConfig>(text).unwrap_or_default()
}

/// Read the on-disk inference config, or the Ollama default if absent.
fn read_inference_config() -> InferenceConfig {
    match std::fs::read_to_string(inference_config_path()) {
        Ok(text) => parse_inference_config(&text),
        Err(_) => InferenceConfig::default(),
    }
}

/// Write the inference config to disk (pretty JSON).
fn write_inference_config(cfg: &InferenceConfig) -> Result<(), String> {
    let path = inference_config_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let json = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    std::fs::write(&path, json + "\n").map_err(|e| format!("Failed to save inference config: {}", e))
}

/// Normalize a user-entered server URL to a bare base host: trim whitespace,
/// drop a trailing `/v1` segment (the engine re-appends its own api prefix),
/// then drop any trailing slash.
fn normalize_host(raw: &str) -> String {
    let s = raw.trim().trim_end_matches('/');
    let s = s.strip_suffix("/v1").unwrap_or(s);
    s.trim_end_matches('/').to_string()
}
```

- [ ] **Step 4: Run to verify passing**

Run: `cd frontend/src-tauri && cargo test inference_config; cargo test normalize_host`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src-tauri/src/lib.rs
git commit -m "feat(desktop): add inference-source config type and I/O"
```

---

## Task 3: Pure `boot_plan()` decision function

**Files:**
- Modify: `frontend/src-tauri/src/lib.rs`
- Test: `#[cfg(test)] mod tests`

- [ ] **Step 1: Write the failing tests**

```rust
    use super::{boot_plan, BootPlan, InferenceConfig, SourceKind};

    #[test]
    fn boot_plan_ollama_launches_and_pulls_one_model() {
        let cfg = InferenceConfig { kind: SourceKind::Ollama, ..Default::default() };
        let plan = boot_plan(&cfg, 16.0);
        assert!(plan.launch_ollama);
        assert_eq!(plan.model_to_pull.as_deref(), Some("qwen3.5:4b"));
        assert!(plan.host_env.is_none());
        assert!(plan.serve_args.windows(2).any(|w| w == ["--engine", "ollama"]));
        assert!(plan.serve_args.windows(2).any(|w| w == ["--model", "qwen3.5:4b"]));
    }

    #[test]
    fn boot_plan_ollama_respects_pinned_model() {
        let cfg = InferenceConfig {
            kind: SourceKind::Ollama,
            model: Some("qwen3.5:9b".into()),
            ..Default::default()
        };
        let plan = boot_plan(&cfg, 16.0);
        assert_eq!(plan.model_to_pull.as_deref(), Some("qwen3.5:9b"));
    }

    #[test]
    fn boot_plan_custom_skips_ollama_and_sets_host_env() {
        let cfg = InferenceConfig {
            kind: SourceKind::Custom,
            model: Some("qwen2.5-7b".into()),
            host: Some("http://localhost:1234".into()),
            engine: Some("lmstudio".into()),
        };
        let plan = boot_plan(&cfg, 16.0);
        assert!(!plan.launch_ollama);
        assert!(plan.model_to_pull.is_none());
        assert_eq!(
            plan.host_env,
            Some(("LMSTUDIO_HOST".to_string(), "http://localhost:1234".to_string()))
        );
        assert!(plan.serve_args.windows(2).any(|w| w == ["--engine", "lmstudio"]));
        assert!(plan.serve_args.windows(2).any(|w| w == ["--model", "qwen2.5-7b"]));
    }

    #[test]
    fn boot_plan_custom_defaults_engine_to_lmstudio() {
        let cfg = InferenceConfig {
            kind: SourceKind::Custom,
            model: Some("m".into()),
            host: Some("http://h:1".into()),
            engine: None,
        };
        let plan = boot_plan(&cfg, 16.0);
        assert_eq!(plan.host_env.unwrap().0, "LMSTUDIO_HOST");
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend/src-tauri && cargo test boot_plan`
Expected: FAIL — unresolved `boot_plan` / `BootPlan`.

- [ ] **Step 3: Implement `BootPlan` and `boot_plan`**

Add after `default_local_model` (or anywhere above `boot_backend`):

```rust
/// A resolved boot plan derived purely from the inference config + RAM.
/// Pure and side-effect-free so it can be unit-tested without spawning
/// processes or touching the network.
#[derive(Debug, Clone, PartialEq, Eq)]
struct BootPlan {
    /// Whether to start and wait for the bundled Ollama.
    launch_ollama: bool,
    /// The single Ollama model to pull (None for custom endpoints).
    model_to_pull: Option<String>,
    /// Optional `(ENV_NAME, value)` host override injected into the
    /// `jarvis serve` child (e.g. `("LMSTUDIO_HOST", "http://localhost:1234")`).
    host_env: Option<(String, String)>,
    /// Args passed to `uv run jarvis serve ...` (excludes the leading
    /// `run`/`jarvis`/`serve`/`--port`, which the caller adds).
    serve_args: Vec<String>,
}

/// Default model id used when a custom endpoint config omits one.
const CUSTOM_FALLBACK_ENGINE: &str = "lmstudio";

fn boot_plan(cfg: &InferenceConfig, ram_gb: f64) -> BootPlan {
    match cfg.kind {
        SourceKind::Ollama => {
            let model = cfg
                .model
                .clone()
                .unwrap_or_else(|| default_local_model(ram_gb).to_string());
            BootPlan {
                launch_ollama: true,
                model_to_pull: Some(model.clone()),
                host_env: None,
                serve_args: vec![
                    "--engine".into(),
                    "ollama".into(),
                    "--model".into(),
                    model,
                    "--agent".into(),
                    "simple".into(),
                ],
            }
        }
        SourceKind::Custom => {
            let engine = cfg
                .engine
                .clone()
                .unwrap_or_else(|| CUSTOM_FALLBACK_ENGINE.to_string());
            let host = cfg.host.clone().unwrap_or_default();
            let env_name = format!("{}_HOST", engine.to_uppercase());
            let model = cfg.model.clone().unwrap_or_default();
            BootPlan {
                launch_ollama: false,
                model_to_pull: None,
                host_env: Some((env_name, host)),
                serve_args: vec![
                    "--engine".into(),
                    engine,
                    "--model".into(),
                    model,
                    "--agent".into(),
                    "simple".into(),
                ],
            }
        }
    }
}
```

- [ ] **Step 4: Run to verify passing**

Run: `cd frontend/src-tauri && cargo test boot_plan`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src-tauri/src/lib.rs
git commit -m "feat(desktop): add pure boot_plan() source-branching function"
```

---

## Task 4: Wire `boot_backend` to the plan (custom endpoint health check + branching)

**Files:**
- Modify: `frontend/src-tauri/src/lib.rs` (`boot_backend`, ~607-1054; `SetupStatus`, ~338)

This task is integration glue around the tested pure functions; verify by manual run (Task 8) since it spawns processes.

- [ ] **Step 1: Add a `source` label field to `SetupStatus`**

In `struct SetupStatus` (line ~338) add after `model_ready`:

```rust
    /// "ollama" | "custom" — lets the setup UI relabel the progress steps.
    source: String,
```

In `impl Default for SetupStatus` add:

```rust
            source: "ollama".into(),
```

- [ ] **Step 2: Add an endpoint reachability helper**

Add near `wait_for_url` (~367):

```rust
/// True if a custom OpenAI-compatible endpoint answers at all (any HTTP
/// status counts — a 404 still proves the server is up). `host` is the bare
/// base URL; we probe `<host>/v1/models`.
async fn endpoint_reachable(host: &str, timeout: Duration) -> bool {
    let client = match reqwest::Client::builder().timeout(Duration::from_secs(3)).build() {
        Ok(c) => c,
        Err(_) => return false,
    };
    let url = format!("{}/v1/models", host.trim_end_matches('/'));
    let deadline = tokio::time::Instant::now() + timeout;
    while tokio::time::Instant::now() < deadline {
        if client.get(&url).send().await.is_ok() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    false
}
```

- [ ] **Step 3: Branch the boot sequence on the plan**

At the top of `boot_backend` (after acquiring `status`/`backend`, before "Phase 1"), read the config and plan:

```rust
    let cfg = read_inference_config();
    let plan = boot_plan(&cfg, total_ram_gb());
    {
        let mut s = status.lock().await;
        s.source = match cfg.kind {
            SourceKind::Ollama => "ollama",
            SourceKind::Custom => "custom",
        }
        .into();
    }
```

Wrap the existing Ollama-start (Phase 1, ~622-650) and model-pull (Phase 2, ~652-685) in `if plan.launch_ollama { ... }`. In the Phase 2 pull, replace `STARTUP_MODEL` with the planned model:

```rust
    if plan.launch_ollama {
        // ... existing Phase 1 Ollama start + wait_for_url ...
        let model = plan.model_to_pull.clone().unwrap_or_else(|| STARTUP_MODEL.to_string());
        if !ollama_has_model(&model).await {
            { let mut s = status.lock().await; s.detail = format!("Downloading {}...", model); }
            if let Err(e) = pull_model(&model).await {
                eprintln!("Warning: failed to pull {}: {}", model, e);
                if !ollama_has_model(FALLBACK_MODEL).await {
                    if let Err(e2) = pull_model(FALLBACK_MODEL).await {
                        let mut s = status.lock().await;
                        s.error = Some(format!("Failed to download model: {}", e2));
                        return;
                    }
                }
            }
        }
        // Persist the resolved model so Settings shows it and reuses it.
        let mut persisted = cfg.clone();
        persisted.model = Some(model);
        let _ = write_inference_config(&persisted);
        { let mut s = status.lock().await; s.ollama_ready = true; s.model_ready = true; }
    } else {
        // Custom endpoint: never start Ollama, never download.
        let host = plan.host_env.as_ref().map(|(_, v)| v.clone()).unwrap_or_default();
        { let mut s = status.lock().await;
          s.phase = "model".into();
          s.detail = format!("Connecting to {}...", host); }
        if host.is_empty() || !endpoint_reachable(&host, Duration::from_secs(15)).await {
            let mut s = status.lock().await;
            s.error = Some(format!(
                "Could not reach your custom inference server at {}. \
                 Start the server (e.g. LM Studio) and check the URL in Settings, then relaunch.",
                if host.is_empty() { "(no URL set)" } else { &host }
            ));
            return;
        }
        { let mut s = status.lock().await; s.ollama_ready = true; s.model_ready = true; }
    }
```

(The `s.ollama_ready = true` on the custom path keeps the existing 2-step `SetupScreen` progress bar complete; the label is corrected in Task 7.)

- [ ] **Step 4: Replace the hard-coded serve args with the plan's args**

Find the `cmd.args([...])` for `jarvis serve` (~919-933). Replace the static `["run","jarvis","serve","--port",&JARVIS_PORT.to_string(),"--model",startup_model,"--agent","simple"]` with the plan-driven version, and delete the now-dead `startup_model`/`pref` block (~851-859):

```rust
    let mut cmd = tokio::process::Command::new(&uv_bin);
    let mut serve_argv: Vec<String> = vec![
        "run".into(), "jarvis".into(), "serve".into(),
        "--port".into(), JARVIS_PORT.to_string(),
    ];
    serve_argv.extend(plan.serve_args.iter().cloned());
    cmd.args(&serve_argv)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .current_dir(root);

    // Inject the custom-endpoint host override, if any.
    if let Some((name, value)) = &plan.host_env {
        cmd.env(name, value);
    }
    // Inject cloud API keys from ~/.openjarvis/cloud-keys.env
    for (key, value) in read_cloud_keys() {
        cmd.env(&key, &value);
    }
```

- [ ] **Step 5: Compile and run the full test suite**

Run: `cd frontend/src-tauri && cargo test`
Expected: PASS — all Task 1-3 tests plus existing tests. `preferred_model` and the old `models_that_fit` were removed in Task 1; `models_that_fit_in` (used by `default_local_model`) is the survivor. Resolve any leftover dead-code warning by deleting the offending fn.

- [ ] **Step 6: Commit**

```bash
git add frontend/src-tauri/src/lib.rs
git commit -m "feat(desktop): branch boot on inference source (ollama vs custom)"
```

---

## Task 5: Tauri commands `get_inference_source` / `set_inference_source`

**Files:**
- Modify: `frontend/src-tauri/src/lib.rs` (near cloud-key commands ~1432; `invoke_handler` ~1970)

- [ ] **Step 1: Implement the commands**

Add near `get_cloud_key_status` (~1441):

```rust
/// Return the current inference-source config for the Settings UI.
#[tauri::command]
async fn get_inference_source() -> Result<InferenceConfig, String> {
    Ok(read_inference_config())
}

/// Persist the chosen inference source. `host` is normalized to a bare base
/// URL. For custom endpoints, an optional API key is stored in cloud-keys.env
/// under `<ENGINE>_API_KEY`. Applies on next app launch.
#[tauri::command]
async fn set_inference_source(
    kind: String,
    model: Option<String>,
    host: Option<String>,
    engine: Option<String>,
    api_key: Option<String>,
) -> Result<(), String> {
    let kind = match kind.as_str() {
        "custom" => SourceKind::Custom,
        _ => SourceKind::Ollama,
    };
    let cfg = InferenceConfig {
        kind,
        model: model.filter(|m| !m.is_empty()),
        host: host.map(|h| normalize_host(&h)).filter(|h| !h.is_empty()),
        engine: engine.filter(|e| !e.is_empty()),
    };
    if let SourceKind::Custom = cfg.kind {
        if cfg.host.is_none() {
            return Err("A server URL is required for a custom endpoint.".into());
        }
        if cfg.model.as_deref().unwrap_or("").is_empty() {
            return Err("A model name is required for a custom endpoint.".into());
        }
        if let Some(key) = api_key.filter(|k| !k.is_empty()) {
            let engine = cfg.engine.clone().unwrap_or_else(|| CUSTOM_FALLBACK_ENGINE.to_string());
            let key_name = format!("{}_API_KEY", engine.to_uppercase());
            let _ = save_cloud_key(key_name, key).await;
        }
    }
    write_inference_config(&cfg)
}
```

- [ ] **Step 2: Register both commands in `invoke_handler`**

In the `tauri::generate_handler![...]` list (~1970-1998) add:

```rust
            get_inference_source,
            set_inference_source,
```

- [ ] **Step 3: Compile**

Run: `cd frontend/src-tauri && cargo test`
Expected: PASS (compiles with the new commands; existing tests still pass).

- [ ] **Step 4: Commit**

```bash
git add frontend/src-tauri/src/lib.rs
git commit -m "feat(desktop): add get/set_inference_source Tauri commands"
```

---

## Task 6: Frontend API bindings

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add typed bindings**

Append to `api.ts` (mirror the existing `isTauri()` + dynamic-`invoke` pattern used by `pullModel`):

```typescript
export type InferenceSource = {
  kind: 'ollama' | 'custom';
  model?: string;
  host?: string;
  engine?: string;
};

export async function getInferenceSource(): Promise<InferenceSource> {
  if (isTauri()) {
    const { invoke } = await import('@tauri-apps/api/core');
    return invoke<InferenceSource>('get_inference_source');
  }
  return { kind: 'ollama' };
}

export async function setInferenceSource(
  src: InferenceSource & { apiKey?: string },
): Promise<void> {
  if (!isTauri()) throw new Error('Inference source is configurable in the desktop app only.');
  const { invoke } = await import('@tauri-apps/api/core');
  await invoke('set_inference_source', {
    kind: src.kind,
    model: src.model ?? null,
    host: src.host ?? null,
    engine: src.engine ?? null,
    apiKey: src.apiKey ?? null,
  });
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds (no TS errors).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(desktop): add inference-source API bindings"
```

---

## Task 7: Settings UI + source-aware setup labels

**Files:**
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/components/SetupScreen.tsx`

- [ ] **Step 1: Add an "Inference source" section to SettingsPage**

Below the existing "Local models (Ollama)" / cloud-keys area (~323-347), add a section using the existing `SettingRow` component and the same input styling already in the file. Wire it to the bindings:

```tsx
// near other imports
import { getInferenceSource, setInferenceSource, type InferenceSource } from '../lib/api';

// inside SettingsPage(), with the other useState hooks:
const [source, setSource] = useState<InferenceSource>({ kind: 'ollama' });
const [customHost, setCustomHost] = useState('http://localhost:1234/v1');
const [customModel, setCustomModel] = useState('');
const [customEngine, setCustomEngine] = useState('lmstudio');
const [customKey, setCustomKey] = useState('');
const [srcSaved, setSrcSaved] = useState(false);

useEffect(() => {
  getInferenceSource().then((s) => {
    setSource(s);
    if (s.host) setCustomHost(s.host);
    if (s.model) setCustomModel(s.model);
    if (s.engine) setCustomEngine(s.engine);
  }).catch(() => {});
}, []);

const saveSource = useCallback(async () => {
  await setInferenceSource(
    source.kind === 'custom'
      ? { kind: 'custom', host: customHost, model: customModel, engine: customEngine, apiKey: customKey || undefined }
      : { kind: 'ollama' },
  );
  setSrcSaved(true);
}, [source.kind, customHost, customModel, customEngine, customKey]);
```

JSX (place inside the settings card list; match surrounding markup/classes):

```tsx
<SettingRow label="Inference source" description="Where the app runs models. Applies after restart.">
  <select
    value={source.kind}
    onChange={(e) => { setSource({ kind: e.target.value as InferenceSource['kind'] }); setSrcSaved(false); }}
  >
    <option value="ollama">Bundled Ollama (default)</option>
    <option value="custom">Custom OpenAI-compatible server</option>
  </select>
</SettingRow>

{source.kind === 'custom' && (
  <>
    <SettingRow label="Server URL" description="e.g. LM Studio: http://localhost:1234/v1">
      <input value={customHost} onChange={(e) => { setCustomHost(e.target.value); setSrcSaved(false); }} placeholder="http://localhost:1234/v1" />
    </SettingRow>
    <SettingRow label="Model" description="Model id served by your endpoint">
      <input value={customModel} onChange={(e) => { setCustomModel(e.target.value); setSrcSaved(false); }} placeholder="qwen2.5-7b-instruct" />
    </SettingRow>
    <SettingRow label="Server type" description="OpenAI-compatible engine">
      <select value={customEngine} onChange={(e) => { setCustomEngine(e.target.value); setSrcSaved(false); }}>
        <option value="lmstudio">LM Studio</option>
        <option value="vllm">vLLM</option>
        <option value="sglang">SGLang</option>
        <option value="llamacpp">llama.cpp</option>
        <option value="mlx">MLX</option>
      </select>
    </SettingRow>
    <SettingRow label="API key (optional)" description="Only if your server requires one">
      <input type="password" value={customKey} onChange={(e) => setCustomKey(e.target.value)} placeholder="leave blank if none" />
    </SettingRow>
  </>
)}

<SettingRow label="" description={srcSaved ? 'Saved — restart the app to apply.' : ''}>
  <button onClick={saveSource}>Save inference source</button>
</SettingRow>
```

- [ ] **Step 2: Make SetupScreen labels source-aware**

In `frontend/src/components/SetupScreen.tsx`, the `STEPS` array (~12) hard-codes "Inference Engine" / "Starting Ollama...". After fetching `status`, when `status.source === 'custom'`, relabel the engine step. Minimal change — replace the static `STEPS` usage in the render (~149) with a computed list:

```tsx
const steps = (status?.source === 'custom')
  ? [
      { key: 'ollama_ready', label: 'Inference Engine', icon: Cpu, detail: 'Connecting to your server...' },
      { key: 'model_ready', label: 'Endpoint', icon: Database, detail: 'Checking endpoint...' },
    ]
  : STEPS;
```

Then map over `steps` instead of `STEPS`. (`SetupStatus` in `frontend/src/lib/api.ts` types must include the new optional `source?: string` field — add it to the `SetupStatus` type.)

- [ ] **Step 3: Build and type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/SettingsPage.tsx frontend/src/components/SetupScreen.tsx frontend/src/lib/api.ts
git commit -m "feat(desktop): inference-source settings UI + source-aware setup"
```

---

## Task 8: Manual end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Fresh Ollama default pulls exactly one model**

```bash
rm -f ~/.openjarvis/inference.json
cd frontend && npm run tauri dev     # or run the built app
```
Watch the setup screen: it pulls a single model = `default_local_model(your_RAM)` and **stops**. Confirm with `ollama list` that no additional ladder models were pulled. Confirm `~/.openjarvis/inference.json` now records `{"kind":"ollama","model":"<that model>"}`.

- [ ] **Step 2: Custom endpoint skips Ollama**

Start LM Studio (or any OpenAI-compatible server) on `http://localhost:1234`, load a model. In the app: Settings → Inference source → "Custom OpenAI-compatible server", URL `http://localhost:1234/v1`, model = the served id, Save. Quit and relaunch.
Confirm: setup shows "Connecting to http://localhost:1234..."; Ollama is **not** started (`pgrep ollama` shows nothing spawned by the app) and no model is downloaded; a chat message round-trips through the custom server.

- [ ] **Step 3: Custom endpoint unreachable shows an actionable error**

Stop the custom server, relaunch the app. Confirm the setup screen surfaces "Could not reach your custom inference server at http://localhost:1234 ..." rather than hanging or silently downloading.

- [ ] **Step 4: Final commit (if any verification fixes were needed)**

```bash
git add -A && git commit -m "fix(desktop): address inference-source verification findings"
```

---

## Notes

- **Relationship to PR #446:** that PR removed only the Phase 4 ladder-walk. Task 1 here removes it again on this branch and also changes the default-model rule, so this branch is self-contained. If #446 merges first, resolve the trivial overlap in favor of this branch.
- **No Python changes:** the custom path relies entirely on the existing `<ENGINE_ID>_HOST` env-var support in `_openai_compat.py` and `jarvis serve --engine/--model`. This deliberately avoids touching core abstractions (which per CLAUDE.md would require opening an issue first).
- **Out of scope (per spec):** cloud-provider onboarding, first-run picker, live switching, endpoint auto-discovery.
