#!/usr/bin/env bash
set -euo pipefail

# BUILD-007 + FULLSTACK-001 renderer. Always render on the destination host;
# never copy /etc/systemd/system/predictions-cup-* between VMs.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
UNIT_SOURCE_DIR="${REPO_ROOT}/deploy/systemd"
SYSTEMD_DIR="${PREDICTIONS_CUP_SYSTEMD_DIR:-/etc/systemd/system}"
SYSTEMCTL_BIN="${PREDICTIONS_CUP_SYSTEMCTL:-systemctl}"

SIG_SERVICE="predictions-cup-sig-capture.service"
POLYMARKET_SERVICE="predictions-cup-polymarket-capture.service"
MAKER_SERVICE="predictions-cup-maker.service"
LIVE_LEARN_SERVICE="predictions-cup-live-learn.service"
OBSERVE_SERVICE="predictions-cup-observe.service"
RUNTIME_TARGET="predictions-cup-runtime.target"
SERVICES=(
  "${SIG_SERVICE}"
  "${POLYMARKET_SERVICE}"
  "${MAKER_SERVICE}"
  "${LIVE_LEARN_SERVICE}"
  "${OBSERVE_SERVICE}"
)
TEMPLATES=("${SERVICES[@]}" "${RUNTIME_TARGET}")

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

resolve_runtime_user() {
  if [[ -n "${PREDICTIONS_CUP_RUNTIME_USER:-}" ]]; then
    printf '%s\n' "${PREDICTIONS_CUP_RUNTIME_USER}"
    return
  fi
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    printf '%s\n' "${SUDO_USER}"
    return
  fi
  if [[ "$(id -u)" -ne 0 ]]; then
    id -un
    return
  fi
  fail "set PREDICTIONS_CUP_RUNTIME_USER when running as root without SUDO_USER"
}

resolve_runtime_home() {
  local runtime_user="$1"
  if [[ -n "${PREDICTIONS_CUP_RUNTIME_HOME:-}" ]]; then
    printf '%s\n' "${PREDICTIONS_CUP_RUNTIME_HOME}"
    return
  fi
  local passwd_entry
  passwd_entry="$(getent passwd "${runtime_user}" || true)"
  [[ -n "${passwd_entry}" ]] || fail "cannot resolve home for runtime user ${runtime_user}"
  printf '%s\n' "${passwd_entry}" | cut -d: -f6
}

validate_render_value() {
  local label="$1"
  local value="$2"
  [[ -n "${value}" ]] || fail "${label} must not be empty"
  if [[ "${value}" =~ [[:space:]\&\|\\] ]]; then
    fail "${label} contains unsupported whitespace or shell/sed metacharacters"
  fi
}

has_env_assignment() {
  local name="$1"
  local env_file="$2"
  grep -Eq "^[[:space:]]*${name}[[:space:]]*=[[:space:]]*[^#[:space:]].*$" "${env_file}"
}

require_env_assignment() {
  local name="$1"
  local env_file="$2"
  has_env_assignment "${name}" "${env_file}" || fail "${env_file} must define non-empty ${name}"
}

env_flag_is_true() {
  local name="$1"
  local env_file="$2"
  grep -Eiq "^[[:space:]]*${name}[[:space:]]*=[[:space:]]*(1|true|yes|on)([[:space:]]*(#.*)?)?$" "${env_file}"
}

capability_mode() {
  local name="$1"
  local env_file="$2"
  local value
  value="$(
    sed -n -E "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*([^#[:space:]]+).*$/\\1/p" "${env_file}" \
      | tail -n 1 \
      | tr '[:lower:]' '[:upper:]'
  )"
  if [[ -z "${value}" ]]; then
    printf 'IN_PROCESS\n'
    return
  fi
  if [[ "${value}" != "IN_PROCESS" && "${value}" != "EXTERNAL_SERVICE" ]]; then
    fail "${name} must be IN_PROCESS or EXTERNAL_SERVICE"
  fi
  printf '%s\n' "${value}"
}

