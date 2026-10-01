#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
SOURCE_DIR="${REPO_ROOT}/deploy/systemd"
SYSTEMD_DIR="${PREDICTIONS_CUP_SYSTEMD_DIR:-/etc/systemd/system}"
SYSTEMCTL_BIN="${PREDICTIONS_CUP_SYSTEMCTL:-systemctl}"
SYSTEMD_ANALYZE_BIN="${PREDICTIONS_CUP_SYSTEMD_ANALYZE:-systemd-analyze}"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

runtime_user="${PREDICTIONS_CUP_RUNTIME_USER:-${SUDO_USER:-}}"
if [[ -z "${runtime_user}" || "${runtime_user}" == "root" ]]; then
  if [[ "$(id -u)" -ne 0 ]]; then runtime_user="$(id -un)"; else fail "set PREDICTIONS_CUP_RUNTIME_USER"; fi
fi

if [[ -n "${PREDICTIONS_CUP_RUNTIME_HOME:-}" ]]; then
  runtime_home="${PREDICTIONS_CUP_RUNTIME_HOME}"
else
  passwd_entry="$(getent passwd "${runtime_user}" || true)"
  [[ -n "${passwd_entry}" ]] || fail "cannot resolve home for ${runtime_user}"
  runtime_home="$(printf '%s\n' "${passwd_entry}" | cut -d: -f6)"
fi
runtime_env="${runtime_home}/.config/predictions-cup/runtime.env"
python_bin="${PREDICTIONS_CUP_PYTHON:-${REPO_ROOT}/.venv/bin/python}"

[[ -x "${python_bin}" ]] || fail "runtime Python is not executable: ${python_bin}"
[[ -f "${runtime_env}" && -r "${runtime_env}" ]] || fail "runtime env missing/unreadable: ${runtime_env}"
[[ "$(stat -c '%a' "${runtime_env}")" == "600" ]] || fail "runtime.env must have mode 600"
[[ "$(stat -c '%U' "${runtime_env}")" == "${runtime_user}" ]] || fail "runtime.env owner must be ${runtime_user}"

grep -Eq '^[[:space:]]*PREDICTIONS_CUP_SIG_READ_CREDENTIAL[[:space:]]*=[[:space:]]*[^#[:space:]]' "${runtime_env}" || fail "SIG read credential missing"
grep -Eq '^[[:space:]]*PREDICTIONS_CUP_TOURNAMENT_ID[[:space:]]*=[[:space:]]*[^#[:space:]]' "${runtime_env}" || fail "tournament id missing"
if grep -Eq '^[[:space:]]*PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL[[:space:]]*=' "${runtime_env}"; then
  fail "FULLSTACK-002 runtime.env must not contain PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL"
fi
if grep -Eiq '^[[:space:]]*PREDICTIONS_CUP_TRADING_ENABLED[[:space:]]*=[[:space:]]*(1|true|yes|on)' "${runtime_env}"; then
  fail "FULLSTACK-002 runtime.env must not enable trading"
fi
if grep -Eiq '^[[:space:]]*PREDICTIONS_CUP_EXECUTION_MODE[[:space:]]*=[[:space:]]*LIVE([[:space:]]*(#.*)?)?
units=(
  predictions-cup-alert@.service
  predictions-cup-sig-capture.service
  predictions-cup-polymarket-capture.service
  predictions-cup-maker.service
  predictions-cup-status.service
  predictions-cup-status.timer
  predictions-cup-runtime.target
)

for unit in "${units[@]}"; do [[ -f "${SOURCE_DIR}/${unit}" ]] || fail "missing unit template ${unit}"; done
"${python_bin}" -c 'import predictions_cup.fullstack; import predictions_cup.maker; import predictions_cup.sig.capture; import predictions_cup.external.polymarket.recorder'

[[ "${SYSTEMD_DIR}" != "/etc/systemd/system" || "$(id -u)" -eq 0 ]] || fail "installing to /etc/systemd/system requires root"
mkdir -p "${SYSTEMD_DIR}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

