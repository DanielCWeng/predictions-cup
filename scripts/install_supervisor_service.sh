#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_REPO_ROOT="${PREDICTIONS_CUP_SUPERVISOR_TARGET_REPO_ROOT:-${REPO_ROOT}}"
UNIT_SOURCE="${REPO_ROOT}/deploy/systemd/predictions-cup-supervisor.service"
SYSTEMD_DIR="${PREDICTIONS_CUP_SYSTEMD_DIR:-/etc/systemd/system}"
SYSTEMCTL_BIN="${PREDICTIONS_CUP_SYSTEMCTL:-systemctl}"
SERVICE="predictions-cup-supervisor.service"

runtime_user="${PREDICTIONS_CUP_RUNTIME_USER:-${SUDO_USER:-$(id -un)}}"
if [[ "${runtime_user}" == "root" ]]; then
  echo "ERROR: set PREDICTIONS_CUP_RUNTIME_USER when running as root" >&2
  exit 1
fi
runtime_home="$(getent passwd "${runtime_user}" | cut -d: -f6)"
runtime_env="${runtime_home}/.config/predictions-cup/runtime.env"
python_bin="${PREDICTIONS_CUP_PYTHON:-${REPO_ROOT}/.venv/bin/python}"

[[ -f "${runtime_env}" ]] || { echo "ERROR: missing ${runtime_env}" >&2; exit 1; }
[[ -x "${python_bin}" ]] || { echo "ERROR: missing executable ${python_bin}" >&2; exit 1; }
[[ -f "${UNIT_SOURCE}" ]] || { echo "ERROR: missing ${UNIT_SOURCE}" >&2; exit 1; }

if grep -Eq '^[[:space:]]*PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL[[:space:]]*=' "${runtime_env}"; then
  echo "ERROR: runtime.env must not contain trade credential" >&2
  exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT
sed \
  -e "s|@@RUNTIME_USER@@|${runtime_user}|g" \
  -e "s|@@CODE_ROOT@@|${REPO_ROOT}|g" \
  -e "s|@@TARGET_REPO_ROOT@@|${TARGET_REPO_ROOT}|g" \
  -e "s|@@RUNTIME_ENV@@|${runtime_env}|g" \
  -e "s|@@PYTHON_BIN@@|${python_bin}|g" \
  "${UNIT_SOURCE}" > "${tmp}"

if grep -Eq '@@[A-Z_]+@@' "${tmp}"; then
  echo "ERROR: unresolved systemd placeholder" >&2
  exit 1
fi

grep -q '^UnsetEnvironment=PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL$' "${tmp}" || {
  echo "ERROR: supervisor unit must strip trade credential" >&2
  exit 1
}

if [[ "${SYSTEMD_DIR}" == "/etc/systemd/system" && "$(id -u)" -ne 0 ]]; then
  echo "ERROR: rerun with sudo" >&2
  exit 1
fi
mkdir -p "${SYSTEMD_DIR}"
install -m 0644 "${tmp}" "${SYSTEMD_DIR}/${SERVICE}"
"${SYSTEMCTL_BIN}" daemon-reload
"${SYSTEMCTL_BIN}" enable "${SERVICE}"
"${SYSTEMCTL_BIN}" restart "${SERVICE}"
"${SYSTEMCTL_BIN}" --no-pager --full status "${SERVICE}" || true