runtime_user="$(resolve_runtime_user)"
runtime_home="$(resolve_runtime_home "${runtime_user}")"
runtime_config_dir="${runtime_home}/.config/predictions-cup"
runtime_env="${runtime_config_dir}/runtime.env"
python_bin="${PREDICTIONS_CUP_PYTHON:-${REPO_ROOT}/.venv/bin/python}"

validate_render_value "runtime user" "${runtime_user}"
validate_render_value "repo root" "${REPO_ROOT}"
validate_render_value "runtime home" "${runtime_home}"
validate_render_value "python path" "${python_bin}"
validate_render_value "systemd directory" "${SYSTEMD_DIR}"

[[ -d "${REPO_ROOT}/src/predictions_cup" ]] || fail "repo source tree missing under ${REPO_ROOT}"
[[ -f "${REPO_ROOT}/src/predictions_cup/sig/capture.py" ]] || fail "SIG capture entrypoint missing"
[[ -f "${REPO_ROOT}/src/predictions_cup/external/polymarket/recorder.py" ]] || fail "Polymarket capture entrypoint missing"
[[ -f "${REPO_ROOT}/src/predictions_cup/maker/service.py" ]] || fail "MAKE entrypoint missing"
[[ -f "${REPO_ROOT}/src/predictions_cup/runtime/fullstack.py" ]] || fail "FULLSTACK operator entrypoint missing"
[[ -x "${python_bin}" ]] || fail "runtime Python is not executable: ${python_bin}"
[[ -d "${runtime_config_dir}" ]] || fail "runtime config directory missing: ${runtime_config_dir}"
[[ -f "${runtime_env}" ]] || fail "runtime environment file missing: ${runtime_env}"
[[ -r "${runtime_env}" ]] || fail "runtime environment file is not readable"

config_mode="$(stat -c '%a' "${runtime_config_dir}")"
env_mode="$(stat -c '%a' "${runtime_env}")"
[[ "${config_mode}" == "700" ]] || fail "${runtime_config_dir} must have mode 700 (found ${config_mode})"
[[ "${env_mode}" == "600" ]] || fail "${runtime_env} must have mode 600 (found ${env_mode})"

config_owner="$(stat -c '%U' "${runtime_config_dir}")"
env_owner="$(stat -c '%U' "${runtime_env}")"
[[ "${config_owner}" == "${runtime_user}" ]] || fail "runtime config directory must be owned by ${runtime_user}"
[[ "${env_owner}" == "${runtime_user}" ]] || fail "runtime.env must be owned by ${runtime_user}"

if grep -Eq '^[[:space:]]*PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL[[:space:]]*=' "${runtime_env}"; then
  fail "runtime.env must not contain PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL"
fi
if env_flag_is_true "PREDICTIONS_CUP_TRADING_ENABLED" "${runtime_env}"; then
  fail "standard FULLSTACK installer is rehearsal/SHADOW only; runtime.env must not enable trading"
fi
if grep -Eiq '^[[:space:]]*PREDICTIONS_CUP_EXECUTION_MODE[[:space:]]*=[[:space:]]*LIVE([[:space:]]*(#.*)?)?$' "${runtime_env}"; then
  fail "standard FULLSTACK installer must not configure LIVE execution"
fi

require_env_assignment "PREDICTIONS_CUP_SIG_READ_CREDENTIAL" "${runtime_env}"
require_env_assignment "PREDICTIONS_CUP_TOURNAMENT_ID" "${runtime_env}"

command -v "${SYSTEMCTL_BIN}" >/dev/null 2>&1 || [[ -x "${SYSTEMCTL_BIN}" ]] || fail "systemctl command not found: ${SYSTEMCTL_BIN}"

ACTIVE_SERVICES=("${SIG_SERVICE}")

