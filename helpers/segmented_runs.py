"""Segmented-run shared mapping + standalone mos-builder helpers
(TASK-20260823-02 phase S1; plan §15 bullet 1 "scenario variants ... runner
integration").

Single source of truth for driving the standalone SegmentedMSR package from
the three runner families (freq / transients / startup):

- ``core_model`` key -> segmented vehicle class names (full-loop trim rigs and
  the S1 UHX-trip wrapper pair);
- the standalone library file list (``SegmentedMSR.mo`` loads NOTHING else --
  no SMD load, no double-load; the legacy startup builder emits two loadFile
  lines, which segmented mode must not repeat);
- mos-text builders for check sweeps and buildModel smoke sessions;
- segmented CSV column candidates (fixed overlay columns + component signal
  names) for the downstream plot-side integration (S3).

Legacy-compat contract: runners keep their default legacy behavior untouched;
this module is imported ONLY on their segmented code path (CLI switch lands in
phases S2-S4). Structural-vs-runtime rule: variants needing different ARRAY
DIMENSIONS than the rig defaults (``numUhxSteps`` et al.) must use dedicated
wrapper MODELS (``R{1,9}MSRRuhxTripThermalSS``); everything else rides runtime
``-override=`` scalars or existing array elements.

Temperature-unit convention (TASK-20260825-04, commit 2a43887): the
SegmentedMSR library is fully kelvin, so EVERY temperature-bearing column of
a segmented result CSV reports kelvin (``TF1``/``TF2``/``TG``,
``TinCore``/``ToutCore``, ``TZout[i]``, ``TPot``, ``ToutPlenum``). Consumers
that render in degC must convert K->degC at their own boundary. The setpoint
tables under ``core/init/`` remain degC and are NOT loaded by any segmented
runner today; any future loader that starts reading them must convert
degC->K at that boundary.

Run (from repo root)::

    python3.12 -c "import helpers.segmented_runs as m; print(m.MODEL_BY_CORE)"
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Library files (standalone package -- exactly one loadFile line per run).
# ---------------------------------------------------------------------------
SEGMENTED_PACKAGE_FILE = "SegmentedMSR.mo"

#: The complete library file list for segmented runs (tuple so callers cannot
#: mutate a shared list by accident).
SEGMENTED_LIBRARY_FILES: tuple[str, ...] = (SEGMENTED_PACKAGE_FILE,)

#: Canonical core keys accepted across the runners.
CORE_KEYS: tuple[str, ...] = ("1r", "9r")

# ---------------------------------------------------------------------------
# Vehicle names (core_model -> SegmentedMSR.Reactors classes).
# ---------------------------------------------------------------------------
#: Full-loop trimmed rigs -- freq sweeps, step/flow transients, startup rides.
MODEL_BY_CORE: dict[str, str] = {
    "1r": "SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS",
    "9r": "SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS",
}

#: UHX-demand trip vehicles (S1 wrapper pair; structural ``numUhxSteps = 2``
#: with demand {1e6 -> 0} W at t = {0, 4000} s and DHRS engagement at 4000 s,
#: mirroring core/MSRR.mo:1131-1152). These CANNOT be reached via overrides.
TRIP_MODEL_BY_CORE: dict[str, str] = {
    "1r": "SegmentedMSR.Reactors.R1MSRRuhxTripThermalSS",
    "9r": "SegmentedMSR.Reactors.R9MSRRuhxTripThermalSS",
}


def normalize_core_key(core_model: str) -> str:
    """Normalize/validate a ``core_model`` CLI value ('1R' -> '1r')."""
    key = core_model.strip().lower()
    if key not in MODEL_BY_CORE:
        raise KeyError(
            f"Unknown segmented core_model {core_model!r}; "
            f"expected one of {list(MODEL_BY_CORE)}"
        )
    return key


def model_for(core_model: str) -> str:
    """Full-loop trim-rig vehicle name for ``core_model``."""
    return MODEL_BY_CORE[normalize_core_key(core_model)]


def trip_model_for(core_model: str) -> str:
    """UHX-trip scenario vehicle name for ``core_model``."""
    return TRIP_MODEL_BY_CORE[normalize_core_key(core_model)]


# ---------------------------------------------------------------------------
# mos-text builders (omc 1.27 environment: simulate() scripting is broken
# system-wide; use buildModel(..., tolerance=...) then run the generated
# executable directly. Tolerance is baked at BUILD time; -rtol/-atol are NOT
# runtime flags. The built executable's file name equals the dotted class
# name, e.g. 'SegmentedMSR.Reactors.R1MSRRuhxTripThermalSS'.)
# ---------------------------------------------------------------------------

def library_load_lines(indent: str = "") -> list[str]:
    """Exactly ONE ``loadFile`` line per library file (anti double-load)."""
    return [f'{indent}loadFile("{name}");' for name in SEGMENTED_LIBRARY_FILES]


def build_check_mos(
    class_names: "list[str] | tuple[str, ...]",
    *,
    library_files: "tuple[str, ...] | list[str]" = SEGMENTED_LIBRARY_FILES,
) -> str:
    """Build a check-sweep .mos text.

    Loads ``library_files`` (pass ``(SMD_MSR_Modelica.mo,
    SegmentedMSR.mo)`` style tuples at the call site for side-by-side clash
    probes), then ``checkModel`` + ``getErrorString()`` for every class.
    MARK lines bracket each command pair so buffered errors cannot leak
    between sections when parsing omc output.
    """
    if not library_files:
        raise ValueError("library_files must name at least one .mo file")
    lines: list[str] = []
    for name in library_files:
        lines.append(f'print("MARK load_{name}\\n");')
        lines.append(f'loadFile("{name}");')
        lines.append("getErrorString();")
    for qual in class_names:
        lines.append(f'print("MARK chk {qual}\\n");')
        lines.append(f"checkModel({qual});")
        lines.append("getErrorString();")
    return "\n".join(lines) + "\n"


def build_smoke_mos(
    model_name: str,
    *,
    tolerance: float = 1e-10,
) -> str:
    """buildModel-only .mos text for one model (smoke-session builder).

    Run the produced executable afterwards, e.g.::

        ./<model_name> -outputFormat=csv -r=out.csv -stopTime=<N>

    (exit code 0 + result file present + no hard assertion == green smoke).
    """
    return "\n".join(
        [
            'print("MARK load_seg\\n");',
            *library_load_lines(),
            "getErrorString();",
            f'print("MARK build {model_name}\\n");',
            f"buildModel({model_name}, tolerance = {tolerance!r});",
            "getErrorString();",
        ]
    ) + "\n"


# ---------------------------------------------------------------------------
# Segmented CSV column candidates (plot-side integration, S3+).
#
# The rigs expose FIXED overlay result columns plus named component signals;
# these tuples are RESOLUTION CANDIDATES, not guarantees -- CSV headers may be
# quoted/decorated depending on the collector, so downstream matchers try them
# in order against cleaned headers.
# ---------------------------------------------------------------------------

#: Fixed overlay columns written by the rigs themselves (plan §9.4 naming).
CSV_OVERLAY_COLUMNS_BY_CORE: dict[str, tuple[str, ...]] = {
    "1r": (
        "nOut",
        "rhoCircPcm",
        "TF1",
        "TF2",
        "TG",
        "TinCore",
        "ToutCore",
        "sumInW",
        "sumOutW",
        "eResid",
    ),
    # NOTE: no TF1/TF2/TG on the 9R rig -- zone outlets/plenum instead.
    "9r": (
        "nOut",
        "rhoCircPcm",
        "TZout[1]",
        "TZout[2]",
        "TZout[3]",
        "TZout[4]",
        "TPot",
        "ToutPlenum",
        "TinCore",
        "sumInW",
        "sumOutW",
        "eResid",
    ),
}

#: Reactor-power column candidates (legacy plots used fuelNode/grapNode paths;
#: segmented power lives on the PowerBlock).
CSV_POWER_COLUMN_CANDIDATES: tuple[str, ...] = (
    "pb.reactorPower",
    "pb.fissionPower.P",
)

#: Total temperature-feedback column candidates per core (1R single feedback;
#: 9R aggregated as RF1..RF9 -- summing is the consumer's job).
CSV_FEEDBACK_COLUMNS_BY_CORE: dict[str, tuple[str, ...]] = {
    "1r": ("fb.TotalTempFeedback",),
    "9r": tuple(f"rf{i}.TotalTempFeedback" for i in range(1, 10)),
}


def overlay_columns_for(core_model: str) -> tuple[str, ...]:
    """Fixed overlay column names for ``core_model``."""
    return CSV_OVERLAY_COLUMNS_BY_CORE[normalize_core_key(core_model)]


def feedback_columns_for(core_model: str) -> tuple[str, ...]:
    """Temperature-feedback column candidates for ``core_model``."""
    return CSV_FEEDBACK_COLUMNS_BY_CORE[normalize_core_key(core_model)]
