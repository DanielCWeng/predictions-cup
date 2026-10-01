# MODEL-RUNTIME-001 operator card

**PAPER enable:** add the model ID to `PREDICTIONS_CUP_PAPER_MODEL_IDS` in `~/.config/predictions-cup/runtime.env`, ensure the accepted SHADOW service is enabled, then restart the process.

**CODE LIVE gate:** `src/predictions_cup/models/model_<name>.py` → that model's explicit `ModelSpec(live_eligible=...)`. Default is false.

**ENV LIVE gate:** `~/.config/predictions-cup/runtime.env` → `PREDICTIONS_CUP_LIVE_MODEL_IDS=model_a,model_b`. Never commit production values.

**Trade credential:** `~/.config/predictions-cup/trade.env` → `PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL`. It never belongs in runtime.env, Git, a model file or the registry.

**Inspect:** `scripts/cupctl models status` (add `--json` for machine-readable output). An env-live ID with code gate off is `BLOCKED_CONFIGURATION`, not green.

**Disable one model:** remove its ID from `PREDICTIONS_CUP_LIVE_MODEL_IDS` (and PAPER allowlist if desired) and restart the runtime. Hot-swap means small file/registry/config change plus process restart, not runtime code injection.

**Global emergency:** the existing global halt/kill switch supersedes every model. Do not try to recover a model to override central Risk.

**Failure:** a quarantined model produces no new exposure. Recovery inside a running runtime is explicit with `ModelRuntime.recover(model_id)`; operationally, fix/review the cause and restart with the intended allowlists.
