#!/usr/bin/env bash
# Script that redoes the startup figures in the paper

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
CORE_MODEL="${1:-1r}"
RUN_DIR="${2:-${ROOT_DIR}/00run/01-startup}"
PLOTS_DIR="${RUN_DIR}/plots"
CSV_PATH="${RUN_DIR}/startUp_to1MW_res.csv"

mkdir -p "${PLOTS_DIR}"

# Keep matplotlib cache writable inside the selected run tree.
export MPLCONFIGDIR="${MPLCONFIGDIR:-${RUN_DIR}/.mplconfig}"
mkdir -p "${MPLCONFIGDIR}"

"${PYTHON_BIN}" -m startup.runMSRR \
  --core_model "${CORE_MODEL}" \
  --scenario startup_to_1mw \
  --run_dir "${RUN_DIR}"

"${PYTHON_BIN}" -m startup.plotApproachToCriticalityPhase4 \
  --core_model "${CORE_MODEL}" \
  --run_dir "${RUN_DIR}" \
  --csv "${CSV_PATH}" \
  --out "${PLOTS_DIR}/plot_startup_phase1to4.png" \
  --signed_log_out "${PLOTS_DIR}/plot_startup_phase1to4_signedlog.png"

"${PYTHON_BIN}" -m startup.plotStartUpTo1MW \
  --core_model "${CORE_MODEL}" \
  --run_dir "${RUN_DIR}" \
  --csv "${CSV_PATH}" \
  --out "${PLOTS_DIR}/plot_startup_to1MW.png"

echo "Run directory: ${RUN_DIR}"
echo "Plots directory: ${PLOTS_DIR}"
