"""Shared helpers for frequency workflow runners and collectors."""

from __future__ import annotations

import csv
import math
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    from helpers.setpoint_model_version import check_table_model_version
    from helpers.setpoint_provenance import (
        DEFAULT_POLICY,
        QUALIFIED_COLUMN,
        handle_missing_verdict_column,
        require_qualified_row,
    )
except ImportError:  # direct-script execution outside an installed checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from helpers.setpoint_model_version import check_table_model_version
    from helpers.setpoint_provenance import (
        DEFAULT_POLICY,
        QUALIFIED_COLUMN,
        handle_missing_verdict_column,
        require_qualified_row,
    )

TABLE_OVERRIDE_NAME_MAP = {
    "reactivityFeedback.FuelTempSetPointNode1": "fuelTempSetPointNode1",
    "reactivityFeedback.FuelTempSetPointNode2": "fuelTempSetPointNode2",
    "reactivityFeedback.GrapTempSetPoint": "graphiteTempSetPoint",
}

MODEL_NAME_BY_CORE = {
    "1r": "MSRR.MSRRuhxNominalTrimNoTrips",
    "9r": "MSRR.MSRRuhxNominalTrim9RNoTrips",
}

# ---------------------------------------------------------------------------
# Model-package selection (TASK-20260823-02, Ambiguity C): the runner CLIs
# expose --package {legacy,segmented} with DEFAULT LEGACY so every pre-existing
# invocation keeps byte-identical behavior. 'segmented' drives the standalone
# SegmentedMSR package through helpers/segmented_runs.py (single source of
# truth for vehicle names and library files).
# ---------------------------------------------------------------------------
LEGACY_PACKAGE = "legacy"
SEGMENTED_PACKAGE = "segmented"
PACKAGE_CHOICES = (LEGACY_PACKAGE, SEGMENTED_PACKAGE)
DEFAULT_PACKAGE = LEGACY_PACKAGE

COLLECT_POWER_CANDIDATES = (
    "pkenpopulationn",
    "pkenpopulation",
    "powerblockfissionpowerp",
    "fuelchannelnompower",
    "npopulationn",
)

# Candidate power-signal column names used by the collectors after header
# cleaning (case preserved).
POWER_COLUMN_CANDIDATES = (
    "pkenpopulationn",
    "pkenpopulation",
    "powerBlockfissionPowerP",
    "fuelChannelNomPower",
    "FuelChannelNomPower",
    "npopulationn",
)

# SegmentedMSR sweeps (review 2026-10-01 M3): the result-variable contract of
# the segmented vehicles carries the neutron population as the overlay column
# ``nOut`` (``= pke.n_population.n``, the quantity the legacy column
# ``<core>.mpke.n_population.n`` carries; 1 at 1 MW). It is consulted only
# AFTER the legacy candidates and the legacy substring fallback, so every
# column the collector chose before is still chosen. The contract's PowerBlock
# taps ``pb.reactorPower`` / ``pb.fissionPower.P`` are deliberately not
# candidates: they are in W, while the gain and operating-point offset assume
# the normalized population (1 at the 1 MW operating point).
SEGMENTED_POWER_COLUMN_CANDIDATES = ("nOut",)

# Keep only time plus the response signal columns needed by collectFreqNominal*.
REDUCED_CSV_VARIABLE_FILTER = (
    r"^(time|.*n_population\.n|.*nPopulation\.n|.*fissionPowerP|.*nomPower)$"
)
STOP_TIME_MODE_CHOICES = ("fixed", "min_cycles_after_ss")
OUTPUT_INTERVAL_MODE_CHOICES = ("fixed_rate", "frequency_scaled")

HEADER_CLEAN_REGEX = re.compile(r"[\[\]\.\(\)_']")
HEAT_LOSS_COLUMN = "heatLossEnabled"


def default_steady_state_table(core_model: str) -> str:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(repo_root, "core", "init", f"setpoints_{core_model}.csv")


def clean_header_for_collect(name: str) -> str:
    cleaned = HEADER_CLEAN_REGEX.sub("", name)
    return cleaned.replace(" ", "").lower()


def find_collect_columns(header: list[str]) -> tuple[int, int]:
    cleaned = [clean_header_for_collect(item.strip().strip('"')) for item in header]

    time_idx = -1
    for idx, item in enumerate(cleaned):
        if item == "time":
            time_idx = idx
            break
    if time_idx < 0:
        raise ValueError("Could not find 'time' column in result CSV.")

    power_idx = -1
    for idx, item in enumerate(cleaned):
        for candidate in COLLECT_POWER_CANDIDATES:
            if item == candidate or item.endswith(candidate) or candidate in item:
                power_idx = idx
                break
        if power_idx >= 0:
            break
    if power_idx < 0:
        for idx, item in enumerate(cleaned):
            if (
                "npopulation" in item
                or "nompower" in item
                or "fissionpower" in item
            ):
                power_idx = idx
                break
    if power_idx < 0:
        raise ValueError(
            "Could not find a power column compatible with collectFreqNominal."
        )

    return time_idx, power_idx