if env_flag_is_true "PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED" "${runtime_env}"; then
  if ! has_env_assignment "PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS" "${runtime_env}"; then
    "${SYSTEMCTL_BIN}" disable --now "${POLYMARKET_SERVICE}" || true
    fail "${runtime_env} must define non-empty PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS"
  fi
  ACTIVE_SERVICES+=("${POLYMARKET_SERVICE}")
fi

if env_flag_is_true "PREDICTIONS_CUP_MAKER_ENABLED" "${runtime_env}"; then
  require_env_assignment "PREDICTIONS_CUP_TOURNAMENT_SLUG" "${runtime_env}"
  ACTIVE_SERVICES+=("${MAKER_SERVICE}")
fi

fixtures_allowed=0
if env_flag_is_true "PREDICTIONS_CUP_FULLSTACK_ALLOW_FIXTURES" "${runtime_env}"; then
  fixtures_allowed=1
fi

if env_flag_is_true "PREDICTIONS_CUP_LIVE_LEARN_ENABLED" "${runtime_env}" || env_flag_is_true "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_ENABLED" "${runtime_env}"; then
  live_learn_mode="$(capability_mode "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE" "${runtime_env}")"
  if [[ "${live_learn_mode}" == "IN_PROCESS" ]]; then
    env_flag_is_true "PREDICTIONS_CUP_MAKER_ENABLED" "${runtime_env}" || fail "IN_PROCESS LIVE-LEARN requires PREDICTIONS_CUP_MAKER_ENABLED=true"
  else
    if ! has_env_assignment "PREDICTIONS_CUP_LIVE_LEARN_COMMAND" "${runtime_env}" && [[ "${fixtures_allowed}" -eq 0 ]]; then
      fail "EXTERNAL_SERVICE LIVE-LEARN requires PREDICTIONS_CUP_LIVE_LEARN_COMMAND or explicit fixture mode"
    fi
    ACTIVE_SERVICES+=("${LIVE_LEARN_SERVICE}")
  fi
fi

if env_flag_is_true "PREDICTIONS_CUP_FULLSTACK_OBSERVE_ENABLED" "${runtime_env}"; then
  observe_mode="$(capability_mode "PREDICTIONS_CUP_FULLSTACK_OBSERVE_MODE" "${runtime_env}")"
  if [[ "${observe_mode}" == "IN_PROCESS" ]]; then
    env_flag_is_true "PREDICTIONS_CUP_MAKER_ENABLED" "${runtime_env}" || fail "IN_PROCESS OBSERVE requires PREDICTIONS_CUP_MAKER_ENABLED=true"
  else
    if ! has_env_assignment "PREDICTIONS_CUP_OBSERVE_COMMAND" "${runtime_env}" && [[ "${fixtures_allowed}" -eq 0 ]]; then
      fail "EXTERNAL_SERVICE OBSERVE requires PREDICTIONS_CUP_OBSERVE_COMMAND or explicit fixture mode"
    fi
    ACTIVE_SERVICES+=("${OBSERVE_SERVICE}")
  fi
fi

for template in "${TEMPLATES[@]}"; do
  [[ -f "${UNIT_SOURCE_DIR}/${template}" ]] || fail "unit template missing: ${UNIT_SOURCE_DIR}/${template}"
done

"${python_bin}" -c 'import predictions_cup.sig.capture; import predictions_cup.external.polymarket.recorder; import predictions_cup.maker.service; import predictions_cup.runtime.fullstack; import predictions_cup.runtime.adapter_service'

if [[ "${SYSTEMD_DIR}" == "/etc/systemd/system" && "$(id -u)" -ne 0 ]]; then
  fail "installing under /etc/systemd/system requires root; rerun with sudo"
fi
if [[ "${SYSTEMD_DIR}" != "/etc/systemd/system" ]]; then
  mkdir -p "${SYSTEMD_DIR}"
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
active_units="${ACTIVE_SERVICES[*]}"

