#!/usr/bin/env python3
"""Fast non-omc unit tests for the transients ``--package`` switch (phase R2).

Covers TASK-20260823-04 AC-4 fast surface against commit 2fdd01d plus the
REV-2fdd01d-01/-02 test-sync reconciliation of fix commit 93980a3:

- segmented case/config selection: SEGMENTED_CORE_CONFIG drives
  helpers.segmented_runs.MODEL_BY_CORE rigs for steps/flows and
  TRIP_MODEL_BY_CORE for uhx_trip; short-horizon floors apply ONLY to the
  SEGMENTED uhx_trip (>=4500 s demand drop) and SEGMENTED flow cases
  (>=4400 s reactivity insertion, REV-2fdd01d-02) -- legacy keeps
  max(stop_time, 4000 + 4*3600 s) trips and byte-unchanged flow timing;
- generated segmented scripts contain EXACTLY ONE
  ``loadFile("SegmentedMSR.mo");``, a ``buildModel(..., tolerance = ...)``
  call with the CLI tolerance baked, and NEVER ``simulate(``;
- output-dir convention (Ambiguity J): default run roots nest
  ``segmented/<core>/``; an explicit ``--out_dir`` gains the SAME insertion;
  legacy layouts stay shaped like pre-R2 (parent of 2fdd01d);
- segmented hard-errors (setpoints CSV inputs, loop-setpoint sync flag,
  non-dassl method, non-positive tolerance/stop_time/number_of_intervals)
  raise BEFORE any simulation starts;
- flow-horizon floor sync (REV-2fdd01d-02, fix 93980a3):
  ``SEGMENTED_FLOW_MIN_STOP_TIME_S == SEGMENTED_FLOW_INSERT_TIME_S + 400 ==
  4400``; the plan builder keeps its ``flow_stop_time=None`` back-compat
  default (falls back to ``stop_time``); the segmented CLI argv carries
  ``-stopTime=4400`` for every flow case while steps keep the requested
  stop_time;
- reader numeric tolerance (REV-2fdd01d-01, fix 93980a3): the segmented
  readers coerce numerics tolerantly -- one stray omc token (observed
  ``983013.774829801=``) becomes a NaN gap instead of aborting load/plot;
  well-formed cells parse identically;
- plotter column resolution rides the DECLARED helpers.segmented_runs
  candidates: power via pb.reactorPower, 9R feedback =
  sum(RF1..RF9.TotalTempFeedback)*1e5, 1R single fb.TotalTempFeedback,
  9R temperature panel = mean(TZout[1..4])/TPot, UHX outlet chain
  ToutCore/ToutPlenum; missing columns raise a clear KeyError;
- fake-run helper published-tree guard (REV-a98c520-01, centralized by
  TASK-20260830-01 P5 / review M4): both ``_make_segmented_fake_run`` and
  ``_make_legacy_fake_run`` refuse (raise) any runner-supplied cwd
  that resolves under ``00runs/transients-*``, ``00runs/startup-*`` or
  ``00runs/freq/`` -- the published record trees the pre-redirect incident
  contaminated -- while ``00runs/tmp/`` scratch and pytest ``tmp_path``
  stay allowed; the guard is the shared ``helpers.published_tree_guard``
  utility, not a local copy;
- legacy preservation: case tables, argparse defaults, build_mos template,
  plot matchers, and output-dir resolution stay identical to the parent
  commit (expectations pinned from ``git show 2fdd01d^:transients/*.py``).

Monkeypatch mechanics follow tests/test_freq_segmented_mode.py: modules load
through importlib and tests patch names imported INTO the runner module
namespaces (``mod.subprocess.run``, ``mod.copyfile``,
``mod.default_transients_run_dir``); nothing is renamed. Synthetic fixtures
only -- zero omc, zero real simulations.
"""

from __future__ import annotations

import os
import re
import sys
import importlib
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")  # headless plotter imports below

import numpy as np
import pandas as pd
import pytest

from helpers import segmented_runs as seg_runs
from helpers.published_tree_guard import (
    refuse_published_tree_cwd as _refuse_published_tree_cwd,
)


def _load_module(_module_name: str, relative_path: str):
    qualified_name = relative_path.removesuffix(".py").replace("/", ".")
    return importlib.reload(importlib.import_module(qualified_name))


def _load_runner(tag: str):
    return _load_module(
        f"run_nonlinear_steps_{tag}", "transients/run_nonlinear_steps.py"
    )


def _load_plotter(tag: str):
    return _load_module(
        f"plot_nonlinear_steps_{tag}", "transients/plot_nonlinear_steps.py"
    )


def _load_paths(tag: str):
    return _load_module(f"transients_paths_{tag}", "transients/paths.py")


def _use_argv(monkeypatch, *flags: str) -> None:
    """Both transients entrypoints read sys.argv (no argv parameter)."""
    monkeypatch.setattr(sys, "argv", ["run_nonlinear_steps.py", *flags])


class _DummyCompleted:
    returncode = 0


_BUILD_MODEL_RE = re.compile(r"buildModel\(([^,]+?),")


def _expected_case_order(runner) -> list[str]:
    return [
        *runner.STEP_PCM.keys(),
        *runner.FLOW_CASES.keys(),
        runner.UHX_TRIP_CASE,
    ]


def _result_name_to_case(result_name: str) -> str:
    # TASK-20260827-01 P5: provenanced runs direct the executable's -r= flag
    # at the {case}_res.csv.tmp temporary companion; strip the documented
    # suffix before the case prefix.  Direct programmatic calls (no
    # provenance context) keep the historical final-name value.
    if result_name.endswith(".tmp"):
        result_name = result_name[: -len(".tmp")]
    return result_name[: -len("_res.csv")]


# ---------------------------------------------------------------------------
# Package constants (runner + plotter re-exports).
# ---------------------------------------------------------------------------


def test_package_constants_default_to_legacy() -> None:
    runner = _load_runner("pkg_const")
    assert runner.LEGACY_PACKAGE == "legacy"
    assert runner.SEGMENTED_PACKAGE == "segmented"
    assert runner.PACKAGE_CHOICES == ("legacy", "segmented")
    assert runner.DEFAULT_PACKAGE == runner.LEGACY_PACKAGE

    plotter = _load_plotter("pkg_const")
    assert plotter.LEGACY_PACKAGE == "legacy"
    assert plotter.SEGMENTED_PACKAGE == "segmented"
    assert plotter.DEFAULT_PACKAGE == "legacy"


def test_segmented_constants_pin_card_decisions() -> None:
    runner = _load_runner("seg_const")
    assert runner.SEGMENTED_CORE_CONFIG == {
        "1r": {"label": "1R"},
        "9r": {"label": "9R"},
    }
    assert set(runner.SEGMENTED_CORE_CONFIG) == set(seg_runs.CORE_KEYS)
    # Short-horizon trip floor (segmented only) and mirrored insertion times.
    assert runner.SEGMENTED_TRIP_MIN_STOP_TIME_S == pytest.approx(4500.0)
    assert runner.SEGMENTED_STEP_INSERT_TIME_S == pytest.approx(2000.0)
    assert runner.SEGMENTED_FLOW_INSERT_TIME_S == pytest.approx(4000.0)

    plotter = _load_plotter("seg_const")
    assert plotter.SEGMENTED_UHX_DECAY_COLUMN_CANDIDATES == ("pb.decayPower",)


def test_segmented_flow_min_stop_time_floor_pinned() -> None:
    """REV-2fdd01d-02: flow floor = insertion time + 400 s plot window.

    The flow overrides insert the 1-$ reactivity step at t=4000 s
    (legacy-mirrored) and ``plot_flow`` draws (t - 4000) in [-50, 400] s,
    so any segmented flow run shorter than 4400 s yields an empty panel.
    """
    runner = _load_runner("seg_flow_floor_const")
    assert runner.SEGMENTED_FLOW_MIN_STOP_TIME_S == pytest.approx(4400.0)
    assert runner.SEGMENTED_FLOW_MIN_STOP_TIME_S == (
        runner.SEGMENTED_FLOW_INSERT_TIME_S + 400.0
    )


# ---------------------------------------------------------------------------
# 1) Segmented case/config selection (plan-level gate).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("core_key", ["1r", "9r"])
def test_build_segmented_run_plan_maps_declared_rigs(core_key: str) -> None:
    runner = _load_runner(f"plan_select_{core_key}")
    trim_rig = seg_runs.MODEL_BY_CORE[core_key]
    trip_rig = seg_runs.TRIP_MODEL_BY_CORE[core_key]

    plan = runner.build_segmented_run_plan(
        seg_runs, core_key, stop_time=2500.0, trip_stop_time=4500.0
    )

    assert [item["case"] for item in plan] == _expected_case_order(runner)
    assert [item["family"] for item in plan] == (
        ["step"] * len(runner.STEP_PCM)
        + ["flow"] * len(runner.FLOW_CASES)
        + ["uhx_trip"]
    )
    for item in plan:
        if item["family"] == "uhx_trip":
            assert item["model"] == trip_rig
            assert item["stop_time"] == pytest.approx(4500.0)
            assert item["overrides"] == runner.build_segmented_uhx_trip_overrides()
        else:
            assert item["model"] == trim_rig
            assert item["stop_time"] == pytest.approx(2500.0)
            if item["family"] == "step":
                assert item["overrides"] == runner.build_segmented_step_overrides(
                    runner.STEP_PCM[item["case"]]
                )
            else:
                assert item["overrides"] == runner.build_segmented_flow_overrides(
                    runner.FLOW_CASES[item["case"]]
                )