def reduce_csv_to_collect_columns(csv_path: str) -> None:
    """Keep only the time and power columns needed by the collectors.

    Streams row-by-row so multi-GB low-power trajectories can be reduced
    without loading them into memory.
    """
    tmp_path = f"{csv_path}.tmp"
    try:
        with open(csv_path, newline="") as src, open(tmp_path, "w", newline="") as dst:
            reader = csv.reader(src)
            writer = csv.writer(dst)
            header = next(reader, None)
            if header is None:
                raise ValueError(f"CSV is empty: {csv_path}")

            time_idx, power_idx = find_collect_columns(header)
            writer.writerow([header[time_idx], header[power_idx]])

            max_idx = max(time_idx, power_idx)
            for row in reader:
                if not row or len(row) <= max_idx:
                    continue
                writer.writerow([row[time_idx], row[power_idx]])

        os.replace(tmp_path, csv_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def build_simflags(
    override: str,
    *,
    simflags_extra: str = "",
    reduced_csv_for_collect: bool = False,
) -> str:
    simflag_parts = [f"-override={override}"]
    simflags_extra = simflags_extra.strip()
    if reduced_csv_for_collect and "-variableFilter" not in simflags_extra:
        simflag_parts.append(
            f"-variableFilter={shlex.quote(REDUCED_CSV_VARIABLE_FILTER)}"
        )
    if simflags_extra:
        simflag_parts.append(simflags_extra)
    return " ".join(simflag_parts)


def cleanup_omc_artifacts_in_dir(work_path: str, keep_files: set[str]) -> None:
    for name in os.listdir(work_path):
        if name in keep_files:
            continue
        path = os.path.join(work_path, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def format_frequency_key(freq: float) -> str:
    """Format a frequency key exactly as persisted by the runner."""
    return f"{float(freq):.12g}"


def frequency_case_dir_name(freq: float) -> str:
    """Campaign case-directory name for one frequency slot.

    Built from the canonical :func:`format_frequency_key` (``.12g``: 12
    significant digits), so requested frequencies distinct at that
    precision occupy distinct slots (the historical zero-padded ``:08.5f``
    formatting collapsed distinct requested frequencies into one directory
    name, clobbering the first point's outputs). Two requested frequencies
    that coincide at 12 significant digits share a key by construction
    (``1.0`` vs ``1.0000000000001`` both format as ``"1"``); such
    duplicates fail closed at the duplicate-key refusal (sweep-manifest
    request validation and the collect-side mapping check) instead of
    silently sharing a slot.
    """
    return f"freq{format_frequency_key(freq)}"


def frequency_file_prefix(freq: float) -> str:
    """Result-CSV file prefix for one frequency slot (matches the dir name)."""
    return f"MSRR_{frequency_case_dir_name(freq)}"


def resolve_case_dir(results_dir: str, freq: float) -> str:
    """Resolve one frequency case directory, preferring the canonical name.

    Read-side helper: new campaigns name case directories from the canonical
    frequency key, while the frozen pre-fix published records keep their old
    zero-padded ``:08.5f`` names (never migrated). The canonical candidate
    wins when both exist; the legacy name is accepted so published trees
    stay plottable without renaming.
    """
    canonical = os.path.join(results_dir, frequency_case_dir_name(freq))
    if os.path.isdir(canonical):
        return canonical
    legacy = os.path.join(results_dir, f"freq{float(freq):08.5f}")
    if os.path.isdir(legacy):
        return legacy
    return canonical


def compute_effective_stop_time(
    freq_point: float,
    *,
    base_stop_time: float,
    ss_time: float,
    stop_time_mode: str,
    min_cycles_after_ss: float,
    settle_discard_s: float = 0.0,
) -> float:
    """Per-frequency stop time.

    ``fixed`` returns ``base_stop_time``.  ``min_cycles_after_ss`` returns
    ``max(base_stop_time, ceil(ss_time + settle_discard_s + N * 2*pi/omega))``:
    the fit window, which opens ``settle_discard_s`` after the perturbation
    start (settling discard, ``freq/fr_protocol.py``), still spans at least
    ``N = min_cycles_after_ss`` forcing periods.  ``settle_discard_s = 0``
    (the default) is the historical rule.
    """
    if stop_time_mode == "fixed":
        return float(base_stop_time)
    if stop_time_mode != "min_cycles_after_ss":
        raise ValueError(f"Unsupported stop_time_mode: {stop_time_mode}")
    if freq_point <= 0:
        raise ValueError("frequency must be positive for dynamic stop-time mode")
    if min_cycles_after_ss <= 0:
        raise ValueError("min_cycles_after_ss must be > 0 for dynamic stop-time mode")
    if settle_discard_s < 0 or not math.isfinite(settle_discard_s):
        raise ValueError("settle_discard_s must be finite and >= 0")

    required = (
        ss_time
        + float(settle_discard_s)
        + min_cycles_after_ss * (2.0 * math.pi / freq_point)
    )
    return float(max(base_stop_time, math.ceil(required)))


def compute_output_step(
    freq_point: float,
    *,
    output_interval_mode: str,
    output_intervals_per_second: float,
    output_samples_per_period: float,
    output_step_max: float,
) -> float:
    if output_intervals_per_second <= 0:
        raise ValueError("output_intervals_per_second must be > 0")

    output_step = 1.0 / output_intervals_per_second
    if output_interval_mode == "fixed_rate":
        return float(output_step)
    if output_interval_mode != "frequency_scaled":
        raise ValueError(
            f"Unsupported output_interval_mode: {output_interval_mode}"
        )
    if freq_point <= 0:
        raise ValueError("frequency must be positive for frequency-scaled output")
    if output_samples_per_period <= 0:
        raise ValueError(
            "output_samples_per_period must be > 0 for frequency-scaled output"
        )

    forcing_period = 2.0 * math.pi / freq_point
    output_step = max(output_step, forcing_period / output_samples_per_period)
    if output_step_max > 0:
        output_step = min(output_step, output_step_max)
    return float(output_step)


def compute_number_of_intervals(
    freq_point: float,
    *,
    stop_time: float,
    output_interval_mode: str,
    output_intervals_per_second: float,
    output_samples_per_period: float,
    output_step_max: float,
) -> int:
    if stop_time <= 0:
        raise ValueError("stop_time must be > 0 when computing output intervals")
    output_step = compute_output_step(
        freq_point,
        output_interval_mode=output_interval_mode,
        output_intervals_per_second=output_intervals_per_second,
        output_samples_per_period=output_samples_per_period,
        output_step_max=output_step_max,
    )
    return max(1, int(math.ceil(stop_time / output_step)))


def csv_reaches_stop_time(csv_path: str, stop_time: float) -> bool:
    """
    Return True if the CSV's last time value reaches (approximately) stop_time.
    Uses a light-weight backward tail read to avoid loading large files.

    The scan walks back from EOF until it brackets the newline that STARTS the
    final NON-EMPTY row (trailing newline(s) skipped), then parses that row
    WHOLE. A fixed-size window is not enough: SegmentedMSR 9R result rows are
    ~24 KB wide (four zone cores), so a 16 KB window slices mid-row and
    misparses a mid-row value as the time column.

    If no line boundary exists within ~1 MB of tail scan, the file is treated
    as not reaching stop_time (returns False). This is a documented behavior
    change on a path shared by legacy and segmented sweeps: the previous
    fixed-window implementation silently misparsed such pathological tails as
    a bogus time value instead of failing loudly; no collector depends on the
    old misparse (Ambiguity I, TASK-20260823-04).
    """
    try:
        with open(csv_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size <= 0:
                return False
            chunk_size = 16384
            max_scan = 1 << 20  # give up on pathological newline-free files
            pos = size
            data = b""
            found = False
            while pos > 0:
                if len(data) > max_scan:
                    return False
                step = min(chunk_size, pos)
                pos -= step
                handle.seek(pos)
                data = handle.read(step) + data
                # Ignore trailing newline(s) at EOF so rfind lands on the
                # newline BEFORE the final non-empty row, not the terminator.
                idx = data.rstrip(b"\n").rfind(b"\n")
                if idx != -1:
                    found = True
                    break
            if not found:
                # No line boundary anywhere: single-line/header-less/corrupt
                # file (the previous implementation also rejected files with
                # fewer than two lines).
                return False
            handle.seek(pos + idx + 1)
            last = handle.read().decode("utf-8", errors="ignore")
        last = last.strip()
        if not last:
            return False
        last_time = float(last.split(",")[0].strip().strip('"'))
        return last_time >= 0.995 * stop_time
    except Exception:
        return False


def load_steady_state_overrides(
    table_path: str,
    power: float,
    heat_loss: int,
    *,
    allowed_override_keys: set[str],
    policy: str = DEFAULT_POLICY,
) -> dict[str, float]:
    """Select the steady-state override row(s) for *power* from *table_path*.

    Selection (unchanged by TASK-20260910-01 P3): exact power match, else
    the endpoint row when *power* lies outside the table, else linear
    interpolation between the bracketing pair.  Provenance gate (added by
    P3, via the shared ``helpers/setpoint_provenance.py`` contract): the
    raw rows the selection logic consumes -- the exact-match row, the
    endpoint row, or BOTH rows of the bracketing interpolation pair -- are
    checked BEFORE any return or interpolation, so a ``qualified=0`` row
    can no longer be silently dropped or interpolated around.  Tables
    without the ``qualified`` column follow the *policy* parameter (the
    shared module's branch (a), the only policy-dependent branch):
    ``strict`` (the default) refuses the load naming the CSV;
    ``legacy-compatible`` warns loudly on stderr and proceeds, exactly
    like ``transients.run_nonlinear_steps.load_setpoints``.  Policy
    selection is an explicit library parameter -- there is no CLI flag
    for it.
    """
    rows: list[tuple[float, dict[str, float], dict[str, str]]] = []

    with open(table_path, newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "power" not in reader.fieldnames:
            raise ValueError(
                f"Steady-state table {table_path} must contain a 'power' column."
            )

        has_heat_loss = HEAT_LOSS_COLUMN in reader.fieldnames
        has_qualified = QUALIFIED_COLUMN in reader.fieldnames
        for raw_row in reader:
            if not raw_row or raw_row.get("power", "").strip() == "":
                continue
            row_power = float(raw_row["power"])
            if has_heat_loss:
                raw_hl = (raw_row.get(HEAT_LOSS_COLUMN) or "").strip()
                if raw_hl == "":
                    continue
                try:
                    row_hl = int(float(raw_hl))
                except ValueError:
                    row_hl = 1 if raw_hl.lower() in ("true", "yes") else 0
                if row_hl != int(heat_loss):
                    continue
            values: dict[str, float] = {}
            for key, value in raw_row.items():
                if key in ("power", HEAT_LOSS_COLUMN) or value is None or value.strip() == "":
                    continue
                override_name = TABLE_OVERRIDE_NAME_MAP.get(key, key)
                if override_name not in allowed_override_keys:
                    continue
                values[override_name] = float(value)
            if has_heat_loss:
                values[HEAT_LOSS_COLUMN] = bool(int(heat_loss))
            rows.append((row_power, values, raw_row))

    if not rows:
        raise ValueError(f"Steady-state table {table_path} has no usable data rows.")

    rows.sort(key=lambda item: item[0])

    # Provenance gate (TASK-20260910-01 P3): a legacy table without the
    # 'qualified' column follows the setpoint policy (TASK-20260911-01 P4)
    # via the shared module: strict (the default) refuses the load naming
    # the CSV; legacy-compatible warns loudly on stderr and proceeds. A
    # qualified table gates every raw row the selection logic below is
    # about to consume, BEFORE any return or interpolation.
    if not has_qualified:
        handle_missing_verdict_column(table_path, policy=policy)
    # The table must belong to the current lumped model (its model-version
    # sidecar; helpers.setpoint_model_version): strict refuses a stale table.
    check_table_model_version(table_path, policy=policy)

    def _gate_row(raw_row: dict[str, str]) -> None:
        if has_qualified:
            require_qualified_row(raw_row, table_path, power)

    for row_power, values, raw_row in rows:
        if abs(row_power - power) < 1e-12:
            _gate_row(raw_row)
            return dict(values)

    if power <= rows[0][0]:
        _gate_row(rows[0][2])
        return dict(rows[0][1])
    if power >= rows[-1][0]:
        _gate_row(rows[-1][2])
        return dict(rows[-1][1])

    for idx in range(1, len(rows)):
        low_power, low_values, low_raw = rows[idx - 1]
        high_power, high_values, high_raw = rows[idx]
        if low_power <= power <= high_power:
            _gate_row(low_raw)
            _gate_row(high_raw)
            span = high_power - low_power
            if span <= 0:
                return dict(low_values)
            weight = (power - low_power) / span
            result: dict[str, float] = {}
            for key, low_value in low_values.items():
                if key not in high_values:
                    continue
                if key == HEAT_LOSS_COLUMN:
                    result[key] = low_value
                    continue
                high_value = high_values[key]
                result[key] = low_value + weight * (high_value - low_value)
            return result

    _gate_row(rows[-1][2])
    return dict(rows[-1][1])


def format_override_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return f"{float(value):.16g}"


def clean_column_headers(columns):
    """Remove conflicting characters from OpenModelica CSV column headers."""
    header_str = str(list(columns))
    chars_to_remove = ['[', ']', '.', '(', ')', '_', "'"]
    rx = '[' + re.escape(''.join(chars_to_remove)) + ']'
    cleaned = re.sub(rx, '', header_str)
    return cleaned.replace(" ", "").split(',')


def read_run_params(results_dir):
    """Read the run parameters file saved by runFreqNominal*.py."""
    params = {}
    params_file = os.path.join(results_dir, "run_params.txt")
    if os.path.exists(params_file):
        with open(params_file, "r") as pf:
            for line in pf:
                parts = line.strip().split('\t')
                if len(parts) == 2:
                    try:
                        params[parts[0]] = float(parts[1])
                    except ValueError:
                        params[parts[0]] = parts[1]
    return params


def find_power_column(cleaned_columns) -> str | None:
    """Locate the power-signal column among cleaned CSV headers.

    Order: the legacy candidates, the legacy substring fallback, then the
    segmented contract column (:data:`SEGMENTED_POWER_COLUMN_CANDIDATES`),
    so legacy result headers resolve exactly as before.
    """
    for candidate in POWER_COLUMN_CANDIDATES:
        if candidate in cleaned_columns:
            return candidate
    for col in cleaned_columns:
        col_lower = col.lower()
        if 'npopulation' in col_lower or 'nompower' in col_lower:
            return col
    for candidate in SEGMENTED_POWER_COLUMN_CANDIDATES:
        if candidate in cleaned_columns:
            return candidate
    return None


def wrap_phase_rad(phase_rad: float) -> float:
    """Wrap phase into [-pi, pi) for stable reporting."""
    return float((phase_rad + math.pi) % (2.0 * math.pi) - math.pi)


def phase_relative_to_perturbation_start_deg(
    phase_rad: float,
    freq_point: float,
    fit_start: float,
    perturbation_start: float,
) -> float:
    """
    Convert a fitted phase referenced to fit_start into a phase referenced to
    the forcing activation time (perturbationStartTime / ss_time).
    """
    phase_relative = phase_rad - freq_point * (fit_start - perturbation_start)
    return float(math.degrees(wrap_phase_rad(phase_relative)))


# Rejection-reason tokens emitted by fit_sine_least_squares (stable prefixes
# so callers and tests can match on category):
REJECT_INVALID_FREQUENCY = "invalid_frequency"
REJECT_INSUFFICIENT_SAMPLES = "insufficient_samples"
REJECT_SUB_CYCLE_WINDOW = "sub_cycle_window"
REJECT_ILL_CONDITIONED = "ill_conditioned"


@dataclass
class SineFitResult:
    """Diagnostics recorded for one fixed-frequency sine fit.

    The fitted model is ``y(t) = c0 + c1*(t - t_c) + c2*(t - t_c)**2 +
    a*sin(w*t) + b*cos(w*t)`` with ``t_c`` the midpoint of the samples passed
    to the fitter, expressed in the caller's time base (absolute simulation
    seconds, or seconds relative to ``fit_start`` if the caller pre-shifted
    its timestamps). ``trend_order`` is the polynomial order of the trend
    (0: offset only, 1: linear, 2: quadratic); ``c1`` / ``c2`` are 0 when
    the corresponding term is not fitted.

    ``fit_start`` / ``fit_end`` are the first/last sample times in that same
    caller time base. Amplitude is ``hypot(a, b)`` and phase follows the
    historical convention ``arctan2(b, a)``, i.e. the fit represents
    ``A*sin(w*t + phase)``; downstream code converts it relative to the
    perturbation start via :func:`phase_relative_to_perturbation_start_deg`.

    Residual diagnostics come in two explicit pairs. ``residual_rms`` /
    ``r_squared`` are computed from the same residuals the solve minimized:
    raw-sample residuals when the timestamp grid was treated as uniform, and
    interval-weighted residuals when ``weighted_intervals`` is true — the
    primary pair always matches the fit's own objective.
    ``residual_rms_unweighted`` / ``r_squared_unweighted`` always carry the
    raw-sample values, so a weighted fit reports both views; the pairs
    coincide whenever ``weighted_intervals`` is false. Both pairs are
    populated whenever the solve produced coefficients — including an
    ``ill_conditioned`` rejection — and stay NaN on the earlier rejections.
    """

    amplitude: float | None = None
    phase_rad: float | None = None
    a_sin: float = math.nan
    b_cos: float = math.nan
    c0: float = math.nan
    c1: float = 0.0
    c2: float = 0.0
    freq_rad_s: float = math.nan
    n_samples: int = 0
    n_cycles: float = math.nan
    fit_start: float = math.nan
    fit_end: float = math.nan
    residual_rms: float = math.nan
    r_squared: float = math.nan
    residual_rms_unweighted: float = math.nan
    r_squared_unweighted: float = math.nan
    condition_number: float = math.nan
    uniformity_metric: float = 0.0
    weighted_intervals: bool = False
    trend_enabled: bool = False
    trend_order: int = 0
    rejection_reason: str = ""

    @property
    def ok(self) -> bool:
        return self.rejection_reason == "" and self.amplitude is not None

    def window_mean_level(self) -> float:
        """Mean of the fitted trend polynomial over ``[fit_start, fit_end]``.

        ``c0`` for trend orders 0 and 1 (the linear term averages to zero
        over the centered window); ``c0 + c2 * h**2 / 3`` for the quadratic
        trend, ``h`` being the half-span.  This is the mean operating level
        the forced response rides on, used by the ``window_mean_power`` gain
        reference (freq/README.md, "Drift regime").
        """
        half_span = 0.5 * (float(self.fit_end) - float(self.fit_start))
        return float(self.c0) + float(self.c2) * half_span * half_span / 3.0

    def predict(self, times) -> np.ndarray:
        """Evaluate the fitted model at the given times (caller time base)."""
        t_arr = np.asarray(times, dtype=float)
        if not self.ok:
            raise ValueError(
                f"cannot predict from rejected sine fit: {self.rejection_reason}"
            )
        w = float(self.freq_rad_s)
        t_c = 0.5 * (self.fit_start + self.fit_end)
        return (
            self.c0
            + self.c1 * (t_arr - t_c)
            + self.c2 * (t_arr - t_c) ** 2
            + self.a_sin * np.sin(w * t_arr)
            + self.b_cos * np.cos(w * t_arr)
        )


def _rejected_fit(reason: str, **overrides) -> SineFitResult:
    result = SineFitResult(rejection_reason=reason)
    known_fields = set(SineFitResult.__dataclass_fields__)
    for key, value in overrides.items():
        if key not in known_fields:
            raise TypeError(
                f"_rejected_fit got unknown SineFitResult field: {key!r}"
            )
        setattr(result, key, value)
    return result


def trapezoid_sample_weights(dt: np.ndarray) -> np.ndarray:
    """Trapezoidal quadrature weights for ``n = len(dt) + 1`` samples.

    Endpoints receive half of the single adjacent interval; each interior
    sample receives half of each neighboring interval::

        weights[0]    = 0.5 * dt[0]
        weights[-1]   = 0.5 * dt[-1]
        weights[1:-1] = 0.5 * (dt[:-1] + dt[1:])

    ``dt`` must contain only positive intervals; the caller rejects
    nonpositive gaps before calling this helper.
    """
    dt_arr = np.asarray(dt, dtype=float).ravel()
    if dt_arr.size == 0:
        raise ValueError("trapezoid_sample_weights requires at least one interval")
    n = int(dt_arr.size) + 1
    weights = np.empty(n, dtype=float)
    weights[0] = 0.5 * dt_arr[0]
    weights[-1] = 0.5 * dt_arr[-1]
    if n > 2:
        weights[1:-1] = 0.5 * (dt_arr[:-1] + dt_arr[1:])
    return weights


def resolve_trend_order(fit_trend: bool = False, trend_order: int | None = None) -> int:
    """Effective polynomial trend order (``trend_order`` wins over ``fit_trend``).

    ``fit_trend=True`` is the historical linear trend (order 1); an explicit
    ``trend_order`` of 0, 1, or 2 selects the offset-only, linear, or
    quadratic trend.  Anything else is refused.
    """
    if trend_order is None:
        return 1 if fit_trend else 0
    order = int(trend_order)
    if order not in (0, 1, 2):
        raise ValueError(f"trend_order must be 0, 1, or 2 (got {trend_order!r})")
    return order


def fit_sine_least_squares(
    time_data,
    power_data,
    freq_point: float,
    *,
    fit_trend: bool = False,
    trend_order: int | None = None,
    uniformity_tol: float = 1e-6,
    min_samples: int = 10,
    min_cycles: float = 0.25,
    max_condition_number: float = 1e8,
) -> SineFitResult:
    """Fit a fixed-frequency sine by simultaneous linear least squares.

    Model::

        y(t) = c0 + c1*(t - t_c) [+ c2*(t - t_c)**2] + a*sin(w*t) + b*cos(w*t)

    with ``w = freq_point``.  The quadratic term (``trend_order=2``) removes
    the slow free-mode drift of drift-regime points (freq/README.md,
    "Drift regime"); its design columns are built on the normalized time
    ``(t - t_c) / h`` (``h`` the half-span) so the condition number stays
    comparable to the offset-only fit, and ``c1`` / ``c2`` are converted
    back to per-second units.  Orders 0 and 1 keep the historical columns
    byte for byte.

    replacing the historical mean-subtract + ``(2/N) y*{sin,cos}`` projections,
    which are biased whenever the fit window does not span an integer number of
    forcing periods or carries a DC offset correlated with the window edges.
    Here ``c0`` is always estimated together with ``a, b`` (never
    mean-subtract-then-project), and the linear trend term ``c1`` is optional
    (default off). ``t_c`` is the midpoint of the fit-window samples used;
    centering only affects the interpretation/conditioning of ``c0`` and ``c1``
    — the fitted curve, amplitude ``A = hypot(a, b)``, and phase
    ``arctan2(b, a)`` are invariant to that choice.

    Parameters
    ----------
    time_data, power_data:
        Sample times (sorted ascending, caller's time base — absolute seconds
        or seconds after ``fit_start``) and signal values of equal length.
    freq_point:
        Fixed angular frequency ``w`` (rad/s).
    fit_trend:
        Include the linear trend term ``c1*(t - t_c)``. Default off.
    trend_order:
        Explicit trend order (0, 1, or 2); overrides ``fit_trend`` when given
        (see :func:`resolve_trend_order`).
    uniformity_tol:
        Nonuniformity threshold for the timestamp grid. When
        ``max(|dt / median(dt) - 1|)`` exceeds it, the solve uses trapezoidal
        interval-length weights so irregular grids (large gaps *or* clustered
        small steps) do not over-weight dense regions; otherwise all samples
        weigh equally. Duplicate timestamps (solvers emit pre-/post-event
        rows at the same stamp on event boundaries) are collapsed to the
        last row of each duplicate run rather than weighted.
    min_samples, min_cycles, max_condition_number:
        Configurable acceptance floors. A fit is rejected with an explicit
        reason when ``n_samples < min_samples`` (after duplicate-timestamp
        collapse), when the sample span covers fewer than ``min_cycles``
        forcing periods (``n_cycles = (t_max - t_min) / (2*pi/w)``), or when
        the condition number of the (row-scaled) design matrix exceeds
        ``max_condition_number``.

    Returns
    -------
    SineFitResult
        Always returned; check ``.ok`` / ``.rejection_reason`` before using
        ``amplitude`` / ``phase_rad``. Diagnostics already computed for a
        rejected fit (e.g. the condition number on an ill-conditioned design)
        stay populated. ``residual_rms`` / ``r_squared`` always refer to the
        objective the solve minimized — weighted residuals when interval
        weights are active (``weighted_intervals`` true), raw-sample
        residuals otherwise — while ``residual_rms_unweighted`` /
        ``r_squared_unweighted`` keep the raw-sample values for comparison
        (identical to the primary pair when no weighting was applied).
    """
    poly_order = resolve_trend_order(fit_trend, trend_order)
    w = float(freq_point)
    t_all = np.asarray(time_data, dtype=float).ravel()
    y_all = np.asarray(power_data, dtype=float).ravel()

    finite = np.isfinite(t_all) & np.isfinite(y_all)
    t = t_all[finite]
    y = y_all[finite]
    # Keep samples sorted ascending regardless of caller order.
    order = np.argsort(t, kind="stable")
    t = t[order]
    y = y[order]
    # Solvers emit pre-/post-event rows at the same timestamp on event
    # boundaries; keep the last row of each duplicate run (the post-event
    # state) so the fit runs on a strictly increasing time grid.
    if t.size > 1:
        keep = np.concatenate((t[1:] != t[:-1], [True]))
        t = t[keep]
        y = y[keep]

    n_samples = int(t.size)

    if not math.isfinite(w) or w <= 0.0:
        return _rejected_fit(
            REJECT_INVALID_FREQUENCY,
            freq_rad_s=w,
            n_samples=n_samples,
            trend_enabled=poly_order >= 1,
            trend_order=poly_order,
        )

    period = 2.0 * math.pi / w

    if n_samples == 0:
        return _rejected_fit(
            REJECT_INSUFFICIENT_SAMPLES,
            freq_rad_s=w,
            n_samples=0,
            trend_enabled=poly_order >= 1,
            trend_order=poly_order,
        )

    fit_start = float(t[0])
    fit_end = float(t[-1])
    n_cycles = (fit_end - fit_start) / period if period > 0 else math.inf

    if n_samples < int(min_samples):
        return _rejected_fit(
            REJECT_INSUFFICIENT_SAMPLES,
            freq_rad_s=w,
            n_samples=n_samples,
            n_cycles=n_cycles,
            fit_start=fit_start,
            fit_end=fit_end,
            trend_enabled=poly_order >= 1,
            trend_order=poly_order,
        )

    if n_cycles < float(min_cycles):
        return _rejected_fit(
            REJECT_SUB_CYCLE_WINDOW,
            amplitude=None,
            freq_rad_s=w,
            n_samples=n_samples,
            n_cycles=n_cycles,
            fit_start=fit_start,
            fit_end=fit_end,
            trend_enabled=poly_order >= 1,
            trend_order=poly_order,
        )

    dt = np.diff(t)
    if dt.size == 0:
        # A single surviving sample carries no interval information.  The
        # uniformity metric below needs at least one interval; np.max of the
        # empty difference array would raise ValueError and break the
        # documented always-returns-a-result contract, so reject explicitly.
        return _rejected_fit(
            REJECT_INSUFFICIENT_SAMPLES,
            freq_rad_s=w,
            n_samples=n_samples,
            n_cycles=n_cycles,
            fit_start=fit_start,
            fit_end=fit_end,
            trend_enabled=poly_order >= 1,
            trend_order=poly_order,
        )
    dt_median = float(np.median(dt))
    # dt is strictly positive here: the grid is sorted and duplicate
    # timestamps were collapsed above.

    uniformity_metric = float(np.max(np.abs(dt / dt_median - 1.0)))
    weights: np.ndarray | None = None
    if uniformity_metric > float(uniformity_tol):
        # Trapezoidal cell widths: endpoints get half the adjacent interval,
        # interior samples get half of each neighbor. Normalize to mean 1 so
        # the design scale stays comparable to the uniform (unweighted) case.
        weights = trapezoid_sample_weights(dt)
        weights /= weights.mean()

    t_c = 0.5 * (fit_start + fit_end)
    half_span = 0.5 * (fit_end - fit_start)
    columns = [np.ones_like(t)]
    if poly_order == 1:
        columns.append(t - t_c)
    elif poly_order == 2:
        tau = (t - t_c) / half_span
        columns.extend((tau, tau * tau))
    columns.extend((np.sin(w * t), np.cos(w * t)))
    design = np.column_stack(columns)

    if weights is None:
        scaled_y = y
    else:
        sqrt_w = np.sqrt(weights)
        design = design * sqrt_w[:, None]
        scaled_y = y * sqrt_w
    cond = float(np.linalg.cond(design))

    coefficients, _, _, _ = np.linalg.lstsq(design, scaled_y, rcond=None)

    if weights is None:
        fitted = design @ coefficients
    else:
        fitted = np.column_stack(columns) @ coefficients
    residuals = y - fitted

    # Raw-sample diagnostics (no interval weighting), always recorded so a
    # weighted fit exposes both views of the same residuals.
    ss_res_u = float(np.sum(residuals**2))
    ss_tot_u = float(np.sum((y - y.mean()) ** 2))
    residual_rms_unweighted = math.sqrt(ss_res_u / n_samples)
    r_squared_unweighted = 1.0 - (ss_res_u / ss_tot_u) if ss_tot_u > 0 else 0.0

    # Primary diagnostics match the objective the solve minimized: with
    # interval weights active, sqrt(w)*residual is the vector lstsq received,
    # so RMS and R^2 are weighted means over the same residuals (weights are
    # normalized to mean 1, hence sum(w) == n_samples). Without weights the
    # primary pair is the raw-sample pair.
    if weights is None:
        residual_rms = residual_rms_unweighted
        r_squared = r_squared_unweighted
    else:
        ss_res_w = float(np.sum(weights * residuals**2))
        residual_rms = math.sqrt(ss_res_w / n_samples)
        y_bar_w = float(np.sum(weights * y)) / n_samples
        ss_tot_w = float(np.sum(weights * (y - y_bar_w) ** 2))
        r_squared = 1.0 - (ss_res_w / ss_tot_w) if ss_tot_w > 0 else 0.0

    if not math.isfinite(cond) or cond > float(max_condition_number):
        return _rejected_fit(
            REJECT_ILL_CONDITIONED,
            freq_rad_s=w,
            n_samples=n_samples,
            n_cycles=n_cycles,
            fit_start=fit_start,
            fit_end=fit_end,
            residual_rms=residual_rms,
            r_squared=r_squared,
            residual_rms_unweighted=residual_rms_unweighted,
            r_squared_unweighted=r_squared_unweighted,
            condition_number=cond,
            uniformity_metric=uniformity_metric,
            weighted_intervals=weights is not None,
            trend_enabled=poly_order >= 1,
            trend_order=poly_order,
        )

    index = 1 + poly_order
    a_sin = float(coefficients[index])
    b_cos = float(coefficients[index + 1])
    c0 = float(coefficients[0])
    c1 = float(coefficients[1]) if poly_order == 1 else 0.0
    c2 = 0.0
    if poly_order == 2:
        c1 = float(coefficients[1]) / half_span
        c2 = float(coefficients[2]) / (half_span * half_span)

    return SineFitResult(
        amplitude=float(math.hypot(a_sin, b_cos)),
        phase_rad=float(math.atan2(b_cos, a_sin)),
        a_sin=a_sin,
        b_cos=b_cos,
        c0=c0,
        c1=c1,
        c2=c2,
        freq_rad_s=w,
        n_samples=n_samples,
        n_cycles=n_cycles,
        fit_start=fit_start,
        fit_end=fit_end,
        residual_rms=residual_rms,
        r_squared=r_squared,
        residual_rms_unweighted=residual_rms_unweighted,
        r_squared_unweighted=r_squared_unweighted,
        condition_number=cond,
        uniformity_metric=uniformity_metric,
        weighted_intervals=weights is not None,
        trend_enabled=poly_order >= 1,
        trend_order=poly_order,
    )


# ---------------------------------------------------------------------------
# Fit-window convergence (settling) metrics
# ---------------------------------------------------------------------------
#
# A point is accepted as settled only when two consecutive sub-windows of
# its fit window (the two halves) agree.  The gain comparison normalizes
# each half's amplitude by that half's fitted mean power level (c0): at low
# power the PKE is bilinear (response amplitude proportional to the current
# mean population), so a slow drift of the operating point after the sine
# switch-on would otherwise masquerade as an amplitude drift.  The
# operating-point offset itself (|c0/P - 1|, with P the manifest power) is a
# separate criterion, because a constant offset biases the reported
# absolute gain without making the halves disagree.

#: Default acceptance tolerances (see freq/README.md "Convergence check").
CONVERGENCE_GAIN_TOL = 0.005
CONVERGENCE_PHASE_TOL_DEG = 0.5
CONVERGENCE_OPERATING_POINT_TOL = 0.005
#: Operating-point bound for points whose gain is referenced to the fit
#: window's mean power (drift-regime points, ``gain_reference =
#: window_mean_power``).  There the mean-power excursion -- the switch-on
#: free mode plus the second-order rectification of the forcing (the
#: kinetics term rho * n averages to a DC reactivity drho * s * cos(phi) / 2,
#: up to ~12 % of P at 1e-5 MW and 10 rad/s) -- no longer biases the gain:
#: at omega >= 120 omega_n the relative transfer function depends on the
#: power only through the loop gain (<= 7e-5), so an excursion eps changes
#: the gain by <= 7e-5 * eps.  The bound only guards the linearization
#: (freq/README.md, "Drift regime").
CONVERGENCE_DRIFT_OPERATING_POINT_TOL = 0.2

CONVERGENCE_REASON_GAIN = "halves_gain"
CONVERGENCE_REASON_PHASE = "halves_phase"
CONVERGENCE_REASON_OPERATING_POINT = "operating_point"
CONVERGENCE_REASON_NOT_EVALUABLE = "not_evaluable"


def _prepare_fit_grid(time_data, power_data) -> tuple[np.ndarray, np.ndarray]:
    """Finite, sorted, duplicate-collapsed samples (same rules as the fitter)."""
    t_all = np.asarray(time_data, dtype=float).ravel()
    y_all = np.asarray(power_data, dtype=float).ravel()
    finite = np.isfinite(t_all) & np.isfinite(y_all)
    t = t_all[finite]
    y = y_all[finite]
    order = np.argsort(t, kind="stable")
    t = t[order]
    y = y[order]
    if t.size > 1:
        keep = np.concatenate((t[1:] != t[:-1], [True]))
        t = t[keep]
        y = y[keep]
    return t, y


def harmonic_ratio(
    time_data,
    power_data,
    freq_point: float,
    *,
    fit_trend: bool = False,
    trend_order: int | None = None,
    uniformity_tol: float = 1e-6,
) -> float:
    """Second-to-first harmonic amplitude ratio ``|H2| / |H1|`` of one window.

    Simultaneous least squares of ``c0 [+ trend] + sum_{k=1,2} a_k sin(k w
    t) + b_k cos(k w t)`` with the same trend-order and interval weighting
    rules as :func:`fit_sine_least_squares`.  Returns NaN when the window
    cannot carry the fit (fewer than 10 samples or under one forcing period).
    """
    order = resolve_trend_order(fit_trend, trend_order)
    w = float(freq_point)
    t, y = _prepare_fit_grid(time_data, power_data)
    if t.size < 10 or not math.isfinite(w) or w <= 0:
        return math.nan
    if (t[-1] - t[0]) * w / (2.0 * math.pi) < 1.0:
        return math.nan
    t_c = 0.5 * (t[0] + t[-1])
    columns = [np.ones_like(t)]
    if order == 1:
        columns.append(t - t_c)
    elif order == 2:
        tau = (t - t_c) / (0.5 * (t[-1] - t[0]))
        columns.extend((tau, tau * tau))
    for k in (1, 2):
        columns.extend((np.sin(k * w * t), np.cos(k * w * t)))
    design = np.column_stack(columns)
    dt = np.diff(t)
    dt_median = float(np.median(dt))
    scaled_y = y
    if float(np.max(np.abs(dt / dt_median - 1.0))) > float(uniformity_tol):
        weights = trapezoid_sample_weights(dt)
        weights /= weights.mean()
        sqrt_w = np.sqrt(weights)
        design = design * sqrt_w[:, None]
        scaled_y = y * sqrt_w
    coefficients, _, _, _ = np.linalg.lstsq(design, scaled_y, rcond=None)
    base = 1 + order
    h1 = math.hypot(float(coefficients[base]), float(coefficients[base + 1]))
    h2 = math.hypot(float(coefficients[base + 2]), float(coefficients[base + 3]))
    if h1 <= 0 or not math.isfinite(h1):
        return math.nan
    return float(h2 / h1)


def fit_window_convergence(
    time_data,
    power_data,
    freq_point: float,
    *,
    fit_trend: bool = False,
    trend_order: int | None = None,
    min_samples: int = 10,
    min_cycles: float = 0.25,
    max_condition_number: float = 1e8,
) -> dict:
    """Two-halves agreement metrics for one fit window.

    The window's samples (caller time base, as passed to the fitter) are
    split at the midpoint time and each half is fitted with
    :func:`fit_sine_least_squares` under the same options.  Returns a dict
    with ``evaluated`` (both halves fitted), ``reason`` (why not, when not),
    ``gain_rel_diff`` (``(A2/c0_2) / (A1/c0_1) - 1``), ``gain_rel_diff_raw``
    (``A2/A1 - 1``), ``phase_diff_deg`` (second minus first half, wrapped to
    [-180, 180)), ``h2_h1_ratio`` (full window, :func:`harmonic_ratio`), and
    the per-half cycle counts.
    """
    t, y = _prepare_fit_grid(time_data, power_data)
    out = {
        "evaluated": False,
        "reason": "",
        "gain_rel_diff": math.nan,
        "gain_rel_diff_raw": math.nan,
        "phase_diff_deg": math.nan,
        "h2_h1_ratio": math.nan,
        "half_cycles": math.nan,
    }
    if t.size < 2:
        out["reason"] = "window has fewer than two samples"
        return out
    order = resolve_trend_order(fit_trend, trend_order)
    out["h2_h1_ratio"] = harmonic_ratio(t, y, freq_point, trend_order=order)
    t_mid = 0.5 * (float(t[0]) + float(t[-1]))
    first = t <= t_mid
    second = t >= t_mid
    kwargs = dict(
        trend_order=order,
        min_samples=min_samples,
        min_cycles=min_cycles,
        max_condition_number=max_condition_number,
    )
    fit_a = fit_sine_least_squares(t[first], y[first], freq_point, **kwargs)
    fit_b = fit_sine_least_squares(t[second], y[second], freq_point, **kwargs)
    out["half_cycles"] = float(fit_a.n_cycles) if math.isfinite(fit_a.n_cycles) else math.nan
    if not fit_a.ok or not fit_b.ok:
        out["reason"] = (
            "half-window fit rejected: "
            f"{fit_a.rejection_reason or 'ok'} / {fit_b.rejection_reason or 'ok'}"
        )
        return out
    amp_a = float(fit_a.amplitude)
    amp_b = float(fit_b.amplitude)
    if amp_a <= 0 or not math.isfinite(amp_a):
        out["reason"] = "first-half amplitude is zero"
        return out
    out["gain_rel_diff_raw"] = amp_b / amp_a - 1.0
    # Each half's amplitude is normalized by that half's mean operating
    # level (c0 for trend orders 0/1; the quadratic trend's window mean).
    level_a = fit_a.window_mean_level()
    level_b = fit_b.window_mean_level()
    if level_a != 0 and level_b != 0 and math.isfinite(level_a) and math.isfinite(level_b):
        out["gain_rel_diff"] = (amp_b / level_b) / (amp_a / level_a) - 1.0
    else:
        out["gain_rel_diff"] = out["gain_rel_diff_raw"]
    out["phase_diff_deg"] = math.degrees(
        wrap_phase_rad(float(fit_b.phase_rad) - float(fit_a.phase_rad))
    )
    out["evaluated"] = True
    return out


def convergence_verdict(
    metrics: dict,
    *,
    operating_point_offset: float | None,
    gain_tol: float = CONVERGENCE_GAIN_TOL,
    phase_tol_deg: float = CONVERGENCE_PHASE_TOL_DEG,
    operating_point_tol: float = CONVERGENCE_OPERATING_POINT_TOL,
) -> tuple[bool, str]:
    """Accept a point only when its halves agree and its operating point holds.

    Returns ``(converged, reason)``; ``reason`` is empty when converged and
    otherwise a ``;``-joined list of failed criteria
    (:data:`CONVERGENCE_REASON_GAIN`, :data:`CONVERGENCE_REASON_PHASE`,
    :data:`CONVERGENCE_REASON_OPERATING_POINT`, or
    :data:`CONVERGENCE_REASON_NOT_EVALUABLE`).  ``operating_point_offset``
    is ``c0 / P - 1``; ``None`` skips that criterion (no reference power).
    """
    if not metrics.get("evaluated"):
        detail = metrics.get("reason") or "halves not fitted"
        return False, f"{CONVERGENCE_REASON_NOT_EVALUABLE}: {detail}"
    failed: list[str] = []
    gain_diff = float(metrics.get("gain_rel_diff", math.nan))
    phase_diff = float(metrics.get("phase_diff_deg", math.nan))
    if not math.isfinite(gain_diff) or abs(gain_diff) > float(gain_tol):
        failed.append(CONVERGENCE_REASON_GAIN)
    if not math.isfinite(phase_diff) or abs(phase_diff) > float(phase_tol_deg):
        failed.append(CONVERGENCE_REASON_PHASE)
    if operating_point_offset is not None and (
        not math.isfinite(float(operating_point_offset))
        or abs(float(operating_point_offset)) > float(operating_point_tol)
    ):
        failed.append(CONVERGENCE_REASON_OPERATING_POINT)
    return (not failed), ";".join(failed)


def matlab_scalar(value) -> str:
    """Format one scalar as a MATLAB/Octave literal (numpy-2 safe)."""
    v = float(value)
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    return f"{v:.17g}"


def format_matlab_assignment(name: str, values) -> str:
    """Format ``name = [...];`` for a MATLAB .m output file."""
    formatted = " ".join(matlab_scalar(value) for value in values)
    return f"{name} = [{formatted}];\n"
