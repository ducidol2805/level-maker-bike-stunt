#!/usr/bin/env python3
"""Read, validate, summarize, and visualize Bike Stunt level JSON.

This is intentionally a read-only inspection tool: it never rewrites the
source level.  It can optionally render a PNG, SVG, or PDF preview.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import tkinter as tk
from collections import OrderedDict
from tkinter import ttk
from tkinter import font as tkfont
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from obstacle_library import ROOT, GROUPS, as_level, build_variant, ground, linear_polygon, load_catalog, sample_curve, vertex
from spring_object import spring_parameters, spring_trajectory
from terrain_export import surface_count
from world_scale import scale_world_data

try:
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon
    from matplotlib import patheffects
    from matplotlib.text import Text
    from matplotlib.path import Path as RenderPath
    from matplotlib.transforms import Affine2D
    from matplotlib.backends.backend_agg import RendererAgg
    from matplotlib.colors import to_rgba
    from PIL import Image, ImageTk
except ImportError as exc:  # pragma: no cover - user-facing dependency check
    raise SystemExit("Missing dependency. Run: pip install -r requirements.txt") from exc


Point = tuple[float, float]
EPSILON = 1e-9
KNOWN_OBJECT_TYPES = {"explosive_ramp", "explosive_barrel", "speed_boost", "coin", "SpringObject"}

# Dark neutral chrome leaves gameplay colors as the visual language: terrain,
# ramps, hazards and collectible/stunt cues remain recognisable at a glance.
UI_BG = "#0b1220"
UI_PANEL = "#111827"
UI_SURFACE = "#172033"
UI_BORDER = "#334155"
UI_TEXT = "#e5e7eb"
UI_MUTED = "#94a3b8"
UI_ACCENT = "#38bdf8"
UI_SELECTED = "#1e3a5f"
PLOT_BG = "#111827"


@dataclass(frozen=True)
class Issue:
    severity: str
    message: str


def point(value: Any, context: str) -> Point:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object with x and y")
    try:
        return float(value["x"]), float(value["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{context} needs numeric x and y") from exc


def points(shape: dict[str, Any], context: str, minimum: int = 2) -> list[Point]:
    raw = shape.get("points")
    if not isinstance(raw, list) or len(raw) < minimum:
        raise ValueError(f"{context}.points needs at least {minimum} points")
    checked = [point(item, f"{context}.points[{index}]") for index, item in enumerate(raw)]
    if any("tangentIn" in item or "tangentOut" in item for item in raw):
        return sample_curve(shape)
    return checked


def polyline_length(line: Iterable[Point]) -> float:
    items = list(line)
    return sum(math.dist(a, b) for a, b in zip(items, items[1:]))


def shape_id(shape: dict[str, Any], index: int, group: str) -> str:
    return str(shape.get("id", f"{group}_{index + 1:03d}"))


def required_group(data: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = data.get(name, [])
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def endpoint_distance(a: Point, b: Point) -> float:
    return math.dist(a, b)


def validate(data: dict[str, Any], tolerance: float) -> list[Issue]:
    issues: list[Issue] = []
    for name in ("Start", "End", "Top", "Bottom"):
        try:
            point(data[name], name)
        except KeyError:
            issues.append(Issue("error", f"Missing required point: {name}"))
        except ValueError as exc:
            issues.append(Issue("error", str(exc)))

    for group, minimum in (("MainPlatform", 4), ("RampPlatform", 2), ("FreePlatform", 2), ("deadzone", 4)):
        try:
            shapes = required_group(data, group)
        except ValueError as exc:
            issues.append(Issue("error", str(exc)))
            continue
        for index, shape in enumerate(shapes):
            label = f"{group}[{index}]"
            if not isinstance(shape, dict):
                issues.append(Issue("error", f"{label} must be an object"))
                continue
            try:
                line = points(shape, label, minimum)
                if polyline_length(line) <= EPSILON:
                    issues.append(Issue("error", f"{label} has zero length"))
                if group in {"MainPlatform", "deadzone"} and not shape.get("closed", True):
                    expected = "terrain cross-section" if group == "MainPlatform" else "kill volume"
                    issues.append(Issue("error", f"{label} must be closed; {expected} needs an enclosed shape"))
                if group == "deadzone" and not shape.get("closed", True):
                    issues.append(Issue("warning", f"{label} should be closed so it can kill the player"))
                if group == "RampPlatform" and shape.get("closed", False):
                    issues.append(Issue("warning", f"{label} is closed; floating ramps are normally open"))
                if group == "FreePlatform" and shape.get("closed") and len(line) < 3:
                    issues.append(Issue("error", f"{label} needs at least 3 points when closed"))
            except ValueError as exc:
                issues.append(Issue("error", str(exc)))

    for group in ("CheckPoint", "InteractableObject"):
        try:
            entries = required_group(data, group)
        except ValueError as exc:
            issues.append(Issue("error", str(exc)))
            continue
        for index, entry in enumerate(entries):
            label = f"{group}[{index}]"
            if not isinstance(entry, dict):
                issues.append(Issue("error", f"{label} must be an object"))
                continue
            if group == "CheckPoint":
                try:
                    point(entry.get("transform"), f"{label}.transform")
                except ValueError as exc:
                    issues.append(Issue("error", str(exc)))
            else:
                kind = entry.get("type")
                if kind not in KNOWN_OBJECT_TYPES:
                    issues.append(Issue("warning", f"{label} has unknown type {kind!r}"))
                if kind == "SpringObject":
                    try:
                        spring_parameters(entry)
                    except ValueError as exc:
                        issues.append(Issue("error", f"{label}: {exc}"))
                if kind == "explosive_barrel" and isinstance(entry.get('properties'), dict) and 'previewTrajectory' in entry['properties']:
                    try:
                        points(entry['properties']['previewTrajectory'], label+'.previewTrajectory')
                    except (ValueError, TypeError, AttributeError) as exc:
                        issues.append(Issue('error', f'{label}: invalid barrel preview trajectory: {exc}'))
                if kind == "explosive_ramp":
                    try:
                        points(entry, label)
                    except ValueError as exc:
                        issues.append(Issue("error", str(exc)))
                else:
                    try:
                        point(entry.get("transform"), f"{label}.transform")
                    except ValueError as exc:
                        issues.append(Issue("error", str(exc)))
    return issues


def overview(data: dict[str, Any], tolerance: float) -> str:
    main = required_group(data, "MainPlatform")
    ramps = required_group(data, "RampPlatform")
    free_platforms = required_group(data, "FreePlatform")
    deadzones = required_group(data, "deadzone")
    main_perimeter = sum(polyline_length(points(s, "MainPlatform")) for s in main)
    ramp_length = sum(polyline_length(points(s, "RampPlatform")) for s in ramps)
    start, end, top, bottom = (point(data[name], name) for name in ("Start", "End", "Top", "Bottom"))
    return "\n".join((
        f"Map: {data.get('map', {}).get('name', data.get('map', {}).get('id', 'unnamed'))}",
        f"Route span: {math.dist(start, end):.2f} units (Start -> End)",
        f"Elevation reference: Bottom y={bottom[1]:.2f}, Top y={top[1]:.2f}, range={top[1] - bottom[1]:.2f}",
        f"MainPlatform: {len(main)} closed terrain shapes, {main_perimeter:.2f} units of outline",
        f"RampPlatform: {len(ramps)} shapes, {ramp_length:.2f} units traced",
        f"FreePlatform: {len(free_platforms)} freely placed shapes",
        f"Deadzones: {len(deadzones)} | Checkpoints: {len(required_group(data, 'CheckPoint'))} | Interactables: {len(required_group(data, 'InteractableObject'))}",
    ))


def bounds(data: dict[str, Any]) -> tuple[float, float, float, float]:
    found: list[Point] = []
    for name in ("Start", "End", "Top", "Bottom"):
        if name in data:
            found.append(point(data[name], name))
    for group in ("MainPlatform", "RampPlatform", "FreePlatform", "deadzone"):
        for index, shape in enumerate(required_group(data, group)):
            found.extend(points(shape, f"{group}[{index}]", 4 if group == "deadzone" else 2))
    for group in ("CheckPoint", "InteractableObject"):
        for index, entry in enumerate(required_group(data, group)):
            if entry.get("type") == "explosive_ramp":
                found.extend(points(entry, f"{group}[{index}]"))
            if entry.get("type") == "SpringObject":
                found.extend(spring_trajectory(entry))
            if entry.get('type') == 'explosive_barrel' and 'previewTrajectory' in entry.get('properties', {}):
                found.extend(points(entry['properties']['previewTrajectory'], 'barrel.previewTrajectory'))
            if entry.get("type") != "explosive_ramp" and isinstance(entry.get("transform"), dict):
                found.append(point(entry["transform"], f"{group}[{index}].transform"))
    if not found:
        return 0, 1, 0, 1
    xs, ys = zip(*found)
    pad = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * 0.06
    return min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad


def draw_path(ax: Any, line: list[Point], **kwargs: Any) -> None:
    xs, ys = zip(*line)
    ax.plot(xs, ys, **kwargs)


TANGENT_COLORS = {'tangentIn': '#0369a1', 'tangentOut': '#be185d'}
MODE_TAGS = {'linear': 'L', 'broken': 'B', 'continuous': 'C'}


def spline_controls(data: dict[str, Any]) -> Iterable[tuple[Point, str, list[tuple[Point, str]]]]:
    """Expose authored knots and local handles in world coordinates."""
    for group in ('MainPlatform', 'RampPlatform', 'FreePlatform', 'deadzone', 'InteractableObject'):
        for shape in data.get(group, []):
            for knot in shape.get('points', []):
                x, y = knot['x'], knot['y']
                handles = []
                for key, color in TANGENT_COLORS.items():
                    tangent = knot.get(key, {})
                    dx, dy = tangent.get('x', 0), tangent.get('y', 0)
                    if math.hypot(dx, dy) > EPSILON:
                        handles.append(((x+dx, y+dy), color))
                yield (x, y), MODE_TAGS.get(knot.get('tangentMode', 'linear'), '?'), handles


def build_figure(data: dict[str, Any], tolerance: float, outline_only: bool, show_legend: bool = True) -> Figure:
    figure = Figure(figsize=(16, 8), facecolor=UI_BG)
    ax = figure.add_subplot(111)
    ax.set_facecolor(PLOT_BG)
    ax.set_title(data.get("map", {}).get("name", "Bike Stunt Level"), color=UI_TEXT)
    ax.set_xlabel("World X (units)", color=UI_TEXT)
    ax.set_ylabel("World Y (units)", color=UI_TEXT)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, alpha=0.55, linewidth=0.6, color=UI_BORDER)
    ax.tick_params(colors=UI_MUTED)
    for spine in ax.spines.values():
        spine.set_color(UI_BORDER)

    for index, zone in enumerate(required_group(data, "deadzone")):
        line = points(zone, f"deadzone[{index}]", 4)
        ax.add_patch(Polygon(line, closed=zone.get("closed", True), facecolor="#ef4444", edgecolor="#991b1b", alpha=0.28, label="Deadzone" if index == 0 else None))

    for index, shape in enumerate(required_group(data, "MainPlatform")):
        line = points(shape, f"MainPlatform[{index}]", 3)
        ax.add_patch(Polygon(line, closed=True, facecolor="none" if outline_only else "#4ade80", edgecolor="#14532d", linewidth=2.8, alpha=0.82, label="MainPlatform terrain" if index == 0 else None))

    for index, shape in enumerate(required_group(data, "RampPlatform")):
        draw_path(ax, points(shape, f"RampPlatform[{index}]"), color="#d97706", linewidth=18, linestyle="-", solid_capstyle="round", label="RampPlatform" if index == 0 else None)

    for index, shape in enumerate(required_group(data, "FreePlatform")):
        line = points(shape, f"FreePlatform[{index}]", 2)
        if shape.get('closed'):
            ax.add_patch(Polygon(line, closed=True, facecolor='none' if outline_only else '#a78bfa',
                                 edgecolor='#7c3aed', linewidth=2.5, alpha=.75,
                                 label='Free Platform' if index == 0 else None))
        else:
            draw_path(ax, line, color='#a78bfa', linewidth=5,
                      label='Free Platform' if index == 0 else None)

    marker_groups: dict[str, list[Point]] = {}
    for index, item in enumerate(required_group(data, "InteractableObject")):
        kind = item.get("type")
        if kind == "explosive_ramp":
            draw_path(ax, points(item, f"InteractableObject[{index}]"), color="#dc2626", linewidth=20, linestyle="-", label="Explosive ramp")
            continue
        marker_groups.setdefault(kind, []).append(point(item["transform"], f"InteractableObject[{index}].transform"))
        if kind == 'explosive_barrel' and 'previewTrajectory' in item.get('properties', {}):
            draw_path(ax, points(item['properties']['previewTrajectory'], 'barrel.previewTrajectory'),
                      color='#dc2626', linewidth=1.5, linestyle=':', label='Barrel arc (estimate)')
        if kind == "SpringObject":
            arc = spring_trajectory(item)
            draw_path(ax, arc, color="#db2777", linewidth=1.5, linestyle=":", label="Spring arc (estimate)")
            target = item["properties"]["targetPosition"]
            ax.scatter(target["x"], target["y"], marker="x", color="#db2777", s=65, zorder=7, label="Spring target")
            ax.annotate("", xy=arc[-1], xytext=arc[-3], arrowprops={"arrowstyle": "->", "color": "#db2777"})
    for kind, positions in marker_groups.items():
        xs, ys = zip(*positions)
        style = {"explosive_barrel": ("X", "#dc2626"), "speed_boost": (">", "#2563eb"), "coin": ("o", "#eab308"), "SpringObject": ("^", "#db2777")}.get(kind, ("$?$", "#6b7280"))
        ax.scatter(xs, ys, s=90, marker=style[0], color=style[1], edgecolor="#111827", linewidth=0.8, zorder=7, label=kind)

    checkpoints = [point(item["transform"], f"CheckPoint[{index}].transform")
                   for index, item in enumerate(required_group(data, "CheckPoint"))]
    if checkpoints:
        xs, ys = zip(*checkpoints)
        ax.scatter(xs, ys, s=130, marker="P", color="#7c3aed", edgecolor="#111827", linewidth=0.8, zorder=8, label="Checkpoint")

    marker_specs = (("Start", "#16a34a", "o"), ("End", "#dc2626", "s"), ("Top", "#0ea5e9", "^"), ("Bottom", "#64748b", "v"))
    for name, color, symbol in marker_specs:
        x, y = point(data[name], name)
        ax.scatter(x, y, s=110, marker=symbol, color=color, edgecolor="#111827", linewidth=0.8, zorder=9, label=name)
        ax.annotate(name, (x, y), xytext=(0, 10), textcoords="offset points", fontsize=9,
                    ha='center', va='bottom', weight="bold", color=UI_TEXT)

    x0, x1, y0, y1 = bounds(data)
    for text in ax.texts:
        text.set_path_effects([patheffects.withStroke(linewidth=2, foreground='black')])
    for (x, y), tag, controls in spline_controls(data):
        for (hx, hy), color in controls:
            ax.plot([x, hx], [y, hy], color=color, linewidth=2,
                    linestyle='-', marker='s', markevery=[1], markersize=3, zorder=10)
            x0, x1, y0, y1 = min(x0, hx), max(x1, hx), min(y0, hy), max(y1, hy)
        ax.plot(x, y, marker='o', color='white', markeredgecolor='white',
                markersize=18, markeredgewidth=0, zorder=11)
        ax.annotate(tag, (x, y), xytext=(0, 0), textcoords='offset points',
                    ha='center', va='center', fontsize=8, color='black', zorder=12)
    if show_legend:
        for key, color in TANGENT_COLORS.items():
            ax.plot([], [], color=color, linewidth=2, linestyle='-', label=key)
        ax.plot([], [], linestyle='none', label='L: linear / B: broken / C: continuous')
    ax.set(xlim=(x0, x1), ylim=(y0, y1))
    if show_legend:
        handles, labels = ax.get_legend_handles_labels()
        unique = dict(zip(labels, handles))
        figure.legend(
            unique.values(), unique.keys(),
            loc="lower center",
            bbox_to_anchor=(0.5, 0.012),
            ncol=min(5, len(unique)),
            framealpha=0.96,
            fontsize=9,
            facecolor=UI_SURFACE,
            edgecolor=UI_BORDER,
            labelcolor=UI_TEXT,
        )
        figure.subplots_adjust(left=0.06, right=0.985, top=0.93, bottom=0.17)
    else:
        figure.subplots_adjust(left=0.06, right=0.985, top=0.93, bottom=0.09)
    for text in figure.findobj(Text):
        text.set_fontweight('bold')
    return figure


def visualize(data: dict[str, Any], tolerance: float, outline_only: bool, output: Path | None) -> None:
    import matplotlib.pyplot as plt

    figure = build_figure(data, tolerance, outline_only)
    if output:
        figure.savefig(output, dpi=180, bbox_inches="tight")
        print(f"Preview written: {output}")
    else:
        figure.show()
        plt.show()
    plt.close(figure)


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("Level root must be a JSON object")
    return value


def discover_levels(directory: Path) -> list[Path]:
    """Find level JSON files, excluding the format schema itself."""
    if not directory.is_dir():
        return []
    return sorted(
        (
            path for path in directory.rglob("*.json")
            if not path.name.endswith(".schema.json")
            and path.name != "level.schema.json"
            and not path.name.endswith("_manifest.json")
        ),
        key=lambda path: str(path).lower(),
    )