def test_build_segmented_run_plan_rejects_unknown_core() -> None:
    runner = _load_runner("plan_bad_core")
    with pytest.raises(KeyError, match="Unknown segmented core_model"):
        runner.build_segmented_run_plan(
            seg_runs, "3r", stop_time=1.0, trip_stop_time=1.0
        )


@pytest.mark.parametrize("core_key", ["1r", "9r"])
def test_build_segmented_run_plan_flow_stop_time_none_backcompat(
    core_key: str,
) -> None:
    """Back-compat default: ``flow_stop_time=None`` falls back to stop_time.

    Callers predating fix 93980a3 pass no flow horizon; the plan must keep
    giving flow items the plain ``stop_time`` in that case.
    """
    runner = _load_runner(f"plan_flow_default_{core_key}")
    plan = runner.build_segmented_run_plan(
        seg_runs, core_key, stop_time=2500.0, trip_stop_time=4500.0
    )
    stops = {item["case"]: item["stop_time"] for item in plan}
    for case in runner.FLOW_CASES:
        assert stops[case] == pytest.approx(2500.0)
    for case in runner.STEP_PCM:
        assert stops[case] == pytest.approx(2500.0)
    assert stops[runner.UHX_TRIP_CASE] == pytest.approx(4500.0)


@pytest.mark.parametrize("core_key", ["1r", "9r"])
def test_build_segmented_run_plan_explicit_flow_stop_time_floors_flows_only(
    core_key: str,
) -> None:
    """CLI route (REV-2fdd01d-02): an explicit flow_stop_time raises ONLY
    the flow items; step horizons, trip horizon, and every override payload
    stay untouched by the floor."""
    runner = _load_runner(f"plan_flow_explicit_{core_key}")
    plan = runner.build_segmented_run_plan(
        seg_runs,
        core_key,
        stop_time=2500.0,
        trip_stop_time=4500.0,
        flow_stop_time=runner.SEGMENTED_FLOW_MIN_STOP_TIME_S,
    )
    stops = {item["case"]: item["stop_time"] for item in plan}
    overrides = {item["case"]: item["overrides"] for item in plan}
    for case, flow_frac in runner.FLOW_CASES.items():
        assert stops[case] == pytest.approx(4400.0)
        # The floor reschedules NOTHING inside the override payload.
        assert overrides[case] == runner.build_segmented_flow_overrides(flow_frac)
    for case in runner.STEP_PCM:
        assert stops[case] == pytest.approx(2500.0)
    assert stops[runner.UHX_TRIP_CASE] == pytest.approx(4500.0)


# ---------------------------------------------------------------------------
# Segmented override routes (mirrored from the legacy routes by design).
# ---------------------------------------------------------------------------


def test_segmented_step_overrides_match_legacy_route() -> None:
    runner = _load_runner("ovr_step")
    assert runner.build_segmented_step_overrides(589.069) == (
        "primaryPump.freeConvFF=1,"
        "secondaryPump.freeConvFF=1,"
        "powerLevel=1,"
        "perturbationAmplitudePcm=0,"
        "externalReactivityAmplitude[2]=589.069,"
        "externalReactivityStepTime[2]=2000"
    )


def test_segmented_flow_overrides_match_legacy_pump_route() -> None:
    runner = _load_runner("ovr_flow")
    assert runner.build_segmented_flow_overrides(2.0 / 3.0) == (
        # freeConvFF rides the SAME unformatted float text as the legacy route;
        # rampUpTo[1] keeps its .10g rounding.
        "primaryPump.freeConvFF=0.6666666666666666,"
        "secondaryPump.freeConvFF=1,"
        "primaryPump.rampUpTo[1]=0.3333333333,"
        "primaryPump.rampUpTime[1]=0,"
        "primaryPump.tripTime=2000,"
        "powerLevel=1,"
        "perturbationAmplitudePcm=0,"
        "externalReactivityAmplitude[2]=589.069,"
        "externalReactivityStepTime[2]=4000"
    )


def test_segmented_uhx_trip_overrides_carry_only_runtime_pins() -> None:
    runner = _load_runner("ovr_trip")
    # Demand-drop schedule/DHRS are structural on the wrapper; no reactivity
    # or setpoint machinery may leak into the override payload.
    assert runner.build_segmented_uhx_trip_overrides() == (
        "primaryPump.freeConvFF=1,"
        "secondaryPump.freeConvFF=1,"
        "primaryPump.rampUpTo[1]=1.0,"
        "primaryPump.rampUpTime[1]=0,"
        "powerLevel=1,"
        "perturbationAmplitudePcm=0"
    )


# ---------------------------------------------------------------------------
# Stubbed subprocess plumbing shared by the driver tests.
# ---------------------------------------------------------------------------


def _make_segmented_fake_run(calls: list):
    """Two-stage fake: omc builds the dotted-name executable; the executable
    writes a result CSV whose final row reaches the requested -stopTime.
    REFUSES published record-tree cwds (see _refuse_published_tree_cwd)."""

    def fake_run(cmd, stdout=None, stderr=None, cwd=None, timeout=None):
        _refuse_published_tree_cwd(cwd)
        calls.append({"cmd": list(cmd), "cwd": Path(cwd)})
        if cmd[0] == "omc":
            mos_text = (Path(cwd) / cmd[1]).read_text(encoding="utf-8")
            model_name = _BUILD_MODEL_RE.search(mos_text).group(1)
            (Path(cwd) / model_name).write_bytes(b"\x7fELF fake segmented exe")
            return _DummyCompleted()
        stop_text = [p for p in cmd if p.startswith("-stopTime=")][0].split("=", 1)[1]
        result_name = [p for p in cmd if p.startswith("-r=")][0][len("-r="):]
        (Path(cwd) / result_name).write_text(
            f"time,value\n0.0,1\n{stop_text},1\n", encoding="utf-8"
        )
        return _DummyCompleted()

    return fake_run


def _make_legacy_fake_run(calls: list):
    """Single-stage fake: each omc invocation yields <case>_res.csv.
    REFUSES published record-tree cwds (see _refuse_published_tree_cwd)."""

    def fake_run(cmd, stdout=None, stderr=None, cwd=None, timeout=None):
        _refuse_published_tree_cwd(cwd)
        calls.append({"cmd": list(cmd), "cwd": Path(cwd)})
        case_stem = Path(cmd[1]).stem
        (Path(cwd) / f"{case_stem}_res.csv").write_text(
            "time,value\n0.0,1\n", encoding="utf-8"
        )
        return _DummyCompleted()

    return fake_run


_SETPOINT_COLUMNS = (
    "power",
    "fuelTempSetPointNode1",
    "fuelTempSetPointNode2",
    "graphiteTempSetPoint",
    "heatExchanger.TpIn_0",
    "heatExchanger.TpOut_0",
    "heatExchanger.TsIn_0",
    "heatExchanger.TsOut_0",
    "heatExchanger.T_PN1_0",
    "heatExchanger.T_PN2_0",
    "heatExchanger.T_PN3_0",
    "heatExchanger.T_PN4_0",
    "heatExchanger.T_TN1_0",
    "heatExchanger.T_TN2_0",
    "heatExchanger.T_SN1_0",
    "heatExchanger.T_SN2_0",
    "heatExchanger.T_SN3_0",
    "heatExchanger.T_SN4_0",
    "pipeHXtoUHX.T_0",
    "pipeUHXtoHX.T_0",
    "dhrs.T_0",
    "pipeDHRStoHX.T_0",
    "pipeHXtoCore.T_0",
    "pipeCoreToDHRS.T_0",
    "uhx.Tp_0",
)


def _write_setpoints_csv(path: Path, power: float = 1.0) -> Path:
    frame = {
        col: [power if col == "power" else 900.0 + idx]
        for idx, col in enumerate(_SETPOINT_COLUMNS)
    }
    pd.DataFrame(frame).to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# 2)+3) Segmented driver e2e with stubbed processes: mos text, dirs, floors.
# ---------------------------------------------------------------------------