for unit in "${units[@]}"; do
  sed \
    -e "s|@@RUNTIME_USER@@|${runtime_user}|g" \
    -e "s|@@REPO_ROOT@@|${REPO_ROOT}|g" \
    -e "s|@@RUNTIME_ENV@@|${runtime_env}|g" \
    -e "s|@@PYTHON_BIN@@|${python_bin}|g" \
    "${SOURCE_DIR}/${unit}" > "${tmp_dir}/${unit}"
  grep -q '@@[A-Z_][A-Z_]*@@' "${tmp_dir}/${unit}" && fail "unresolved placeholder in ${unit}"
  grep -Eq 'PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL=.+' "${tmp_dir}/${unit}" && fail "trade credential embedded in ${unit}"
done

if command -v "${SYSTEMD_ANALYZE_BIN}" >/dev/null 2>&1 || [[ -x "${SYSTEMD_ANALYZE_BIN}" ]]; then
  "${SYSTEMD_ANALYZE_BIN}" verify "${tmp_dir}"/*
fi

for unit in "${units[@]}"; do
  install -m 0644 "${tmp_dir}/${unit}" "${SYSTEMD_DIR}/${unit}"
done

"${SYSTEMCTL_BIN}" daemon-reload
"${SYSTEMCTL_BIN}" enable predictions-cup-runtime.target predictions-cup-status.timer
printf 'Installed FULLSTACK-002 safe composition. No services were started and LIVE remains unavailable.\n'
printf 'Start explicitly with: %s/scripts/cupctl start-shadow\n' "${REPO_ROOT}"
 "${runtime_env}"; then
  fail "FULLSTACK-002 runtime.env must not request EXECUTION_MODE=LIVE"
fi

units=(
  predictions-cup-alert@.service
  predictions-cup-sig-capture.service
  predictions-cup-polymarket-capture.service
  predictions-cup-maker.service
  predictions-cup-status.service
  predictions-cup-status.timer
  predictions-cup-runtime.target
)

for unit in "${units[@]}"; do [[ -f "${SOURCE_DIR}/${unit}" ]] || fail "missing unit template ${unit}"; done
"${python_bin}" -c 'import predictions_cup.fullstack; import predictions_cup.maker; import predictions_cup.sig.capture; import predictions_cup.external.polymarket.recorder'

[[ "${SYSTEMD_DIR}" != "/etc/systemd/system" || "$(id -u)" -eq 0 ]] || fail "installing to /etc/systemd/system requires root"
mkdir -p "${SYSTEMD_DIR}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

for unit in "${units[@]}"; do
  sed \
    -e "s|@@RUNTIME_USER@@|${runtime_user}|g" \
    -e "s|@@REPO_ROOT@@|${REPO_ROOT}|g" \
    -e "s|@@RUNTIME_ENV@@|${runtime_env}|g" \
    -e "s|@@PYTHON_BIN@@|${python_bin}|g" \
    "${SOURCE_DIR}/${unit}" > "${tmp_dir}/${unit}"
  grep -q '@@[A-Z_][A-Z_]*@@' "${tmp_dir}/${unit}" && fail "unresolved placeholder in ${unit}"
  grep -Eq 'PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL=.+' "${tmp_dir}/${unit}" && fail "trade credential embedded in ${unit}"
  install -m 0644 "${tmp_dir}/${unit}" "${SYSTEMD_DIR}/${unit}"
done

if command -v "${SYSTEMD_ANALYZE_BIN}" >/dev/null 2>&1 || [[ -x "${SYSTEMD_ANALYZE_BIN}" ]]; then
  "${SYSTEMD_ANALYZE_BIN}" verify "${tmp_dir}"/*
fi

"${SYSTEMCTL_BIN}" daemon-reload
"${SYSTEMCTL_BIN}" enable predictions-cup-runtime.target predictions-cup-status.timer
printf 'Installed FULLSTACK-002 safe composition. No services were started and LIVE remains unavailable.\n'
printf 'Start explicitly with: %s/scripts/cupctl start-shadow\n' "${REPO_ROOT}"