for template in "${TEMPLATES[@]}"; do
  source_unit="${UNIT_SOURCE_DIR}/${template}"
  rendered_unit="${tmp_dir}/${template}"
  sed \
    -e "s|@@RUNTIME_USER@@|${runtime_user}|g" \
    -e "s|@@REPO_ROOT@@|${REPO_ROOT}|g" \
    -e "s|@@RUNTIME_ENV@@|${runtime_env}|g" \
    -e "s|@@PYTHON_BIN@@|${python_bin}|g" \
    -e "s|@@ACTIVE_UNITS@@|${active_units}|g" \
    "${source_unit}" > "${rendered_unit}"

  if grep -q '@@[A-Z_][A-Z_]*@@' "${rendered_unit}"; then
    fail "unresolved placeholder remains in ${template}"
  fi
  if grep -Eq 'trade\.env' "${rendered_unit}"; then
    fail "standard rehearsal units must not reference trade.env"
  fi
  if [[ "${template}" == *.service ]]; then
    grep -q '^UnsetEnvironment=PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL$' "${rendered_unit}" || fail "${template} must strip the SIG trade credential"
    grep -q -- '--runtime-env-only' "${rendered_unit}" || fail "${template} must disable repo-local dotenv loading"
    grep -q '^SendSIGKILL=no$' "${rendered_unit}" || fail "${template} must not force-kill on graceful-stop timeout"
  fi
  if [[ "${template}" == "${POLYMARKET_SERVICE}" ]] && ! grep -q -- '--require-explicit-universe' "${rendered_unit}"; then
    fail "Polymarket service must require a strict external supervised universe"
  fi
  if [[ "${template}" == "${SIG_SERVICE}" ]] && grep -q -- '--tracked-exchange-id' "${rendered_unit}"; then
    fail "SIG service must not hard-code tracked exchange IDs"
  fi
  if [[ "${template}" == "${MAKER_SERVICE}" ]] && grep -q -- '--live' "${rendered_unit}"; then
    fail "standard FULLSTACK maker unit must not authorize LIVE execution"
  fi

  install -m 0644 "${rendered_unit}" "${SYSTEMD_DIR}/${template}"
done

"${SYSTEMCTL_BIN}" daemon-reload
for service in "${SERVICES[@]}"; do
  found=0
  for active in "${ACTIVE_SERVICES[@]}"; do
    [[ "${service}" == "${active}" ]] && found=1
  done
  if [[ "${found}" -eq 1 ]]; then
    "${SYSTEMCTL_BIN}" enable "${service}"
  else
    "${SYSTEMCTL_BIN}" disable --now "${service}" || true
  fi
done
"${SYSTEMCTL_BIN}" enable "${RUNTIME_TARGET}"

restart_failed=0
for service in "${ACTIVE_SERVICES[@]}"; do
  if ! "${SYSTEMCTL_BIN}" restart "${service}"; then
    printf 'ERROR: restart failed for %s\n' "${service}" >&2
    restart_failed=1
  fi
done

active_failed=0
for service in "${ACTIVE_SERVICES[@]}"; do
  if "${SYSTEMCTL_BIN}" is-active --quiet "${service}"; then
    printf 'OK: %s is active\n' "${service}"
  else
    printf 'ERROR: %s is not active\n' "${service}" >&2
    active_failed=1
  fi
  "${SYSTEMCTL_BIN}" --no-pager --full status "${service}" || true
done

if [[ "${restart_failed}" -ne 0 || "${active_failed}" -ne 0 ]]; then
  printf 'Inspect logs with: journalctl -u <service> -n 100 --no-pager\n' >&2
  exit 1
fi

printf 'Installed composed rehearsal runtime. EnvironmentFile=%s\n' "${runtime_env}"
printf 'Active services: %s\n' "${active_units}"
printf 'LIVE authorization is deliberately not installed by this command.\n'
printf 'Next: bash scripts/cupctl status && bash scripts/cupctl rehearse\n'