def test_segmented_main_explicit_out_dir_nests_and_floors_trip_and_flow(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_runner("seg_main_out_dir")
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "SegmentedMSR.mo").write_text("// dummy package\n", encoding="utf-8")
    out_dir = tmp_path / "results"

    calls: list[dict] = []
    copied: list[Path] = []
    monkeypatch.setattr(runner.subprocess, "run", _make_segmented_fake_run(calls))
    monkeypatch.setattr(runner, "copyfile", lambda src, dst: copied.append(Path(dst)))
    _use_argv(
        monkeypatch,
        "--package", "segmented",
        "--core_dir", str(core_dir),
        "--out_dir", str(out_dir),
        "--stop_time", "2500",
        "--number_of_intervals", "10000",
        "--tolerance", "2e-05",
    )

    assert runner.main() == 0

    # Ambiguity J: <out_dir>/segmented/<core>/ insertion; no legacy sibling.
    for core_key in ("1r", "9r"):
        core_out = out_dir / "segmented" / core_key
        assert core_out.is_dir()
        assert not (out_dir / core_key).exists()

        mos_names = sorted(p.name for p in core_out.glob("*.mos"))
        assert mos_names == sorted(f"{case}.mos" for case in _expected_case_order(runner))

        trim_rig = seg_runs.MODEL_BY_CORE[core_key]
        trip_rig = seg_runs.TRIP_MODEL_BY_CORE[core_key]
        for mos_path in core_out.glob("*.mos"):
            text = mos_path.read_text(encoding="utf-8")
            # Exactly ONE standalone-library load; never the legacy pair,
            # never the broken simulate() scripting route.
            assert text.count('loadFile("SegmentedMSR.mo");') == 1
            assert "SMD_MSR_Modelica" not in text
            assert "simulate(" not in text
            if mos_path.stem == "uhx_trip":
                expected_model = trip_rig
                expected_stop = "4500"
            elif mos_path.stem.startswith("flow"):
                expected_model = trim_rig
                expected_stop = "4400"  # REV-2fdd01d-02 floor rides argv
            else:
                expected_model = trim_rig
                expected_stop = "2500"
            assert f"buildModel({expected_model}, tolerance = 2e-05);" in text
            assert f"stopTime={expected_stop}" not in text  # stops ride argv

        # No legacy multi-hour floor may leak into any segmented TEXT artifact.
        joined_mos = "".join(
            p.read_text(encoding="utf-8") for p in core_out.glob("*.mos")
        )
        assert "18400" not in joined_mos

    # Copy set per core: ONLY the standalone library.
    assert [p.name for p in copied] == ["SegmentedMSR.mo", "SegmentedMSR.mo"]
    assert sorted(p.parent.name for p in copied) == ["1r", "9r"]

    # Two-stage execution: every case writes its .mos and runs its executable,
    # but REV-2fdd01d-03 build-once reuse means only the FIRST plan item per
    # DISTINCT model pays the omc build stage; later items write their .mos
    # and skip omc, reusing the executable built earlier in the same workdir.
    assert len(calls) == 24
    omc_calls = [c for c in calls if c["cmd"][0] == "omc"]
    exe_calls = [c for c in calls if c["cmd"][0] != "omc"]
    # omc builds: 2 distinct models (trim rig + trip wrapper) x 2 cores;
    # executables still run once per case.
    assert len(omc_calls) == 4 and len(exe_calls) == 20
    assert all(call["cmd"][1].endswith(".mos") for call in omc_calls)

    exe_by_core_case: dict[tuple[str, str], list[str]] = {}
    for call in exe_calls:
        result_flag = next(p for p in call["cmd"] if p.startswith("-r="))
        case = _result_name_to_case(result_flag[len("-r="):])
        exe_by_core_case[(call["cwd"].name, case)] = call["cmd"]

    for core_key in ("1r", "9r"):
        trim_exe = f"./{seg_runs.MODEL_BY_CORE[core_key]}"
        trip_exe = f"./{seg_runs.TRIP_MODEL_BY_CORE[core_key]}"
        for case, pcm in runner.STEP_PCM.items():
            cmd = exe_by_core_case[(core_key, case)]
            assert cmd[0] == trim_exe
            # Steps keep the requested --stop_time (no floor).
            assert "-stopTime=2500" in cmd
            assert f"-stepSize={2500.0 / 10000:.17g}" in cmd
            assert "-outputFormat=csv" in cmd
            assert "-maxStepSize=0.02" in cmd
            assert f"-override={runner.build_segmented_step_overrides(pcm)}" in cmd
            assert not any(p.startswith("-numberOfIntervals=") for p in cmd)

        for case, flow_frac in runner.FLOW_CASES.items():
            cmd = exe_by_core_case[(core_key, case)]
            assert cmd[0] == trim_exe
            # REV-2fdd01d-02: flow horizons ride the 4400 s floor
            # (t=4000 s insertion + 400 s plot window), NOT the
            # requested --stop_time; overrides are NOT rescheduled.
            assert "-stopTime=4400" in cmd
            assert f"-stepSize={4400.0 / 10000:.17g}" in cmd
            assert f"-override={runner.build_segmented_flow_overrides(flow_frac)}" in cmd

        trip_cmd = exe_by_core_case[(core_key, runner.UHX_TRIP_CASE)]
        # Trip floor raises ONLY uhx_trip past the t=4000 s demand drop;
        # flow cases ride the separate REV-2fdd01d-02 floor pinned above.
        assert trip_cmd[0] == trip_exe
        assert "-stopTime=4500" in trip_cmd
        assert f"-stepSize={4500.0 / 10000:.17g}" in trip_cmd
        assert f"-override={runner.build_segmented_uhx_trip_overrides()}" in trip_cmd


def test_segmented_main_default_run_root_nests_segmented(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_runner("seg_main_default_root")
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "SegmentedMSR.mo").write_text("// dummy package\n", encoding="utf-8")

    # default_transients_run_dir is imported INTO the runner namespace; point
    # it at scratch so the default-root nesting is provable without touching
    # the repository's own 00runs tree.
    default_root = tmp_path / "default_root"
    monkeypatch.setattr(
        runner,
        "default_transients_run_dir",
        lambda repo_root, *, core_models: default_root,
    )
    calls: list[dict] = []
    monkeypatch.setattr(runner.subprocess, "run", _make_segmented_fake_run(calls))
    _use_argv(
        monkeypatch,
        "--package", "segmented",
        "--core_dir", str(core_dir),
        "--core_models", "1r",
        "--stop_time", "2500",
    )

    assert runner.main() == 0
    assert (default_root / "segmented" / "1r").is_dir()
    assert not (default_root / "1r").exists()
    # 10 cases x direct executable run for the single core; REV-2fdd01d-03
    # build-once reuse: the first plan item per DISTINCT model (trim rig,
    # trip wrapper) builds via omc, later items write their .mos but skip
    # the omc stage.
    assert len(calls) == 12
    assert all(call["cwd"] == default_root / "segmented" / "1r" for call in calls)


def test_segmented_main_floors_only_flow_horizon_in_argv(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """REV-2fdd01d-02 driver pin: segmented argv floors EVERY flow case to
    ``-stopTime=4400`` while steps keep the requested --stop_time and
    uhx_trip keeps its own 4500 s floor; both horizon notices print."""
    runner = _load_runner("seg_flow_floor_argv")
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "SegmentedMSR.mo").write_text("// dummy package\n", encoding="utf-8")

    # Point the default run root at scratch (same convention as the
    # default-root nesting test above): the production default is
    # 00runs/transients-1r, the PUBLISHED record this repo never lets tests
    # write into.
    default_root = tmp_path / "default_root"
    monkeypatch.setattr(
        runner,
        "default_transients_run_dir",
        lambda repo_root, *, core_models: default_root,
    )

    calls: list[dict] = []
    monkeypatch.setattr(runner.subprocess, "run", _make_segmented_fake_run(calls))
    monkeypatch.setattr(runner, "copyfile", lambda src, dst: None)
    _use_argv(
        monkeypatch,
        "--package", "segmented",
        "--core_dir", str(core_dir),
        "--core_models", "1r",
        "--stop_time", "2500",
        "--number_of_intervals", "10000",
    )

    assert runner.main() == 0

    def _family(case: str) -> str:
        if case == runner.UHX_TRIP_CASE:
            return "uhx_trip"
        return "flow" if case in runner.FLOW_CASES else "step"

    stops_by_family: dict[str, set[str]] = {}
    for call in calls:
        if call["cmd"][0] == "omc":
            continue
        result_flag = next(p for p in call["cmd"] if p.startswith("-r="))
        case = _result_name_to_case(result_flag[len("-r="):])
        stop_flag = next(p for p in call["cmd"] if p.startswith("-stopTime="))
        stops_by_family.setdefault(_family(case), set()).add(
            stop_flag.split("=", 1)[1]
        )

    assert stops_by_family == {
        "step": {"2500"},  # requested --stop_time kept verbatim
        "flow": {"4400"},  # REV-2fdd01d-02 floor
        "uhx_trip": {"4500"},
    }

    printed = capsys.readouterr().out
    assert "Segmented flow-case horizon raised to 4400 s" in printed
    assert "t=4000 s reactivity insertion" in printed
    assert "uhx_trip horizon raised to 4500 s" in printed


def test_run_case_segmented_fails_when_result_never_reaches_stop_time(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_runner("seg_short_gate")

    def fake_run(cmd, stdout=None, stderr=None, cwd=None, timeout=None):
        if cmd[0] == "omc":
            (Path(cwd) / "Some.Model").write_bytes(b"\x7fELF")
            return _DummyCompleted()
        (Path(cwd) / "case_res.csv").write_text(
            "time,value\n0.0,1\n100.0,1\n", encoding="utf-8"
        )
        return _DummyCompleted()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="did not reach requested stop_time"):
        runner.run_case_segmented(
            "omc",
            "Some.Model",
            "// mos",
            tmp_path,
            case_prefix="case",
            stop_time=2500.0,
            number_of_intervals=10,
            max_step_size=0.02,
            overrides="",
        )


def test_run_case_segmented_fails_when_build_produces_no_executable(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_runner("seg_no_exe")

    def fake_run(cmd, stdout=None, stderr=None, cwd=None, timeout=None):
        return _DummyCompleted()  # omc exits 0 but builds nothing

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="produced no executable"):
        runner.run_case_segmented(
            "omc",
            "Missing.Model",
            "// mos",
            tmp_path,
            case_prefix="case",
            stop_time=10.0,
            number_of_intervals=10,
            max_step_size=0.02,
            overrides="",
        )


def test_segmented_fake_run_refuses_published_record_tree_cwds(
    tmp_path: Path,
) -> None:
    """REV-a98c520-01 (centralized by TASK-20260830-01 P5 / M4): both
    fake-run helpers raise BEFORE any write when the runner hands them a cwd
    resolving under a published record tree, while scratch cwds keep
    working. The pytest tmp_path itself resolves under 00runs/tmp/, so the
    positive case below also proves the guard does not over-fire on
    scratch."""
    repo_root = Path(__file__).resolve().parents[1]
    for fake, argv in (
        (_make_segmented_fake_run(calls := []),
         ["./Some.Model", "-stopTime=100", "-r=case_res.csv"]),
        (_make_legacy_fake_run(legacy_calls := []),
         ["omc", "some_case.mos"]),
    ):
        for bad_cwd in (
            repo_root / "00runs" / "transients-1r" / "segmented" / "1r",
            repo_root / "00runs" / "transients-9r",
            repo_root / "00runs" / "startup-startup-1r",
            repo_root / "00runs" / "freq",
            repo_root / "00runs" / "freq" / "1r",
        ):
            with pytest.raises(RuntimeError, match="published record tree"):
                fake(argv, cwd=bad_cwd)
        assert not calls and not legacy_calls, (
            "the refusal must precede the call record (and any write)"
        )

    # Scratch cwds stay allowed: each fake writes its fake result CSV.
    fake = _make_segmented_fake_run(calls)
    fake(["./Some.Model", "-stopTime=100", "-r=case_res.csv"], cwd=tmp_path)
    assert (tmp_path / "case_res.csv").is_file()

    legacy = _make_legacy_fake_run(legacy_calls)
    legacy(["omc", "some_case.mos"], cwd=tmp_path)
    assert (tmp_path / "some_case_res.csv").is_file()


