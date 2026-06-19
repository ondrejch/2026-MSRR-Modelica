#!/usr/bin/env python3
"""Generate MSRR model schematics (1R/9R) for the journal article."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "latex" / "MSRR_journal_article" / "figures"


def _arrow(ax, start, end, label: str | None = None, offset=(0, 0)) -> None:
    arrow = FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>",
        mutation_scale=12,
        linewidth=1.2,
        color="#333333",
    )
    ax.add_patch(arrow)
    if label:
        ax.text(
            (start[0] + end[0]) / 2 + offset[0],
            (start[1] + end[1]) / 2 + offset[1],
            label,
            fontsize=9,
            ha="center",
            va="center",
            color="#333333",
        )


def _box(ax, xy, w, h, label: str, fc="#f3f3f3") -> Rectangle:
    rect = Rectangle(xy, w, h, linewidth=1.2, edgecolor="#333333", facecolor=fc)
    ax.add_patch(rect)
    ax.text(
        xy[0] + w / 2,
        xy[1] + h / 2,
        label,
        fontsize=9,
        ha="center",
        va="center",
        color="#111111",
    )
    return rect


def _core_block(ax, xy, w, h, regions: int) -> None:
    if regions == 1:
        _box(ax, xy, w, h, "Core (1 region)")
        return
    rect = _box(ax, xy, w, h, "Core (9 regions)")
    # Draw a 3x3 grid inside the core block to signal 9 regions.
    x0, y0 = xy
    for i in range(1, 3):
        ax.plot([x0 + i * w / 3, x0 + i * w / 3], [y0, y0 + h], color="#666666", lw=0.8)
        ax.plot([x0, x0 + w], [y0 + i * h / 3, y0 + i * h / 3], color="#666666", lw=0.8)
    rect.set_facecolor("#f8f8f8")


def draw_block(regions: int, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis("off")

    _core_block(ax, (0.6, 2.0), 2.0, 2.0, regions)
    _box(ax, (3.0, 3.6), 1.6, 1.0, "Primary\nPump", fc="#e8f4ff")
    _box(ax, (5.2, 3.6), 2.0, 1.0, "PHX\n(primary)", fc="#fff4e6")
    _box(ax, (7.6, 3.6), 1.8, 1.0, "Pipe\nreturn", fc="#f3f3f3")
    _box(ax, (5.2, 1.0), 2.0, 1.0, "PHX\n(secondary)", fc="#fff4e6")
    _box(ax, (7.6, 1.0), 1.8, 1.0, "UHX", fc="#ffe6e6")
    _box(ax, (3.0, 1.0), 1.6, 1.0, "Secondary\nPump", fc="#e8f4ff")
    _box(ax, (3.0, 2.0), 1.6, 1.0, "DHRS", fc="#e6ffe6")

    # Primary loop arrows.
    _arrow(ax, (2.6, 3.0), (3.0, 4.1), label="fuel", offset=(0.0, 0.3))
    _arrow(ax, (4.6, 4.1), (5.2, 4.1))
    _arrow(ax, (7.2, 4.1), (7.6, 4.1))
    _arrow(ax, (9.4, 4.1), (9.4, 2.6))
    _arrow(ax, (9.4, 2.6), (2.6, 2.6))
    _arrow(ax, (2.6, 2.6), (2.6, 3.0))

    # DHRS branch (inline indication).
    _arrow(ax, (2.6, 2.6), (3.0, 2.6))

    # Secondary loop arrows.
    _arrow(ax, (4.6, 1.5), (5.2, 1.5), label="coolant", offset=(0.0, -0.35))
    _arrow(ax, (7.2, 1.5), (7.6, 1.5))
    _arrow(ax, (9.4, 1.5), (9.4, 0.6))
    _arrow(ax, (9.4, 0.6), (3.0, 0.6))
    _arrow(ax, (3.0, 0.6), (3.0, 1.0))

    # Kinetics/reactivity block.
    _box(ax, (0.6, 4.6), 2.0, 1.0, "Reactivity\n& Kinetics", fc="#f0e6ff")
    _arrow(ax, (1.6, 4.6), (1.6, 4.0))
    ax.text(0.6, 5.8, "Model construction (block view)", fontsize=10, ha="left")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, format="svg")
    plt.close(fig)


def draw_loop(regions: int, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 7)
    ax.axis("off")

    # Core
    _core_block(ax, (0.8, 2.2), 2.2, 2.6, regions)
    # Primary loop components
    _box(ax, (3.6, 4.6), 1.6, 1.0, "Primary\nPump", fc="#e8f4ff")
    _box(ax, (6.0, 4.6), 2.0, 1.0, "PHX", fc="#fff4e6")
    _box(ax, (9.0, 4.6), 1.8, 1.0, "Pipe", fc="#f3f3f3")
    _box(ax, (4.0, 2.4), 1.6, 1.0, "DHRS", fc="#e6ffe6")
    _box(ax, (6.0, 1.0), 2.0, 1.0, "PHX", fc="#fff4e6")
    _box(ax, (9.0, 1.0), 1.8, 1.0, "UHX", fc="#ffe6e6")
    _box(ax, (3.6, 1.0), 1.6, 1.0, "Secondary\nPump", fc="#e8f4ff")

    # Primary loop arrows (clockwise)
    _arrow(ax, (3.0, 3.5), (3.6, 5.1), label="fuel", offset=(0.0, 0.3))
    _arrow(ax, (5.2, 5.1), (6.0, 5.1))
    _arrow(ax, (8.0, 5.1), (9.0, 5.1))
    _arrow(ax, (10.8, 5.1), (10.8, 2.7))
    _arrow(ax, (10.8, 2.7), (3.0, 2.7))
    _arrow(ax, (3.0, 2.7), (3.0, 3.5))

    # DHRS inline segment
    _arrow(ax, (3.0, 2.7), (4.0, 2.7))

    # Secondary loop arrows (clockwise)
    _arrow(ax, (5.2, 1.5), (6.0, 1.5), label="coolant", offset=(0.0, -0.35))
    _arrow(ax, (8.0, 1.5), (9.0, 1.5))
    _arrow(ax, (10.8, 1.5), (10.8, 0.6))
    _arrow(ax, (10.8, 0.6), (3.6, 0.6))
    _arrow(ax, (3.6, 0.6), (3.6, 1.0))

    # Reactivity/kinetics block
    _box(ax, (0.8, 5.2), 2.2, 1.0, "Reactivity\n& Kinetics", fc="#f0e6ff")
    _arrow(ax, (1.9, 5.2), (1.9, 4.8))
    ax.text(0.8, 6.6, "Model construction (loop view)", fontsize=10, ha="left")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, format="svg")
    plt.close(fig)


def _legend(ax, x, y) -> None:
    ax.text(x, y, "Fuel Salt", fontsize=9, va="center")
    ax.text(x, y - 0.5, "Coolant Salt", fontsize=9, va="center")
    ax.text(x, y - 1.0, "Air", fontsize=9, va="center")
    ax.text(x + 3.0, y, "Heat Removal (when activated)", fontsize=9, va="center")
    ax.text(x + 6.4, y, "Nodes with Fission Power", fontsize=9, va="center")
    ax.text(x + 6.4, y - 0.5, "Nodes with Decay Power", fontsize=9, va="center")
    ax.text(x + 6.4, y - 1.0, "Time Delay", fontsize=9, va="center")

    ax.plot([x - 0.5, x], [y, y], color="#1f77b4", lw=2.5)
    ax.plot([x - 0.5, x], [y - 0.5, y - 0.5], color="#d62728", lw=2.5)
    ax.plot([x - 0.5, x], [y - 1.0, y - 1.0], color="#ff7f0e", lw=2.5)
    ax.plot([x + 2.5, x + 2.5], [y - 0.15, y + 0.15], color="#d62728", lw=2.5)
    ax.add_patch(Rectangle((x + 5.9, y - 0.18), 0.4, 0.36, facecolor="#ffffff", edgecolor="#1f77b4", lw=2))
    ax.add_patch(Rectangle((x + 5.9, y - 0.68), 0.4, 0.36, facecolor="#ffffff", edgecolor="#17becf", lw=2))
    ax.add_patch(plt.Polygon([[x + 5.95, y - 1.1], [x + 6.35, y - 1.25], [x + 5.95, y - 1.4]], closed=True, fill=False, edgecolor="#1f77b4", lw=2))


def _node(ax, xy, w, h, label, edge, fc="#ffffff", lw=2.0) -> Rectangle:
    rect = Rectangle(xy, w, h, linewidth=lw, edgecolor=edge, facecolor=fc)
    ax.add_patch(rect)
    ax.text(xy[0] + w / 2, xy[1] + h / 2, label, fontsize=8.5, ha="center", va="center")
    return rect


def draw_nodal(regions: int, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11.2, 6.0))
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 10)
    ax.axis("off")

    # Legend
    _legend(ax, 2.0, 9.2)

    # Core block
    core_box = Rectangle((1.0, 3.0), 4.0, 3.0, linewidth=1.5, edgecolor="#333333", facecolor="none", linestyle="--")
    ax.add_patch(core_box)
    ax.text(3.0, 2.7, "Core", fontsize=9, ha="center")

    if regions == 1:
        _node(ax, (1.3, 3.3), 1.5, 1.2, "Core Fuel\nNode 1", edge="#1f77b4")
        _node(ax, (1.3, 4.8), 1.5, 1.2, "Core Fuel\nNode 2", edge="#1f77b4")
        _node(ax, (3.1, 4.05), 1.5, 1.2, "Core Graphite\nNode", edge="#1f77b4")
    else:
        # 9 region layout (3x3) with graphite block to the right
        x0, y0 = 1.2, 3.2
        dx, dy = 1.1, 0.85
        idx = 1
        for r in range(3):
            for c in range(3):
                _node(ax, (x0 + c * dx, y0 + r * dy), 1.0, 0.75, f"Fuel\nR{idx}", edge="#1f77b4")
                idx += 1
        _node(ax, (3.8, 4.05), 1.0, 1.2, "Graphite", edge="#1f77b4")

    # DHRS and cold leg
    _node(ax, (6.0, 6.0), 1.7, 0.9, "DHRS", edge="#17becf")
    _node(ax, (6.0, 3.0), 1.7, 0.9, "Cold Leg", edge="#17becf")

    # Primary heat exchanger block
    phx_box = Rectangle((9.0, 2.0), 5.5, 5.5, linewidth=1.5, edgecolor="#333333", facecolor="none", linestyle="--")
    ax.add_patch(phx_box)
    ax.text(11.75, 1.6, "Primary Heat Exchanger", fontsize=9, ha="center")

    # PHX primary (fuel) nodes
    for i in range(4):
        _node(ax, (9.3, 6.0 - i * 1.1), 1.7, 0.8, f"PHX Fuel\nNode {i+1}", edge="#17becf")

    # PHX tube nodes
    _node(ax, (11.3, 5.2), 1.7, 0.8, "PHX Tube\nNode 1", edge="#333333", lw=1.2)
    _node(ax, (11.3, 3.6), 1.7, 0.8, "PHX Tube\nNode 2", edge="#333333", lw=1.2)

    # PHX secondary (coolant) nodes
    for i in range(4):
        _node(ax, (13.5, 6.0 - i * 1.1), 1.7, 0.8, f"PHX Coolant\nNode {4-i}", edge="#d62728", lw=1.6)

    # UHX / radiator
    uhx_box = Rectangle((15.2, 3.4), 2.2, 2.3, linewidth=1.5, edgecolor="#333333", facecolor="none", linestyle="--")
    ax.add_patch(uhx_box)
    ax.text(16.3, 3.1, "UHX", fontsize=9, ha="center")
    _node(ax, (15.35, 4.7), 1.8, 0.7, "UHX Coolant\nNode", edge="#d62728", lw=1.6)
    _node(ax, (15.35, 3.7), 1.8, 0.7, "UHX Air\nNode", edge="#ff7f0e", lw=1.6)

    # Fuel flow (blue)
    ax.plot([5.0, 6.0], [4.5, 6.45], color="#1f77b4", lw=2.2)
    ax.plot([7.7, 9.3], [6.45, 6.4], color="#1f77b4", lw=2.2)
    ax.plot([11.0, 5.0], [2.2, 2.2], color="#1f77b4", lw=2.2)
    ax.plot([5.0, 5.0], [2.2, 3.6], color="#1f77b4", lw=2.2)
    ax.add_patch(plt.Polygon([[8.1, 6.3], [8.6, 6.45], [8.1, 6.6]], closed=True, fill=False, edgecolor="#1f77b4", lw=2))
    ax.add_patch(plt.Polygon([[8.6, 2.2], [9.1, 2.35], [8.6, 2.5]], closed=True, fill=False, edgecolor="#1f77b4", lw=2))

    # Coolant flow (red)
    ax.plot([14.4, 16.0], [5.1, 5.1], color="#d62728", lw=2.2)
    ax.plot([16.0, 16.0], [5.1, 4.7], color="#d62728", lw=2.2)
    ax.plot([16.0, 16.0], [4.1, 3.7], color="#d62728", lw=2.2)
    ax.plot([16.0, 14.4], [3.7, 3.7], color="#d62728", lw=2.2)
    ax.add_patch(plt.Polygon([[14.6, 5.1], [15.0, 5.25], [14.6, 5.4]], closed=True, fill=False, edgecolor="#d62728", lw=2))
    ax.add_patch(plt.Polygon([[15.0, 3.7], [15.4, 3.85], [15.0, 4.0]], closed=True, fill=False, edgecolor="#d62728", lw=2))

    # Heat removal arrow
    ax.arrow(6.85, 7.1, 0, 1.2, width=0.02, head_width=0.25, head_length=0.2, color="#d62728")

    # Interconnect arrows between PHX tube and coolant nodes
    ax.plot([11.0, 13.5], [5.6, 5.6], color="#333333", lw=1.2)
    ax.plot([11.0, 13.5], [4.0, 4.0], color="#333333", lw=1.2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, format="svg")
    plt.close(fig)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    draw_block(1, OUT_DIR / "msrr_schematic_block_1r.svg")
    draw_block(9, OUT_DIR / "msrr_schematic_block_9r.svg")
    draw_loop(1, OUT_DIR / "msrr_schematic_loop_1r.svg")
    draw_loop(9, OUT_DIR / "msrr_schematic_loop_9r.svg")
    draw_nodal(1, OUT_DIR / "msrr_schematic_nodal_1r.svg")
    draw_nodal(9, OUT_DIR / "msrr_schematic_nodal_9r.svg")
    print("Wrote schematics to", OUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
