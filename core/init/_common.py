"""Shared helpers for steady-state setpoint generation scripts."""

from __future__ import annotations


def variable_filter_for_core(core_model: str) -> str:
    """Return the canonical reduced-output variable filter for a core model.

    The returned pattern is a normal Python/POSIX regex (single backslashes).
    Use :func:`mos_escape` before embedding it in a Modelica ``.mos`` string
    literal.
    """
    if core_model == "9r":
        return (
            r"^(time|msre9r\.upperPlenum\.T|msre9r\.R[1-9]\.(fuelNode1|fuelNode2|grapNode)\.T|"
            r"heatExchanger\.T_(in|out)_(p|s)Fluid\.T|heatExchanger\.T_[PST]N[1-4]|"
            r"pipe(HXtoUHX|UHXtoHX|DHRStoHX|HXtoCore|CoreToDHRS)\.tempPi|"
            r"dhrs\.tempOut\.T|uhx\.tempOut\.T|"
            r"msre9r\.mpke\.n_population\.n|msre9r\.powerblock\.reactorPower"
            r"|msre9r\.powerblock\.fissionPower\.P)$"
        )
    return (
        r"^(time|core1R\.fuelchannel\.(fuelNode1|fuelNode2|grapNode)\.T|"
        r"heatExchanger\.T_(in|out)_(p|s)Fluid\.T|heatExchanger\.T_[PST]N[1-4]|"
        r"pipe(HXtoUHX|UHXtoHX|DHRStoHX|HXtoCore|CoreToDHRS)\.tempPi|"
        r"dhrs\.tempOut\.T|uhx\.tempOut\.T|"
        r"core1R\.mpke\.n_population\.n|core1R\.powerblock\.reactorPower"
        r"|core1R\.powerblock\.fissionPower\.P)$"
    )


def mos_escape(value: str) -> str:
    """Escape a value for embedding inside a Modelica ``.mos`` string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