def test_refuse_guard_is_the_shared_published_tree_utility() -> None:
    """TASK-20260830-01 P5 / M4: the module no longer carries a local copy of
    the guard -- ``_refuse_published_tree_cwd`` IS the shared
    ``helpers.published_tree_guard`` implementation (drop-in import alias),
    so a regression back to a per-module duplicate is caught."""
    from helpers import published_tree_guard

    assert _refuse_published_tree_cwd is published_tree_guard.refuse_published_tree_cwd


# ---------------------------------------------------------------------------
# 4) Segmented hard errors: legacy-only knobs die BEFORE any simulation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra_argv, needle",
    [
        (["--setpoints_csv", "custom.csv"], "--setpoints_csv is unsupported"),
        (["--setpoints_csv_1r", "custom1.csv"], "--setpoints_csv_1r is unsupported"),
        (["--setpoints_csv_9r", "custom9.csv"], "--setpoints_csv_9r is unsupported"),
        (["--no_sync_loop_setpoints"], "--no_sync_loop_setpoints is unsupported"),
        (["--method", "rk4"], "--method is unsupported"),
        (["--stop_time", "0"], "--stop_time must be > 0"),
        (["--stop_time", "-5"], "--stop_time must be > 0"),
        (["--number_of_intervals", "0"], "--number_of_intervals must be >= 1"),
        (["--tolerance", "0"], "--tolerance must be > 0"),
    ],
)
def test_validate_args_segmented_hard_errors(
    monkeypatch, extra_argv: list, needle: str
) -> None:
    runner = _load_runner("seg_validate_bad")
    _use_argv(monkeypatch, "--package", "segmented", *extra_argv)
    args = runner.parse_args()
    with pytest.raises(ValueError, match=needle):
        runner.validate_args(args)


def test_validate_args_segmented_nominal_invocation_passes(monkeypatch) -> None:
    runner = _load_runner("seg_validate_ok")
    _use_argv(monkeypatch, "--package", "segmented")
    args = runner.parse_args()
    assert args.package == "segmented"
    runner.validate_args(args)


def test_validate_args_legacy_mode_passes_trivially(monkeypatch) -> None:
    runner = _load_runner("legacy_validate_ok")
    _use_argv(monkeypatch)
    runner.validate_args(runner.parse_args())
    # The same legacy-only knobs pass untouched under --package legacy.
    _use_argv(
        monkeypatch,
        "--setpoints_csv", "custom.csv",
        "--setpoints_csv_1r", "custom1.csv",
        "--setpoints_csv_9r", "custom9.csv",
        "--no_sync_loop_setpoints",
        "--method", "rk4",
        "--stop_time", "0",
        "--number_of_intervals", "0",
        "--tolerance", "0",
    )
    args = runner.parse_args()
    assert args.package == "legacy"
    runner.validate_args(args)  # early return: zero behavior change


def test_main_validates_before_any_simulation(monkeypatch, tmp_path: Path) -> None:
    runner = _load_runner("seg_guard_order")

    def _forbid(cmd, **kwargs):
        raise AssertionError(f"subprocess reached despite invalid args: {cmd}")

    monkeypatch.setattr(runner.subprocess, "run", _forbid)
    _use_argv(
        monkeypatch,
        "--package", "segmented",
        "--core_dir", str(tmp_path / "unused"),
        "--method", "rk4",
    )
    with pytest.raises(ValueError, match="--method is unsupported"):
        runner.main()


# ---------------------------------------------------------------------------
# 6) Legacy preservation vs parent commit (2fdd01d^).
# ---------------------------------------------------------------------------


def test_legacy_case_tables_unchanged_from_parent() -> None:
    runner = _load_runner("legacy_tables")
    assert runner.STEP_PCM == {
        "step_2dol": 1178.138,
        "step_1dol": 589.069,
        "step_0p5dol": 294.535,
        "step_0p1dol": 58.907,
        "step_100pcm": 100.0,
        "step_10pcm": 10.0,
    }
    assert runner.FLOW_CASES == {
        "flow_100pct": 1.0,
        "flow_66pct": 2.0 / 3.0,
        "flow_33pct": 1.0 / 3.0,
    }
    assert runner.UHX_TRIP_CASE == "uhx_trip"

    config_1r = runner.CORE_CONFIG["1r"]
    assert config_1r["label"] == "1R"
    assert config_1r["setpoints_file"] == "setpoints_1r.csv"
    assert (
        config_1r["init_mode_override"]
        == "core1R.mpke.initMode=SMD_MSR_Modelica.Units.InitMode.SteadyState"
    )
    assert config_1r["step_models"] == {
        "step_2dol": "MSRR.Transients.R1fullSteps.R1MSRR2dol",
        "step_1dol": "MSRR.Transients.R1fullSteps.R1MSRR1dol",
        "step_0p5dol": "MSRR.Transients.R1fullSteps.R1MSRRhalfDol",
        "step_0p1dol": "MSRR.Transients.R1fullSteps.R1MSRRpOneDol",
        "step_100pcm": "MSRR.Transients.R1fullSteps.R1MSRR100pcm",
        "step_10pcm": "MSRR.Transients.R1fullSteps.R1MSRR10pcm",
    }
    assert config_1r["nominal_thermal_model"] == "MSRR.MSRRuhxNominalTrimThermalSS"
    assert config_1r["uhx_model"] == "MSRR.MSRRuhxTripThermalSS"
    assert config_1r["uhx_extra_overrides"] == ""

    config_9r = runner.CORE_CONFIG["9r"]
    assert config_9r["label"] == "9R"
    assert config_9r["setpoints_file"] == "setpoints_9r.csv"
    assert (
        config_9r["init_mode_override"]
        == "msre9r.mpke.initMode=SMD_MSR_Modelica.Units.InitMode.SteadyState"
    )
    assert config_9r["step_models"] == {}
    assert config_9r["step_via_overrides"] is True
    assert config_9r["nominal_thermal_model"] == "MSRR.MSRRuhxNominalTrim9RThermalSS"
    assert config_9r["uhx_model"] == "MSRR.MSRRuhxTrip9RThermalSS"
    assert config_9r["uhx_extra_overrides"] == ""


def test_parse_args_defaults_unchanged_and_default_dir_formula_intact(
    monkeypatch,
) -> None:
    runner = _load_runner("legacy_defaults")
    paths_mod = _load_paths("legacy_defaults")

    _use_argv(monkeypatch)
    args = runner.parse_args()
    assert args.package == "legacy"
    assert args.omc == "omc"
    assert args.stop_time == pytest.approx(10000.0)
    assert args.number_of_intervals == 10000
    assert args.tolerance == pytest.approx(1e-6)
    assert args.method == "dassl"
    assert args.max_step_size == pytest.approx(0.02)
    assert args.omc_timeout_seconds == pytest.approx(0.0)
    assert args.core_models == ["1r", "9r"]

    repo_root = Path(runner.__file__).resolve().parents[1]
    assert args.out_dir == paths_mod.default_transients_run_dir(
        repo_root, core_models=["1r", "9r"]
    )
    assert paths_mod.default_transients_run_dir(
        Path("/repo"), core_models=["1r"]
    ) == Path("/repo/00runs/transients-1r")
    assert paths_mod.default_transients_run_dir(
        Path("/repo"), core_models=["9r", "1r", "9r"]
    ) == Path("/repo/00runs/transients-9r-1r")


def test_legacy_build_mos_template_byte_identical_to_parent() -> None:
    runner = _load_runner("legacy_mos_template")
    mos = runner.build_mos(
        Path("libs/SMD_MSR_Modelica.mo"),
        Path("libs/MSRR.mo"),
        "MSRR.Transients.R1fullSteps.R1MSRR100pcm",
        start_time=0.0,
        stop_time=15000.0,
        number_of_intervals=10000,
        tolerance=1e-6,
        method="dassl",
        file_prefix="step_100pcm",
        simflags="-maxStepSize=0.02 -override=externalReact.stepTime[2]=2000",
    )
    assert mos == (
        "// Generated by transients/run_nonlinear_steps.py\n"
        'loadFile("libs/SMD_MSR_Modelica.mo");\n'
        'loadFile("libs/MSRR.mo");\n'
        "simulate(MSRR.Transients.R1fullSteps.R1MSRR100pcm,"
        "startTime=0,"
        "stopTime=15000,"
        "numberOfIntervals=10000,"
        "tolerance=1e-06,"
        "method=dassl,"
        'outputFormat="csv",'
        'fileNamePrefix="step_100pcm",'
        'simflags="-maxStepSize=0.02 -override=externalReact.stepTime[2]=2000");\n'
    )


_LEGACY_LIB_TEXT = "// dummy legacy library\n"


