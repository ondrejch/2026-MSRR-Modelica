#!/usr/bin/env python3
"""Build the committed frequency-response gain prior from a finished campaign.

The prior (``data/scenarios/freq/fr_gain_prior.json``) feeds the two
protocol rules of ``freq/fr_protocol.py``: the target-swing perturbation
amplitude and the settling discard.  It is generated read-only from the
collected aggregates of one frequency campaign (default: the
``review-2026-09`` rebaseline, ``00runs/paper-rerun-review-2026-09/freq``)::

    python3.12 -m freq.build_gain_prior \\
        --source_root 00runs/paper-rerun-review-2026-09 \\
        --output data/scenarios/freq/fr_gain_prior.json

``--check`` regenerates in memory and exits 1 when the committed file
differs (content comparison; no timestamp is written, so regeneration from
the same inputs is byte-reproducible).

What is extracted, per core and power:

- ``gain``: the published ``gain`` column (normalized power per unit
  reactivity) on the campaign grid, rounded to 7 significant digits;
- ``resonance``: the resonance peak ``omega_n`` (log-parabolic
  interpolation of the gain maximum), the half-power (-3 dB) points
  ``omega_lo < omega_n < omega_hi`` (log-log interpolation), and
  ``zeta = (omega_hi - omega_lo) / (2 omega_n)``, ``sigma = zeta*omega_n``
  (exact for a second-order band-pass).  Powers whose peak or either
  half-power point lies outside the swept band get ``omega_n`` from the
  measured ``omega_n ~ sqrt(P)`` law anchored at the lowest measured power
  and ``sigma`` from the power law through the two lowest measured powers
  (``method = power_law_extrapolation``).

The time-domain evidence block (``settle_model.evidence``) re-fits the case
nearest ``omega_n`` at every measured power with 2-cycle sliding windows
and reports when the sliding gain settles; it documents the slow-mode floor
``sigma_floor_s_inv`` (see ``freq/README.md``).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

try:
    from ._common import fit_sine_least_squares, resolve_case_dir
    from . import fr_protocol
except ImportError:  # script-style execution from freq/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from freq._common import fit_sine_least_squares, resolve_case_dir
    from freq import fr_protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = REPO_ROOT / "00runs" / "paper-rerun-review-2026-09"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "scenarios" / "freq" / fr_protocol.DEFAULT_PRIOR_FILENAME
DEFAULT_POWERS = (1e-5, 1e-4, 1e-3, 1e-2, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2)
DEFAULT_CORES = ("1r", "9r")

#: Slow-mode floor on the settling decay rate [1/s] (tau = 2000 s).  Chosen
#: from the time-domain evidence block: near-resonance sliding gains at
#: P >= 0.4 MW keep drifting by 0.5-1.3 % for ~5000-6000 s after forcing
#: onset although the half-power width predicts sigma = 2.5e-3..1.4e-2 1/s
#: there, and the resonance-dominated 0.1 MW decay measures 5.3e-4 1/s.
SIGMA_FLOOR_S_INV = 5.0e-4

#: Sliding-window settle evidence: tolerance on |A(t)/A_late - 1|.
EVIDENCE_TOL = 0.0025


def _power_tag(power: float) -> str:
    try:
        from .paths import make_power_tag
    except ImportError:
        from freq.paths import make_power_tag
    return make_power_tag(float(power))


def _sig(value: float, digits: int = 7) -> float:
    return float(f"{float(value):.{digits}g}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_aggregate(path: Path) -> tuple[list[float], list[float]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{path}: empty aggregate")
    return (
        [float(row["frequency_rad_s"]) for row in rows],
        [float(row["gain"]) for row in rows],
    )


def half_power_resonance(omega: list[float], gain: list[float]) -> dict | None:
    """Peak + half-power width of one gain curve; None when out of band."""
    w = np.asarray(omega, dtype=float)
    g = np.asarray(gain, dtype=float)
    i = int(np.argmax(g))
    if i == 0 or i == len(w) - 1:
        return None
    coeffs = np.polyfit(np.log(w[i - 1:i + 2]), np.log(g[i - 1:i + 2]), 2)
    if coeffs[0] >= 0:
        return None
    x_peak = -coeffs[1] / (2.0 * coeffs[0])
    omega_n = float(math.exp(x_peak))
    peak = float(math.exp(np.polyval(coeffs, x_peak)))
    half = peak / math.sqrt(2.0)

    def crossing(k0: int, k1: int) -> float:
        t = (math.log(half) - math.log(g[k0])) / (math.log(g[k1]) - math.log(g[k0]))
        return float(math.exp(math.log(w[k0]) + t * (math.log(w[k1]) - math.log(w[k0]))))

    lo = hi = None
    for k in range(i, 0, -1):
        if g[k - 1] < half <= g[k]:
            lo = crossing(k - 1, k)
            break
    for k in range(i, len(w) - 1):
        if g[k + 1] < half <= g[k]:
            hi = crossing(k, k + 1)
            break
    if lo is None or hi is None:
        return None
    zeta = (hi - lo) / (2.0 * omega_n)
    return {
        "omega_n_rad_s": omega_n,
        "peak_gain": peak,
        "half_power_low_rad_s": lo,
        "half_power_high_rad_s": hi,
        "zeta": zeta,
        "sigma_s_inv": zeta * omega_n,
        "method": "half_power",
    }


def extrapolate_resonance(rows: list[dict], powers: list[float]) -> list[dict]:
    """Fill powers without an in-band resonance from the lowest measured rows."""
    measured = sorted((row for row in rows if row["method"] == "half_power"),
                      key=lambda row: row["power_mw"])
    if len(measured) < 2:
        raise ValueError("need >= 2 measured resonance rows to extrapolate")
    p0, p1 = measured[0]["power_mw"], measured[1]["power_mw"]
    s0, s1 = measured[0]["sigma_s_inv"], measured[1]["sigma_s_inv"]
    slope = math.log(s1 / s0) / math.log(p1 / p0)
    w0 = measured[0]["omega_n_rad_s"]
    have = {row["power_mw"] for row in rows}
    out = list(rows)
    for power in powers:
        if power in have:
            continue
        if power > p0:
            raise ValueError(
                f"power {power:g} MW above the lowest measured resonance "
                f"({p0:g} MW) has no in-band resonance; refusing to extrapolate"
            )
        omega_n = w0 * math.sqrt(power / p0)
        sigma = s0 * (power / p0) ** slope
        out.append(
            {
                "power_mw": power,
                "omega_n_rad_s": omega_n,
                "zeta": sigma / omega_n,
                "sigma_s_inv": sigma,
                "method": "power_law_extrapolation",
                "anchor_power_mw": p0,
                "sigma_power_law_exponent": slope,
                "omega_n_power_law_exponent": 0.5,
            }
        )
    return sorted(out, key=lambda row: row["power_mw"])


def sliding_settle_evidence(csv_path: Path, omega: float, ss_time: float) -> dict:
    """2-cycle sliding-window gains after forcing onset; settle time estimate."""
    data = np.loadtxt(csv_path, delimiter=",", skiprows=1, usecols=(0, 1))
    t, y = data[:, 0], data[:, 1]
    period = 2.0 * math.pi / omega
    t_end = float(t[-1])
    starts: list[float] = []
    amps: list[float] = []
    start = ss_time
    while start + 2.0 * period <= t_end + 1e-9:
        mask = (t >= start) & (t <= start + 2.0 * period)
        fit = fit_sine_least_squares(t[mask] - start, y[mask], omega)
        if fit.ok and fit.amplitude:
            starts.append(start - ss_time)
            amps.append(float(fit.amplitude))
        start += 0.5 * period
    if len(amps) < 4:
        return {"resolved": False, "reason": "fewer than 4 sliding windows"}
    late = 0.5 * (amps[-1] + amps[-2])
    settle = None
    for idx in range(len(amps)):
        if all(abs(a / late - 1.0) <= EVIDENCE_TOL for a in amps[idx:]):
            settle = starts[idx]
            break
    span = t_end - ss_time
    resolved = settle is not None and settle + 4.0 * period <= span
    return {
        "resolved": bool(resolved),
        "settle_s": (float(settle) if settle is not None else None),
        "forcing_span_s": float(span),
        "period_s": float(period),
        "tolerance": EVIDENCE_TOL,
        "initial_rel_deviation": float(amps[0] / late - 1.0),
        "max_rel_deviation_after_first_cycle": float(
            max(abs(a / late - 1.0) for a, s in zip(amps, starts) if s >= period)
        ) if any(s >= period for s in starts) else None,
    }


def _read_ss_time(run_dir: Path) -> float:
    for line in (run_dir / "run_params.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0] == "ss_time":
            return float(parts[1])
    raise ValueError(f"{run_dir}/run_params.txt has no ss_time")


def build_prior(source_root: Path, *, cores=DEFAULT_CORES, powers=DEFAULT_POWERS,
                evidence: bool = True, source_label: str | None = None) -> dict:
    source_root = Path(source_root)
    freq_root = source_root / "freq"
    metadata = source_root / "metadata"

    def meta(name: str) -> str | None:
        path = metadata / name
        return path.read_text(encoding="utf-8").strip() if path.is_file() else None

    aggregates: dict[str, dict] = {}
    core_blocks: dict[str, dict] = {}
    evidence_rows: list[dict] = []
    for core in cores:
        omega_ref: list[float] | None = None
        gain_rows: list[list[float]] = []
        resonance_rows: list[dict] = []
        vehicle = None
        for power in powers:
            run_dir = freq_root / core / f"power_{_power_tag(power)}"
            agg = run_dir / "FreqResponseResults.csv"
            omega, gain = read_aggregate(agg)
            if omega_ref is None:
                omega_ref = omega
            elif len(omega) != len(omega_ref) or any(
                not math.isclose(a, b, rel_tol=1e-9) for a, b in zip(omega, omega_ref)
            ):
                raise ValueError(f"{agg}: frequency grid differs from the first power")
            manifest_path = run_dir / "FreqResponseResults.manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
            vehicle = vehicle or (manifest.get("sweep") or {}).get("model_name")
            aggregates[f"{core}/power_{_power_tag(power)}/FreqResponseResults.csv"] = {
                "sha256": _sha256(agg),
                "rows": len(omega),
                "collection_status": manifest.get("collection_status"),
                "generation_id": manifest.get("generation_id"),
            }
            gain_rows.append([_sig(value) for value in gain])
            resonance = half_power_resonance(omega, gain)
            if resonance is not None:
                resonance["power_mw"] = float(power)
                resonance_rows.append(resonance)
                if evidence:
                    idx = int(np.argmin(np.abs(np.log(np.asarray(omega) / resonance["omega_n_rad_s"]))))
                    case_dir = Path(resolve_case_dir(str(run_dir), omega[idx]))
                    csv_candidates = sorted(case_dir.glob("*_res.csv"))
                    if csv_candidates:
                        row = sliding_settle_evidence(
                            csv_candidates[0], omega[idx], _read_ss_time(run_dir)
                        )
                        row.update(
                            {
                                "core": core,
                                "power_mw": float(power),
                                "omega_rad_s": float(omega[idx]),
                                "half_power_sigma_s_inv": resonance["sigma_s_inv"],
                                "half_power_settle_3_e_folds_s": 3.0 / resonance["sigma_s_inv"],
                            }
                        )
                        evidence_rows.append(row)
        resonance_rows = extrapolate_resonance(resonance_rows, [float(p) for p in powers])
        for row in resonance_rows:
            for key, value in list(row.items()):
                if isinstance(value, float) and key != "power_mw":
                    row[key] = _sig(value)
        core_blocks[core] = {
            "vehicle": vehicle,
            "omega_rad_s": [_sig(value, 12) for value in (omega_ref or [])],
            "powers_mw": [float(p) for p in powers],
            "gain": gain_rows,
            "resonance": resonance_rows,
        }
    for row in evidence_rows:
        for key, value in list(row.items()):
            if isinstance(value, float):
                row[key] = _sig(value, 5)
    generator = Path(__file__).resolve()
    return {
        "schema_version": fr_protocol.PRIOR_SCHEMA_VERSION,
        "kind": fr_protocol.PRIOR_KIND,
        "id": meta("campaign_id.txt") or source_root.name,
        "doc": (
            "Frequency-response gain prior for the legacy lumped 1R/9R "
            "nominal-trim sweeps: measured |G(omega, P)| (normalized power, "
            "1 = 1 MW, per unit reactivity dk/k) on the campaign grid plus "
            "per-power resonance parameters. Consumed by freq/fr_protocol.py "
            "(target-swing amplitude and settling discard). Regenerate with "
            "python3.12 -m freq.build_gain_prior; do not hand-edit."
        ),
        "gain_unit": "normalized power (1 = 1 MW) per unit reactivity (dk/k)",
        "provenance": {
            "source_campaign": meta("campaign_id.txt"),
            "source_root": source_label or str(source_root),
            "source_git_commit": meta("git_commit.txt"),
            "source_openmodelica_version": meta("openmodelica_version.txt"),
            "source_python_version": meta("python_version.txt"),
            "aggregates": aggregates,
            "generator": "freq/build_gain_prior.py",
            "generator_sha256": _sha256(generator),
            "known_bias": (
                "The source gains were fitted from the perturbation start "
                "with the legacy inverse-power amplitudes (1-20 pcm), so "
                "near-resonance gains are biased low (1.5-2.7 % at 1 MW, up "
                "to 21-28 % at 0.01 MW) and the 0.01 MW resonance is a "
                "large-swing describing function. The prior only sets the "
                "perturbation amplitude (a gain error e changes the realized "
                "swing by a factor 1/(1-e)) and the settle-time estimate; it "
                "is never used as a result."
            ),
        },
        "interpolation": (
            "log|G| linear in log(omega) and log(P); the larger of the plain "
            "and the omega/sqrt(P)-aligned interpolant is used between "
            "table powers (never under-predicts either); constant "
            "extrapolation in omega, |G| ~ P extrapolation in power."
        ),
        "settle_model": {
            "rule": "T_d = k / min(zeta*omega_n, sigma_floor_s_inv), k >= 3",
            "sigma_floor_s_inv": SIGMA_FLOOR_S_INV,
            "sigma_floor_note": (
                "Slow-mode floor (tau = 2000 s). The half-power width "
                "(zeta*omega_n) captures the resonance decay at P <= 0.2 MW "
                "(0.01 MW: 7.5e-5 1/s half-power vs 7.7e-5 1/s envelope "
                "decay; 0.1 MW: 6.1e-4 vs 5.3e-4 1/s) but not the slow "
                "amplitude drift seen at P >= 0.4 MW, where near-resonance "
                "sliding gains keep moving by 0.5-1.3 % for ~5000-6000 s "
                "after forcing onset (see evidence)."
            ),
            "evidence": evidence_rows,
        },
        "cores": core_blocks,
    }


_NUMERIC_LIST = re.compile(r"\[\s*((?:-?[0-9][0-9.eE+-]*,\s*)*-?[0-9][0-9.eE+-]*)\s*\]")


def render(payload: dict) -> str:
    """Stable JSON text: sorted keys, numeric arrays on one line each."""
    text = json.dumps(payload, indent=1, sort_keys=True)
    text = _NUMERIC_LIST.sub(
        lambda match: "[" + ", ".join(part.strip() for part in match.group(1).split(",")) + "]",
        text,
    )
    return text + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source_root", type=str, default=str(DEFAULT_SOURCE_ROOT),
                        help="Campaign root holding freq/<core>/power_<tag>/ and metadata/ "
                             f"(default: {DEFAULT_SOURCE_ROOT.relative_to(REPO_ROOT)})")
    parser.add_argument("--source_label", type=str,
                        default="00runs/paper-rerun-review-2026-09",
                        help="Repository-relative label recorded as provenance.source_root")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT),
                        help="Output JSON path (default: data/scenarios/freq/fr_gain_prior.json)")
    parser.add_argument("--no_evidence", action="store_true",
                        help="Skip the time-domain settle evidence (reads the case CSVs)")
    parser.add_argument("--check", action="store_true",
                        help="Regenerate in memory; exit 1 when --output differs")
    args = parser.parse_args(argv)
    payload = build_prior(
        Path(args.source_root),
        evidence=not args.no_evidence,
        source_label=args.source_label,
    )
    text = render(payload)
    output = Path(args.output)
    if args.check:
        current = output.read_text(encoding="utf-8") if output.is_file() else None
        if current != text:
            print(f"{output}: differs from a fresh build", file=sys.stderr)
            return 1
        print(f"{output}: up to date")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"wrote {output} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
