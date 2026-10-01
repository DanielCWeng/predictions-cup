#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
UNIT_SOURCE="${REPO_ROOT}/deploy/systemd/predictions-cup-supervisor-analyst.service"
SYSTEMD_DIR="${PREDICTIONS_CUP_SYSTEMD_DIR:-/etc/systemd/system}"
SYSTEMCTL_BIN="${PREDICTIONS_CUP_SYSTEMCTL:-systemctl}"
SERVICE="predictions-cup-supervisor-analyst.service"

runtime_user="${PREDICTIONS_CUP_RUNTIME_USER:-${SUDO_USER:-$(id -un)}}"
if [[ "${runtime_user}" == "root" ]]; then
  echo "ERROR: set PREDICTIONS_CUP_RUNTIME_USER when running as root" >&2
  exit 1
fi
runtime_home="$(getent passwd "${runtime_user}" | cut -d: -f6)"
analyst_env="${PREDICTIONS_CUP_ANALYST_ENV:-${runtime_home}/.config/predictions-cup/analyst.env}"
python_bin="${PREDICTIONS_CUP_PYTHON:-${REPO_ROOT}/.venv/bin/python}"

[[ -f "${analyst_env}" ]] || {
  echo "ERROR: missing ${analyst_env}" >&2
  echo "Create it with mode 600 and ANTHROPIC_API_KEY before enabling the analyst." >&2
  exit 1
}
grep -Eq '^ANTHROPIC_API_KEY=.+$' "${analyst_env}" || {
  echo "ERROR: analyst.env must contain ANTHROPIC_API_KEY" >&2
  exit 1
}
if grep -Eq '^PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL=' "${analyst_env}"; then
  echo "ERROR: analyst.env must not contain the SIG trade credential" >&2
  exit 1
fi
[[ -x "${python_bin}" ]] || { echo "ERROR: missing executable ${python_bin}" >&2; exit 1; }
[[ -f "${UNIT_SOURCE}" ]] || { echo "ERROR: missing ${UNIT_SOURCE}" >&2; exit 1; }

tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT
sed \
  -e "s|@@RUNTIME_USER@@|${runtime_user}|g" \
  -e "s|@@REPO_ROOT@@|${REPO_ROOT}|g" \
  -e "s|@@ANALYST_ENV@@|${analyst_env}|g" \
  -e "s|@@PYTHON_BIN@@|${python_bin}|g" \
  "${UNIT_SOURCE}" > "${tmp}"

if grep -Eq '@@[A-Z_]+@@' "${tmp}"; then
  echo "ERROR: unresolved systemd placeholder" >&2
  exit 1
fi

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