def test_legacy_main_layout_and_multi_hour_trip_floor_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_runner("legacy_main_e2e")
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    library_path = core_dir / "SMD_MSR_Modelica.mo"
    model_lib_path = core_dir / "MSRR.mo"
    library_path.write_text(_LEGACY_LIB_TEXT, encoding="utf-8")
    model_lib_path.write_text(_LEGACY_LIB_TEXT, encoding="utf-8")
    sp_1r = _write_setpoints_csv(tmp_path / "sp_1r.csv")
    sp_9r = _write_setpoints_csv(tmp_path / "sp_9r.csv")
    out_dir = tmp_path / "results"

    calls: list[dict] = []
    monkeypatch.setattr(runner.subprocess, "run", _make_legacy_fake_run(calls))
    _use_argv(
        monkeypatch,
        "--core_dir", str(core_dir),
        "--out_dir", str(out_dir),
        "--stop_time", "15000",
        "--setpoints_csv_1r", str(sp_1r),
        "--setpoints_csv_9r", str(sp_9r),
    )

    assert runner.main() == 0

    # Legacy shape: <out>/<core>/ with NO segmented component anywhere.
    assert (out_dir / "1r").is_dir()
    assert (out_dir / "9r").is_dir()
    assert not (out_dir / "segmented").exists()

    # Single-stage simulate() route: 10 omc invocations per core, nothing else.
    assert len(calls) == 20
    assert all(call["cmd"][0] == "omc" for call in calls)

    # Byte-pin the dedicated-model 1R step route exactly as the parent wrote it.
    one_r = out_dir / "1r"
    step_mos = (one_r / "step_100pcm.mos").read_text(encoding="utf-8")
    assert step_mos == (
        "// Generated by transients/run_nonlinear_steps.py\n"
        f'loadFile("{library_path}");\n'
        f'loadFile("{model_lib_path}");\n'
        "simulate(MSRR.Transients.R1fullSteps.R1MSRR100pcm,"
        "startTime=0,"
        "stopTime=15000,"
        "numberOfIntervals=10000,"
        "tolerance=1e-06,"
        "method=dassl,"
        'outputFormat="csv",'
        'fileNamePrefix="step_100pcm",'
        # P5 appends the result redirect to the temporary companion
        # ({case}_res.csv.tmp) after the override payload; the build_mos
        # template itself is untouched (pinned above without the -r= part).
        'simflags="-maxStepSize=0.02 -override=externalReact.stepTime[2]=2000 '
        '-r=step_100pcm_res.csv.tmp");\n'
    )

    # Legacy trip floor stays max(stop, 4000 + 4*3600): 18400 with stop 15000;
    # the SEGMENTED-only 4500 s floor must NOT appear here.
    for core_key in ("1r", "9r"):
        uhx_mos = (out_dir / core_key / "uhx_trip.mos").read_text(encoding="utf-8")
        assert "stopTime=18400," in uhx_mos
        assert "stopTime=4500" not in uhx_mos
    for mos_path in sorted(one_r.glob("step_*.mos")):
        if mos_path.name != "step_100pcm.mos":
            assert "stopTime=15000," in mos_path.read_text(encoding="utf-8")

    # 9R override route (parent behavior): setpoint prefix + pump pins +
    # externalReactivityAmplitude[2] at t=2000 s on the nominal thermal model.
    nine_r_step = (out_dir / "9r" / "step_2dol.mos").read_text(encoding="utf-8")
    assert "simulate(MSRR.MSRRuhxNominalTrim9RThermalSS," in nine_r_step
    assert "externalReactivityAmplitude[2]=1178.138" in nine_r_step
    assert "externalReactivityStepTime[2]=2000" in nine_r_step
    assert "primaryPump.freeConvFF=1" in nine_r_step
    assert "perturbationAmplitudePcm=0" in nine_r_step
    assert "heatExchanger.detailedStateInitWeight=1" in nine_r_step
    assert (
        "msre9r.mpke.initMode=SMD_MSR_Modelica.Units.InitMode.SteadyState"
        in nine_r_step
    )

    # Flow route (parent behavior): pump ramp + 1-$ step at t=4000 s. The
    # freeConvFF float text is the parent's unformatted repr.
    flow_mos = (one_r / "flow_66pct.mos").read_text(encoding="utf-8")
    assert "simulate(MSRR.MSRRuhxNominalTrimThermalSS," in flow_mos
    assert "primaryPump.freeConvFF=0.6666666666666666" in flow_mos
    assert "primaryPump.rampUpTo[1]=0.3333333333" in flow_mos
    assert "primaryPump.tripTime=2000" in flow_mos
    assert "externalReactivityStepTime[2]=4000" in flow_mos
    assert 'fileNamePrefix="flow_66pct"' in flow_mos


# ---------------------------------------------------------------------------
# 6 cont.) Legacy plot matchers unchanged (behavioral pins on synthetic CSVs).
# ---------------------------------------------------------------------------


def test_legacy_load_power_time_candidates_unchanged(tmp_path: Path) -> None:
    plotter = _load_plotter("legacy_power_time")
    csv_path = tmp_path / "legacy_step.csv"
    pd.DataFrame(
        {
            "time": [0.0, 2100.0],
            "core1R.powerblock.fissionPower.P": [2.0e6, 1.9e6],
        }
    ).to_csv(csv_path, index=False)
    t_s, p_kw = plotter.load_power_time(csv_path)
    assert t_s.tolist() == [0.0, 2100.0]
    assert p_kw.tolist() == pytest.approx([2000.0, 1900.0])


def test_legacy_load_fuel_graphite_feedback_exact_and_sum_fallback(
    tmp_path: Path,
) -> None:
    plotter = _load_plotter("legacy_fuel_fb")

    exact_csv = tmp_path / "legacy_1r.csv"
    pd.DataFrame(
        {
            "time": [0.0, 2100.0],
            "core1R.region[1].fuelNode1.T": [600.0, 602.0],
            "core1R.region[2].fuelNode1.T": [610.0, 612.0],
            "core1R.region[1].fuelNode2.T": [620.0, 622.0],
            "core1R.grapNode.T": [650.0, 651.0],
            "core1R.react.TotalTempFeedback": [2.5e-3, 2.0e-3],
        }
    ).to_csv(exact_csv, index=False)
    t_s, fuel_avg, graphite, feedback_pcm = plotter.load_fuel_graphite_feedback(
        exact_csv
    )
    assert t_s.tolist() == [0.0, 2100.0]
    assert fuel_avg.tolist() == pytest.approx([612.5, 614.5])
    assert graphite.tolist() == pytest.approx([650.0, 651.0])
    assert feedback_pcm.tolist() == pytest.approx([250.0, 200.0])

    # Parent matcher semantics: the EXACT aggregated candidate wins when the
    # legacy 9R sumFB signal is present, even alongside regional channels.
    aggregated_csv = tmp_path / "legacy_9r_aggregated.csv"
    pd.DataFrame(
        {
            "time": [0.0],
            "msre9r.zone[1].fuelNode1.T": [600.0],
            "msre9r.zone[1].fuelNode2.T": [610.0],
            "msre9r.grapNode.T": [650.0],
            "msre9r.sumFB.reactivityOut.rho": [2.0e-3],
            "msre9r.rf1.TotalTempFeedback": [1.0e-3],
            "msre9r.rf2.TotalTempFeedback": [1.5e-3],
        }
    ).to_csv(aggregated_csv, index=False)
    _, fuel_avg, graphite, feedback_pcm = plotter.load_fuel_graphite_feedback(
        aggregated_csv
    )
    assert fuel_avg.tolist() == pytest.approx([605.0])
    assert graphite.tolist() == pytest.approx([650.0])
    assert feedback_pcm.tolist() == pytest.approx([200.0])  # exact sumFB channel

    # Without any exact candidate, the endswith scan resolves the FIRST
    # TotalTempFeedback-suffixed column (parent behavior: single channel,
    # no implicit summation on this loader).
    regional_csv = tmp_path / "legacy_9r_regional.csv"
    pd.DataFrame(
        {
            "time": [0.0],
            "msre9r.zone[1].fuelNode1.T": [600.0],
            "msre9r.zone[1].fuelNode2.T": [610.0],
            "msre9r.grapNode.T": [650.0],
            "msre9r.rf1.TotalTempFeedback": [1.0e-3],
            "msre9r.rf2.TotalTempFeedback": [1.5e-3],
        }
    ).to_csv(regional_csv, index=False)
    _, fuel_avg, graphite, feedback_pcm = plotter.load_fuel_graphite_feedback(
        regional_csv
    )
    assert fuel_avg.tolist() == pytest.approx([605.0])
    assert graphite.tolist() == pytest.approx([650.0])
    assert feedback_pcm.tolist() == pytest.approx([100.0])  # first match only


def test_legacy_load_uhx_signals_candidates_unchanged(tmp_path: Path) -> None:
    plotter = _load_plotter("legacy_uhx")
    csv_path = tmp_path / "legacy_uhx.csv"
    pd.DataFrame(
        {
            "time": [0.0, 8000.0],
            "core1R.powerblock.reactorPower": [1.0e6, 0.5e6],
            "core1R.powerblock.fissionPower.P": [9.5e5, 4.0e5],
            "core1R.powerblock.decayPower": [2.4e4, 2.2e4],
            "core1R.tempIn.T": [550.0, 551.0],
            "core1R.tempOut.T": [580.0, 578.0],
            "core1R.grapNode.T": [650.0, 649.0],
            "core1R.react.TotalTempFeedback": [2.5e-3, -1.0e-3],
        }
    ).to_csv(csv_path, index=False)
    signals = plotter.load_uhx_signals(csv_path)
    assert set(signals) == {
        "time",
        "total_power",
        "fission_power",
        "decay_power",
        "fuel_in",
        "fuel_out",
        "graphite",
        "feedback_pcm",
    }
    assert signals["total_power"].tolist() == pytest.approx([1.0e6, 0.5e6])
    assert signals["fission_power"].tolist() == pytest.approx([9.5e5, 4.0e5])
    assert signals["decay_power"].tolist() == pytest.approx([2.4e4, 2.2e4])
    assert signals["fuel_in"].tolist() == pytest.approx([550.0, 551.0])
    assert signals["fuel_out"].tolist() == pytest.approx([580.0, 578.0])
    assert signals["graphite"].tolist() == pytest.approx([650.0, 649.0])
    assert signals["feedback_pcm"].tolist() == pytest.approx([250.0, -100.0])


def test_plotter_case_style_tables_unchanged_from_parent() -> None:
    plotter = _load_plotter("legacy_plot_tables")
    assert plotter.STEP_CASES == [
        ("step_2dol", "2.0 $", "#2ca02c"),
        ("step_1dol", "1.0 $", "#1f77b4"),
        ("step_0p5dol", "0.5 $", "#d62728"),
    ]
    assert plotter.FLOW_CASES == [
        ("flow_100pct", "1.0× flow", "#2ca02c"),
        ("flow_66pct", "2/3× flow", "#1f77b4"),
        ("flow_33pct", "1/3× flow", "#d62728"),
    ]
    # CORE_STYLE intentionally diverged from the pre-R2.8 parent at
    # TASK-20260901-01 P3 (commit 945125d): reviewer R2.8 requires 1R/9R to
    # separate without color alone, so the styles now pin an HLS lightness
    # split (dark 1R / light 9R), a lengthened 1R dash, and distinct widths.
    assert plotter.CORE_STYLE == {
        "1r": {
            "label": "1R",
            "line": (0, (6.0, 2.5)),
            "lightness_scale": 0.70,
            "linewidth": 1.6,
        },
        "9r": {
            "label": "9R",
            "line": "-",
            "lightness_scale": 1.50,
            "linewidth": 1.1,
        },
    }
    assert plotter.UHX_CASE == "uhx_trip"


def test_plotter_core_styles_grayscale_distinguishable() -> None:
    """R2.8 invariant (TASK-20260901-01 P3): 1R vs 9R must survive grayscale.

    For every STEP/FLOW base color the BT.601 luminance of the 1r-styled and
    9r-styled variants must differ by >= 0.30 (on a 0..1 scale) -- i.e. the
    darker/lighter split is large enough that a grayscale rendering still
    separates the core models without relying on hue. Dash pattern and
    linewidth must also differ as a second non-color channel.
    """
    plotter = _load_plotter("core_style_grayscale")

    def _bt601_luminance(hex_color: str) -> float:
        hex_color = hex_color.lstrip("#")
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return 0.299 * r + 0.587 * g + 0.114 * b

    for case_key, _, base_color in plotter.STEP_CASES + plotter.FLOW_CASES:
        lum_1r = _bt601_luminance(plotter._core_color(base_color, "1r"))
        lum_9r = _bt601_luminance(plotter._core_color(base_color, "9r"))
        assert lum_9r - lum_1r >= 0.30, (
            f"{case_key} ({base_color}): 9R-1R luminance gap "
            f"{lum_9r - lum_1r:.4f} < 0.30"
        )

    # Non-color channels: dash pattern and linewidth differ between cores.
    assert plotter.CORE_STYLE["1r"]["line"] != plotter.CORE_STYLE["9r"]["line"]
    assert plotter._core_linewidth("1r") != plotter._core_linewidth("9r")


# ---------------------------------------------------------------------------
# Output-dir resolution: legacy unchanged, segmented nests (Ambiguity J).
# ---------------------------------------------------------------------------


def test_resolve_outputs_for_core_legacy_behavior_unchanged(tmp_path: Path) -> None:
    plotter = _load_plotter("resolve_legacy")

    root = tmp_path / "per_core"
    (root / "9r").mkdir(parents=True)
    assert plotter._resolve_outputs_for_core(root, "9r") == root / "9r"

    flat = tmp_path / "flat_1r"
    flat.mkdir()
    assert plotter._resolve_outputs_for_core(flat, "1r") == flat  # old 1R layout

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="Missing outputs for core"):
        plotter._resolve_outputs_for_core(empty, "9r")


def test_resolve_outputs_for_core_segmented_nests_with_alt_fallback(
    tmp_path: Path,
) -> None:
    plotter = _load_plotter("resolve_seg")

    root = tmp_path / "nested"
    (root / "segmented" / "9r").mkdir(parents=True)
    assert (
        plotter._resolve_outputs_for_core(root, "9r", package="segmented")
        == root / "segmented" / "9r"
    )

    # Callers may pass the segmented/ dir itself (alt resolution).
    alt_root = tmp_path / "alt"
    (alt_root / "9r").mkdir(parents=True)
    assert (
        plotter._resolve_outputs_for_core(alt_root, "9r", package="segmented")
        == alt_root / "9r"
    )

    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(FileNotFoundError, match="Missing segmented outputs"):
        plotter._resolve_outputs_for_core(bare, "9r", package="segmented")

    # Default keyword stays LEGACY even for a segmented-looking tree: root has
    # no 9r/ subdir (only segmented/9r), and the flat fallback is 1R-only.
    with pytest.raises(FileNotFoundError, match="Missing outputs for core"):
        plotter._resolve_outputs_for_core(root, "9r")


# ---------------------------------------------------------------------------
# 5) Plotter segmented column resolution (declared candidates, synthetic CSVs).
# ---------------------------------------------------------------------------


def _panel_columns_for(core_key: str) -> tuple[list[str], list[str]]:
    if core_key == "1r":
        return ["TF1", "TF2"], ["TG"]
    return [f"TZout[{idx}]" for idx in range(1, 5)], ["TPot"]


def test_segmented_panel_column_groups_match_ambiguity_h_decision() -> None:
    plotter = _load_plotter("panel_cols")
    for core_key in ("1r", "9r"):
        fuel_cols, slow_cols = plotter.segmented_temperature_columns(
            seg_runs, core_key
        )
        expected_fuel, expected_slow = _panel_columns_for(core_key)
        assert fuel_cols == expected_fuel
        assert slow_cols == expected_slow
        inlet, outlet = plotter.segmented_loop_temperature_columns(
            seg_runs, core_key
        )
        assert inlet == ["TinCore"]
        assert outlet == (["ToutCore"] if core_key == "1r" else ["ToutPlenum"])


def _write_segmented_step_csv(
    path: Path,
    core_key: str,
    *,
    drop: tuple[str, ...] = (),
) -> Path:
    data: dict[str, list[float]] = {"time": [0.0, 3000.0]}
    data["pb.reactorPower"] = [2.0e6, 1.9e6]
    for idx, channel in enumerate(seg_runs.feedback_columns_for(core_key), start=1):
        data[channel] = [idx * 1.0e-4, idx * 2.0e-4]
    fuel_cols, slow_cols = _panel_columns_for(core_key)
    for idx, col in enumerate(fuel_cols, start=1):
        data[col] = [600.0 + idx, 610.0 + idx]
    for idx, col in enumerate(slow_cols, start=1):
        data[col] = [700.0 + idx, 705.0 + idx]

    frame = pd.DataFrame(data).drop(columns=list(drop))
    frame.to_csv(path, index=False)
    return path


def test_read_segmented_step_signals_9r_sums_rf_channels_times_1e5(
    tmp_path: Path,
) -> None:
    plotter = _load_plotter("step_reader_9r")
    csv_path = _write_segmented_step_csv(tmp_path / "step_1dol_res.csv", "9r")
    t_s, p_kw, fuel_avg, slow_node, feedback_pcm = plotter.read_segmented_step_signals(
        csv_path, "9r"
    )
    assert t_s.tolist() == [0.0, 3000.0]
    assert p_kw.tolist() == pytest.approx([2000.0, 1900.0])  # W -> kW via to_kw
    # mean(TZout[1..4]): row1 = mean(601..604), row2 = mean(611..614).
    assert fuel_avg.tolist() == pytest.approx([602.5, 612.5])
    assert slow_node.tolist() == pytest.approx([701.0, 706.0])  # TPot
    # sum(RF1..RF9)*1e5: rows give 45e-4 and 90e-4 in delta-units.
    assert feedback_pcm.tolist() == pytest.approx([450.0, 900.0])


def test_read_segmented_step_signals_1r_single_feedback_channel(
    tmp_path: Path,
) -> None:
    plotter = _load_plotter("step_reader_1r")
    csv_path = _write_segmented_step_csv(tmp_path / "step_1dol_res.csv", "1r")
    _, p_kw, fuel_avg, slow_node, feedback_pcm = plotter.read_segmented_step_signals(
        csv_path, "1r"
    )
    assert p_kw.tolist() == pytest.approx([2000.0, 1900.0])
    assert fuel_avg.tolist() == pytest.approx([(601.0 + 602.0) / 2.0, (611.0 + 612.0) / 2.0])
    assert slow_node.tolist() == pytest.approx([701.0, 706.0])  # TG
    assert feedback_pcm.tolist() == pytest.approx([10.0, 20.0])  # fb*1e5, 1 channel


def test_read_segmented_step_headers_built_from_declared_surface(
    tmp_path: Path,
) -> None:
    """Header composed ONLY from helpers.segmented_runs declarations resolves."""
    plotter = _load_plotter("step_reader_declared")
    data: dict[str, list[float]] = {"time": [0.0]}
    data[next(iter(seg_runs.CSV_POWER_COLUMN_CANDIDATES))] = [2.0e6]
    for idx, channel in enumerate(seg_runs.feedback_columns_for("9r"), start=1):
        data[channel] = [idx * 1.0e-4]
    for col in seg_runs.overlay_columns_for("9r"):
        if col.startswith("TZout["):
            zone = int(col[len("TZout["):-1])
            data[col] = [600.0 + zone]
        elif col == "TPot":
            data[col] = [700.0]
        else:
            data[col] = [1.0]
    csv_path = tmp_path / "declared_res.csv"
    pd.DataFrame(data).to_csv(csv_path, index=False)

    _, p_kw, fuel_avg, slow_node, feedback_pcm = plotter.read_segmented_step_signals(
        csv_path, "9r"
    )
    assert p_kw.tolist() == pytest.approx([2000.0])
    assert fuel_avg.tolist() == pytest.approx([602.5])
    assert slow_node.tolist() == pytest.approx([700.0])
    assert feedback_pcm.tolist() == pytest.approx([450.0])


def test_read_segmented_step_power_suffix_fallback_chain(tmp_path: Path) -> None:
    """Without the exact candidates, the declared endswith chain still finds
    the decorated power column (find_column exact -> suffix fallback)."""
    plotter = _load_plotter("step_reader_suffix")
    csv_path = _write_segmented_step_csv(tmp_path / "decorated_res.csv", "9r")
    frame = pd.read_csv(csv_path).drop(columns=["pb.reactorPower"])
    frame.insert(1, "plant.reactorPower", [2.0e6, 1.9e6])
    frame.to_csv(csv_path, index=False)

    _, p_kw, *_rest = plotter.read_segmented_step_signals(csv_path, "9r")
    assert p_kw.tolist() == pytest.approx([2000.0, 1900.0])


def test_read_segmented_uhx_signals_9r_uses_toutplenum_and_tpot(
    tmp_path: Path,
) -> None:
    plotter = _load_plotter("uhx_reader_9r")
    data: dict[str, list[float]] = {
        "time": [0.0, 12000.0],
        "pb.reactorPower": [1.0e6, 0.8e6],
        "pb.fissionPower.P": [9.5e5, 7.0e5],
        "pb.decayPower": [2.4e4, 2.3e4],
        "TinCore": [550.0, 552.0],
        "ToutPlenum": [580.0, 578.0],
        "TPot": [700.0, 699.0],
    }
    for idx, channel in enumerate(seg_runs.feedback_columns_for("9r"), start=1):
        data[channel] = [idx * 1.0e-4, idx * 0.5e-4]
    csv_path = tmp_path / "uhx_trip_res.csv"
    pd.DataFrame(data).to_csv(csv_path, index=False)

    signals = plotter.read_segmented_uhx_signals(csv_path, "9r")
    assert set(signals) == {
        "time",
        "total_power",
        "fission_power",
        "decay_power",
        "fuel_in",
        "fuel_out",
        "graphite",
        "feedback_pcm",
    }
    # Total/fission/decay stay RAW (normalization happens in plot_uhx).
    assert signals["total_power"].tolist() == pytest.approx([1.0e6, 0.8e6])
    assert signals["fission_power"].tolist() == pytest.approx([9.5e5, 7.0e5])
    assert signals["decay_power"].tolist() == pytest.approx([2.4e4, 2.3e4])
    assert signals["fuel_in"].tolist() == pytest.approx([550.0, 552.0])
    assert signals["fuel_out"].tolist() == pytest.approx([580.0, 578.0])  # ToutPlenum
    assert signals["graphite"].tolist() == pytest.approx([700.0, 699.0])  # TPot
    assert signals["feedback_pcm"].tolist() == pytest.approx([450.0, 225.0])


def test_read_segmented_uhx_signals_1r_uses_toutcore_and_tg(tmp_path: Path) -> None:
    plotter = _load_plotter("uhx_reader_1r")
    data: dict[str, list[float]] = {
        "time": [0.0],
        "pb.reactorPower": [1.0e6],
        "pb.fissionPower.P": [9.5e5],
        "pb.decayPower": [2.4e4],
        "TinCore": [550.0],
        "ToutCore": [585.0],
        "TG": [650.0],
        "fb.TotalTempFeedback": [2.5e-3],
    }
    csv_path = tmp_path / "uhx_trip_res.csv"
    pd.DataFrame(data).to_csv(csv_path, index=False)

    signals = plotter.read_segmented_uhx_signals(csv_path, "1r")
    assert signals["fuel_out"].tolist() == pytest.approx([585.0])  # ToutCore
    assert signals["graphite"].tolist() == pytest.approx([650.0])  # TG
    assert signals["feedback_pcm"].tolist() == pytest.approx([250.0])


@pytest.mark.parametrize(
    "drop, needle",
    [
        (("pb.reactorPower",), "no segmented column for reactor power"),
        (("rf7.TotalTempFeedback",), "temperature-feedback channel"),
        (("TZout[2]",), "fuel-panel column"),
        (("TPot",), "slow-node column"),
    ],
)
def test_read_segmented_step_missing_columns_raise_clear_errors(
    tmp_path: Path, drop: tuple[str, ...], needle: str
) -> None:
    plotter = _load_plotter("step_reader_missing")
    csv_path = _write_segmented_step_csv(tmp_path / "broken_res.csv", "9r", drop=drop)
    with pytest.raises(KeyError, match=needle):
        plotter.read_segmented_step_signals(csv_path, "9r")


def test_read_segmented_step_missing_time_column_raises(tmp_path: Path) -> None:
    plotter = _load_plotter("step_reader_no_time")
    csv_path = _write_segmented_step_csv(tmp_path / "notime_res.csv", "9r")
    frame = pd.read_csv(csv_path).drop(columns=["time"])
    frame.to_csv(csv_path, index=False)
    with pytest.raises(KeyError, match="missing time column"):
        plotter.read_segmented_step_signals(csv_path, "9r")


def test_read_segmented_uhx_missing_decay_column_raises(tmp_path: Path) -> None:
    plotter = _load_plotter("uhx_reader_no_decay")
    data: dict[str, list[float]] = {
        "time": [0.0],
        "pb.reactorPower": [1.0e6],
        "pb.fissionPower.P": [9.5e5],
        "TinCore": [550.0],
        "ToutPlenum": [580.0],
        "TPot": [700.0],
        "fb.TotalTempFeedback": [2.5e-3],
    }
    csv_path = tmp_path / "uhx_nodecay_res.csv"
    pd.DataFrame(data).to_csv(csv_path, index=False)
    with pytest.raises(KeyError, match="no segmented column for decay power"):
        plotter.read_segmented_uhx_signals(csv_path, "9r")


# ---------------------------------------------------------------------------
# Reader numeric tolerance: one stray omc token gaps, never aborts
# (REV-2fdd01d-01, fix 93980a3).
# ---------------------------------------------------------------------------


#: The exact glitching token shape observed on 9R flow_66pct_res.csv.
_BAD_TOKEN_CELL = "983013.774829801="


def _corrupt_csv_cell(csv_path: Path, row: int, column: str, token: str) -> None:
    """Overwrite ONE csv data cell with a raw non-numeric omc-glitch token."""
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    column_index = lines[0].split(",").index(column)
    cells = lines[row].split(",")
    cells[column_index] = token
    lines[row] = ",".join(cells)
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_read_segmented_step_signals_coerces_stray_token_to_nan_gap(
    tmp_path: Path,
) -> None:
    """One malformed power cell becomes a NaN gap; clean cells identical."""
    plotter = _load_plotter("step_reader_coerce")
    clean_path = _write_segmented_step_csv(tmp_path / "clean_res.csv", "9r")
    dirty_path = tmp_path / "dirty_res.csv"
    dirty_path.write_text(clean_path.read_text(encoding="utf-8"), encoding="utf-8")
    _corrupt_csv_cell(
        dirty_path, row=1, column="pb.reactorPower", token=_BAD_TOKEN_CELL
    )

    (
        clean_t,
        clean_kw,
        clean_fuel,
        clean_slow,
        clean_fb,
    ) = plotter.read_segmented_step_signals(clean_path, "9r")
    # Pre-fix this raised ValueError: could not convert string to float.
    t_s, p_kw, fuel_avg, slow_node, feedback_pcm = (
        plotter.read_segmented_step_signals(dirty_path, "9r")
    )

    assert np.isnan(p_kw[0])  # malformed sample gapped...
    assert p_kw[1] == pytest.approx(clean_kw[1])  # ...well-formed one intact
    np.testing.assert_array_equal(t_s, clean_t)
    np.testing.assert_allclose(fuel_avg, clean_fuel)
    np.testing.assert_allclose(slow_node, clean_slow)
    np.testing.assert_allclose(feedback_pcm, clean_fb)


def test_read_segmented_uhx_signals_coerces_stray_token_to_nan_gap(
    tmp_path: Path,
) -> None:
    """One malformed slow-node cell gaps graphite only; others identical."""
    plotter = _load_plotter("uhx_reader_coerce")
    data: dict[str, list[float]] = {
        "time": [0.0, 12000.0],
        "pb.reactorPower": [1.0e6, 0.8e6],
        "pb.fissionPower.P": [9.5e5, 7.0e5],
        "pb.decayPower": [2.4e4, 2.3e4],
        "TinCore": [550.0, 552.0],
        "ToutPlenum": [580.0, 578.0],
        "TPot": [700.0, 699.0],
    }
    for idx, channel in enumerate(seg_runs.feedback_columns_for("9r"), start=1):
        data[channel] = [idx * 1.0e-4, idx * 0.5e-4]
    clean_path = tmp_path / "clean_res.csv"
    dirty_path = tmp_path / "dirty_res.csv"
    pd.DataFrame(data).to_csv(clean_path, index=False)
    dirty_path.write_text(clean_path.read_text(encoding="utf-8"), encoding="utf-8")
    _corrupt_csv_cell(dirty_path, row=1, column="TPot", token="701.25=")

    clean = plotter.read_segmented_uhx_signals(clean_path, "9r")
    signals = plotter.read_segmented_uhx_signals(dirty_path, "9r")  # no raise

    assert np.isnan(signals["graphite"][0])
    assert signals["graphite"][1] == pytest.approx(clean["graphite"][1])
    for key in (
        "time",
        "total_power",
        "fission_power",
        "decay_power",
        "fuel_in",
        "fuel_out",
        "feedback_pcm",
    ):
        np.testing.assert_allclose(signals[key], clean[key])


def test_plot_flow_survives_one_stray_token_in_segmented_csv(tmp_path: Path) -> None:
    """End-to-end REV-2fdd01d-01: plot_flow renders despite one bad cell.

    The R2 review watched exactly this die on the implementer tree: a single
    ``983013.774829801=`` cell aborted MSRRstep_flow.png after plot_steps
    had succeeded. The coerced NaN must gap one sample inside the drawn
    window instead of raising.
    """
    plotter = _load_plotter("plot_flow_coerce")
    core_dir = tmp_path / "segmented" / "9r"
    core_dir.mkdir(parents=True)
    for case in ("flow_100pct", "flow_66pct", "flow_33pct"):
        _write_plot_fixture_csv(core_dir / f"{case}_res.csv", "9r", "flow")

    # Fixture flow grid: header line + t = 3800..4500 s step 1 s, so
    # t = 4005 s (inside the [-50, 400] window) sits on line index 206.
    _corrupt_csv_cell(
        core_dir / "flow_66pct_res.csv",
        row=206,
        column="pb.reactorPower",
        token=_BAD_TOKEN_CELL,
    )

    figs = tmp_path / "figs"
    figs.mkdir()
    figure = plotter.plot_flow(
        figs,
        {"9r": core_dir},
        ["9r"],
        readers={
            "9r": lambda csv_path: plotter.read_segmented_step_signals(
                csv_path, "9r"
            )
        },
    )
    assert figure == figs / "MSRRstep_flow.png"
    assert figure.is_file() and figure.stat().st_size > 1000


# ---------------------------------------------------------------------------
# Segmented plot wiring e2e (readers + nested dirs render real PNGs).
# ---------------------------------------------------------------------------


def _write_plot_fixture_csv(path: Path, core_key: str, kind: str) -> None:
    """Synthetic result CSV for pipeline well-posedness smokes ONLY.

    Cell magnitudes are NOT physical temperatures: they reuse the degC-era
    numerals (~550-700) as convenient arbitrary data. Real segmented CSVs
    carry kelvin since TASK-20260825-04, so the render funnel will draw these
    cells shifted by -273.15 (see ``_read_step_signals`` / ``plot_uhx``);
    every consumer here asserts shapes/NaN handling/file creation only,
    never axis values.
    """
    if kind == "step":
        t = np.arange(1800.0, 2501.0, 1.0)
    elif kind == "flow":
        t = np.arange(3800.0, 4501.0, 1.0)
    else:
        t = np.arange(2000.0, 18501.0, 25.0)

    data: dict[str, np.ndarray] = {
        "time": t,
        "pb.reactorPower": 1.0e6 * (1.0 + 0.02 * np.sin(t / 50.0)),
        "pb.fissionPower.P": 9.5e5 * (1.0 + 0.02 * np.sin(t / 50.0)),
        "pb.decayPower": 2.4e4 * (1.0 + 0.01 * np.sin(t / 200.0)),
    }
    for idx, channel in enumerate(seg_runs.feedback_columns_for(core_key), start=1):
        data[channel] = idx * 1.0e-4 * (1.0 + 0.05 * np.sin(t / 100.0))
    fuel_cols, slow_cols = _panel_columns_for(core_key)
    for idx, col in enumerate(fuel_cols, start=1):
        data[col] = 600.0 + idx + 2.0 * np.sin(t / 80.0)
    for idx, col in enumerate(slow_cols, start=1):
        data[col] = 700.0 + idx + np.sin(t / 90.0)
    data["TinCore"] = 550.0 + np.sin(t / 70.0)
    outlet_cols = ["ToutCore"] if core_key == "1r" else ["ToutPlenum"]
    for offset, col in enumerate(outlet_cols):
        data[col] = 585.0 + offset + np.sin(t / 75.0)

    pd.DataFrame(data).to_csv(path, index=False)


def test_segmented_plot_main_renders_three_figures_from_nested_dirs(
    tmp_path: Path, monkeypatch
) -> None:
    plotter = _load_plotter("plot_smoke")
    outputs = tmp_path / "outputs"
    for core_key in ("1r", "9r"):
        core_dir = outputs / "segmented" / core_key
        core_dir.mkdir(parents=True)
        for case in ("step_2dol", "step_1dol", "step_0p5dol"):
            _write_plot_fixture_csv(core_dir / f"{case}_res.csv", core_key, "step")
        for case in ("flow_100pct", "flow_66pct", "flow_33pct"):
            _write_plot_fixture_csv(core_dir / f"{case}_res.csv", core_key, "flow")
        _write_plot_fixture_csv(core_dir / "uhx_trip_res.csv", core_key, "uhx")

    figs = tmp_path / "figs"
    _use_argv(
        monkeypatch,
        "--package", "segmented",
        "--outputs_dir", str(outputs),
        "--fig_dir", str(figs),
    )
    assert plotter.main() == 0
    for name in ("MSRRstep_nominal.png", "MSRRstep_flow.png", "MSRR_uhx_trip.png"):
        figure = figs / name
        assert figure.is_file() and figure.stat().st_size > 1000


# ---------------------------------------------------------------------------
# TASK-20260825-04 P4 pins on the P3 unit boundary (commit ec1f640):
# segmented result CSVs are KELVIN; the raw readers stay kelvin-faithful and
# ONLY the render funnel shifts K->degC so the panels keep their pre-
# conversion degC scale.
# ---------------------------------------------------------------------------


def test_read_segmented_step_signals_stays_kelvin_faithful(tmp_path: Path) -> None:
    """Raw segmented readers return CSV values VERBATIM (no unit shift).

    Since commit 2a43887 every segmented temperature column reports kelvin;
    helpers.segmented_runs documents that consumers convert at their own
    boundary. A reader-side shift would double-convert once the render
    funnel subtracts 273.15, so the read boundary must pass ~843-873 K
    magnitudes through untouched.
    """
    plotter = _load_plotter("step_reader_kelvin")
    zone_k = {1: 871.25, 2: 869.5, 3: 866.75, 4: 863.0}  # TZout[i], kelvin
    pot_k = 858.15                                       # TPot, kelvin
    data: dict[str, list[float]] = {"time": [0.0, 3000.0]}
    data["pb.reactorPower"] = [2.0e6, 1.9e6]
    for idx, channel in enumerate(seg_runs.feedback_columns_for("9r"), start=1):
        data[channel] = [idx * 1.0e-4, idx * 2.0e-4]
    for i, value in zone_k.items():
        data[f"TZout[{i}]"] = [value, value + 1.0]
    data["TPot"] = [pot_k, pot_k + 1.0]

    csv_path = tmp_path / "step_1dol_res.csv"
    pd.DataFrame(data).to_csv(csv_path, index=False)
    t_s, p_kw, fuel_avg, slow_node, feedback_pcm = plotter.read_segmented_step_signals(
        csv_path, "9r"
    )
    zone_mean = sum(zone_k.values()) / len(zone_k)
    assert min(fuel_avg) > 800.0  # kelvin magnitudes survived the read
    np.testing.assert_allclose(
        fuel_avg, [zone_mean, zone_mean + 1.0], atol=1e-12
    )
    np.testing.assert_allclose(slow_node, [pot_k, pot_k + 1.0], atol=1e-12)
    np.testing.assert_allclose(t_s, [0.0, 3000.0], atol=0)
    np.testing.assert_allclose(p_kw, [2000.0, 1900.0], rtol=0)
    assert feedback_pcm.shape == (2,)


def test_segmented_render_funnel_shifts_kelvin_to_degc() -> None:
    """The render funnel is the SINGLE K->degC boundary (P3, ec1f640).

    With a per-core segmented reader, ``_read_step_signals`` must hand the
    panels fuel/graphite in degC (nominal ~600/570 C scale) while time,
    power, and feedback pass through unit-invariant, and NaN gaps survive
    the shift unchanged. The legacy path (readers=None) never enters this
    branch, which is what keeps non-segmented rendering byte-identical.
    """
    plotter = _load_plotter("render_kelvin_boundary")
    t_in = np.array([1999.0, 2000.0, 2001.0])
    p_kw_in = np.array([1000.0, 999.0, 998.0])
    fuel_k = np.array([873.15, np.nan, 874.65])   # 600 / gap / 601.5 degC
    graphite_k = np.array([843.15, 843.65, np.nan])
    fb_in = np.array([10.0, -20.0, 30.0])

    def segmented_reader(_csv_path: Path) -> tuple:
        return t_in, p_kw_in, fuel_k.copy(), graphite_k.copy(), fb_in.copy()

    t_out, p_kw_out, fuel_c, graphite_c, fb_out = plotter._read_step_signals(
        {"1r": segmented_reader}, "1r", Path("ignored_res.csv")
    )
    np.testing.assert_allclose(fuel_c, [600.0, np.nan, 601.5], atol=1e-12)
    np.testing.assert_allclose(graphite_c, [570.0, 570.5, np.nan], atol=1e-12)
    assert np.isnan(fuel_c[1]) and np.isnan(graphite_c[2])
    np.testing.assert_allclose(t_out, t_in, atol=0)
    np.testing.assert_allclose(p_kw_out, p_kw_in, atol=0)
    np.testing.assert_allclose(fb_out, fb_in, atol=0)
