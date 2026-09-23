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


@dataclass(frozen=True)
class VectorPath:
    """Canvas-oriented world coordinates: X right, Y down; never mutated."""

    source_id: str
    coords: tuple[float, ...]
    closed: bool
    fill: str
    outline: str
    width: float
    smooth: bool = True
    dash: tuple[int, ...] = ()
    arrow: str = 'none'


@dataclass(frozen=True)
class VectorMarker:
    position: Point
    kind: str
    color: str
    label: str = ''


@dataclass(frozen=True)
class VectorLevel:
    paths: tuple[VectorPath, ...]
    markers: tuple[VectorMarker, ...]
    extent: tuple[float, float, float, float]  # left, top, right, bottom
    legend: tuple[tuple[str, str, str], ...]


LEGEND_STYLES = {
    'Deadzone': ('#ef4444', '■'), 'MainPlatform terrain': ('#4ade80', '■'),
    'RampPlatform': ('#d97706', '━'), 'Explosive ramp': ('#dc2626', '━'),
    'Spring arc (estimate)': ('#db2777', '┄'), 'Spring target': ('#db2777', '×'),
    'Barrel arc (estimate)': ('#dc2626', '┄'), 'explosive_barrel': ('#dc2626', '✕'),
    'speed_boost': ('#60a5fa', '▶'), 'coin': ('#eab308', '●'),
    'SpringObject': ('#db2777', '▲'), 'Checkpoint': ('#a78bfa', '✚'),
    'Start': ('#16a34a', '●'), 'End': ('#dc2626', '■'),
    'Top': ('#0ea5e9', '▲'), 'Bottom': ('#94a3b8', '▼'),
}
LEGEND_STYLES['Free Platform'] = ('#a78bfa', '■')
LEGEND_STYLES.update({
    'Pre-explosion route': ('#f59e0b', '-'),
    'Revealed route': ('#22d3ee', '-'),
})


def bezier_coordinates(shape: dict[str, Any], *, closed: bool) -> tuple[float, ...]:
    """Map SpriteShape handles to Tk's raw cubic knots/control points.

    Open paths contain 3*N+1 points. Closed polygons contain 3*N points:
    their final two controls return to the first knot implicitly. Do not use
    Tk's smooth=True, which would reinterpret the points as quadratic splines.
    """
    knots = shape['points']
    result = [float(knots[0]['x']), -float(knots[0]['y'])]
    count = len(knots) if closed else len(knots)-1
    for i in range(count):
        a, b = knots[i], knots[(i+1) % len(knots)]
        outgoing = a.get('tangentOut', {}) if a.get('tangentMode') != 'linear' else {}
        incoming = b.get('tangentIn', {}) if b.get('tangentMode') != 'linear' else {}
        result.extend((a['x']+outgoing.get('x', 0), -(a['y']+outgoing.get('y', 0)),
                       b['x']+incoming.get('x', 0), -(b['y']+incoming.get('y', 0))))
        if not closed or i < count-1:
            result.extend((b['x'], -b['y']))
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{shape.get('id', 'Shape')}: non-finite Bezier coordinates")
    return tuple(result)


def compile_vector_level(data: dict[str, Any], outline_only: bool = False,
                         *, show_controls: bool = True) -> VectorLevel:
    """Prepare native paths and overlays once; no Matplotlib or raster work."""
    paths: list[VectorPath] = []
    markers: list[VectorMarker] = []
    labels: dict[str, None] = {}
    palette = data.get('map', {}).get('environment', {}).get('palette', {})
    legend_styles = dict(LEGEND_STYLES)
    if palette:
        for name, key in (('MainPlatform terrain','terrainFill'),('Deadzone','hazardOutline'),
                          ('RampPlatform','platformOutline')):
            color, symbol = legend_styles[name]
            legend_styles[name] = (palette.get(key,color),symbol)

    def legend(name: str) -> None:
        labels[name] = None

    def shape_path(shape: dict[str, Any], closed: bool, name: str,
                   fill: str, outline: str, width: float, dash: tuple[int, ...] = ()) -> None:
        paths.append(VectorPath(str(shape.get('id', name)), bezier_coordinates(shape, closed=closed),
                                closed, fill, outline, width, dash=dash))
        legend(name)

    def marker(value: dict[str, Any], kind: str, name: str, label: str = '') -> None:
        x, y = point(value, name)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(f'{name}: non-finite marker position')
        markers.append(VectorMarker((x, -y), kind, LEGEND_STYLES.get(name, (UI_MUTED, '●'))[0], label))
        legend(name)

    # Native Tk polygons have no alpha channel. These fills are preblended
    # against the same dark background, with deadzones behind the ground.
    for shape in required_group(data, 'deadzone'):
        shape_path(shape, True, 'Deadzone', palette.get('hazardFill','#4f2430'),
                   palette.get('hazardOutline','#7f2836'), 1)
    for shape in required_group(data, 'MainPlatform'):
        shape_path(shape, True, 'MainPlatform terrain',
                   '' if outline_only else palette.get('terrainFill','#43b878'),
                   palette.get('terrainOutline','#14532d'), 2)
    for shape in required_group(data, 'RampPlatform'):
        phase = shape.get('metadata', {}).get('routePhase')
        if phase == 'before_explosion':
            shape_path(shape, shape.get('closed', False), 'Pre-explosion route', '', '#f59e0b', 15)
        elif phase == 'after_explosion':
            shape_path(shape, shape.get('closed', False), 'Revealed route', '', '#22d3ee', 15)
        else:
            shape_path(shape, shape.get('closed', False), 'RampPlatform', '',
                       palette.get('platformOutline','#d97706'), 15)
    for shape in required_group(data, 'FreePlatform'):
        closed = shape.get('closed', False)
        shape_path(shape, closed, 'Free Platform',
                   '' if outline_only or not closed else '#3b2a57', '#a78bfa', 3 if closed else 5)
    for item in required_group(data, 'InteractableObject'):
        kind = item['type']
        if kind == 'explosive_ramp':
            shape_path(item, item.get('closed', False), 'Explosive ramp', '', '#dc2626', 15)
            continue
        marker(item['transform'], kind, kind)
        if kind == 'SpringObject':
            x, y, tx, ty, duration, gravity = spring_parameters(item)
            vx, vy = (tx-x)/duration, (ty-y)/duration+.5*gravity*duration
            # A ballistic quadratic is represented exactly by a cubic Bezier.
            coords = (x, -y, x+vx*duration/3, -(y+vy*duration/3),
                      tx-vx*duration/3, -(ty-(vy-gravity*duration)*duration/3), tx, -ty)
            paths.append(VectorPath(item.get('id', 'spring')+'_arc', coords, False,
                                    '', '#db2777', 1, dash=(2, 4), arrow='last'))
            legend('Spring arc (estimate)')
            marker(item['properties']['targetPosition'], 'target', 'Spring target')
        elif kind == 'explosive_barrel' and 'previewTrajectory' in item.get('properties', {}):
            trajectory = item['properties']['previewTrajectory']
            if any('tangentIn' in p or 'tangentOut' in p for p in trajectory['points']):
                coords = bezier_coordinates(trajectory, closed=False)
                smooth = True
            else:
                coords = tuple(v for p in trajectory['points'] for v in (p['x'], -p['y']))
                smooth = False
            paths.append(VectorPath(item.get('id', 'barrel')+'_arc', coords, False,
                                    '', '#dc2626', 1, smooth=smooth, dash=(2, 4)))
            legend('Barrel arc (estimate)')
    for item in required_group(data, 'CheckPoint'):
        marker(item['transform'], 'checkpoint', 'Checkpoint')
    for name, kind in (('Start', 'start'), ('End', 'end'), ('Top', 'up'), ('Bottom', 'down')):
        marker(data[name], kind, name, name)
    left, right, bottom, top = bounds(data)
    if show_controls:
        for index, ((x, y), tag, controls) in enumerate(spline_controls(data)):
            for (hx, hy), color in controls:
                paths.append(VectorPath(f'control_{index}', (x, -y, hx, -hy), False,
                                        '', color, 2, smooth=False))
                markers.append(VectorMarker((hx, -hy), 'handle', color))
                left, right = min(left, hx), max(right, hx)
                bottom, top = min(bottom, hy), max(top, hy)
            markers.append(VectorMarker((x, -y), 'knot', '#ffffff', tag))
        for key, color in TANGENT_COLORS.items():
            legend_styles[key] = (color, '-')
            legend(key)
        legend('L: linear / B: broken / C: continuous')
    return VectorLevel(tuple(paths), tuple(markers), (left, -top, right, -bottom),
                       tuple((name, *legend_styles.get(name, (UI_TEXT, '●'))) for name in labels))


class PreviewCanvas(tk.Canvas):
    """Supersampled geometry with fixed-pixel text, icons and line widths."""

    PAN_GAIN = 1.6
    ZOOM_STEP = 1.45
    FRAME_MS = 16
    AA_SCALE = 2
    TEXT_FONT = ('Segoe UI', -11, 'bold')
    LABEL_OFFSET = (0, -10)
    STROKE_OFFSETS = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                      (1, 0), (-1, 1), (0, 1), (1, 1))
    # Pixel offsets around the object anchor. Never scale these dimensions.
    SYMBOLS = {
        'up': (0, -6, 6, 6, -6, 6),
        'down': (-6, -6, 6, -6, 0, 6),
        'right': (-6, -6, 6, 0, -6, 6),
        'square': (-6, -6, 6, -6, 6, 6, -6, 6),
        'cross': (-2, -6, 2, -6, 2, -2, 6, -2, 6, 2, 2, 2,
                  2, 6, -2, 6, -2, 2, -6, 2, -6, -2, -2, -2),
        'x': (-6, -4, -4, -6, 0, -2, 4, -6, 6, -4, 2, 0,
              6, 4, 4, 6, 0, 2, -4, 6, -6, 4, -2, 0),
        'diamond': (0, -6, 6, 0, 0, 6, -6, 0),
    }

    def __init__(self, master: Any) -> None:
        super().__init__(master, background=PLOT_BG, highlightthickness=0, takefocus=True)
        self.scene: VectorLevel | None = None
        self.scale = 1.0
        self.offset = [0.0, 0.0]
        self._drawn_scale = 1.0
        self._drawn_offset = [0.0, 0.0]
        self._size = (1, 1)
        self._fit_mode = True
        self._force_project = False
        self._pan_position: tuple[int, int] | None = None
        self._draw_after: str | None = None
        self._path_items: list[tuple[int, VectorPath]] = []
        self._marker_items: list[tuple[int, int | None, VectorMarker]] = []
        self._label_strokes: dict[int, list[int]] = {}
        self.controls_visible = False
        self.aa_enabled = False
        self._aa_paths: list[tuple[VectorPath, RenderPath]] = []
        self._aa_photo: ImageTk.PhotoImage | None = None
        self._aa_item: int | None = None
        self._aa_key: tuple[Any, ...] | None = None
        self._aa_bounds: list[tuple[float, float, float, float]] = []
        self.bind('<Configure>', self._resize)
        self.bind('<MouseWheel>', self._wheel)
        self.bind('<Button-4>', self._wheel)
        self.bind('<Button-5>', self._wheel)
        self.bind('<ButtonPress-1>', self._start_pan)
        self.bind('<B1-Motion>', self._drag)
        self.bind('<ButtonRelease-1>', self._end_pan)
        self.bind('<Double-Button-1>', self.fit)
        self.bind('<FocusOut>', self._end_pan)

    def set_scene(self, scene: VectorLevel) -> None:
        self._cancel_pending()
        self._pan_position = None
        self.configure(cursor='')
        self.delete('scene')
        self._path_items.clear()
        self._marker_items.clear()
        self._label_strokes.clear()
        self.scene = scene
        self._aa_item = None
        self._aa_photo = None
        self._aa_key = None
        self._aa_paths = [(path, self._render_path(path)) for path in scene.paths]
        self._aa_bounds = [(min(p.coords[::2]), min(p.coords[1::2]),
                            max(p.coords[::2]), max(p.coords[1::2])) for p in scene.paths]
        for path in scene.paths:
            options: dict[str, Any] = dict(width=path.width, tags=('scene', 'geometry'),
                                          smooth='raw' if path.smooth else False, splinesteps=64)
            if path.source_id.startswith('control_'):
                options['tags'] += ('point_controls',)
            if path.dash:
                options['dash'] = path.dash
            if path.closed:
                item = self.create_polygon(path.coords, fill=path.fill, outline=path.outline, **options)
            else:
                item = self.create_line(path.coords, fill=path.outline, arrow=path.arrow,
                                        arrowshape=(8, 10, 4), **options)
            self._path_items.append((item, path))
        for spec in scene.markers:
            options = dict(fill=spec.color, outline=PLOT_BG, width=1, tags=('scene', 'overlay', 'marker'))
            control_tags = ('point_controls',) if spec.kind in ('knot', 'handle') else ()
            options['tags'] += control_tags
            if spec.kind == 'knot':
                options['outline'] = '#ffffff'
                options['width'] = 0
            coords = self._symbol_coords(spec.kind, 0, 0)
            if spec.kind in ('coin', 'start', 'knot', 'handle'):
                item = self.create_oval(coords, **options)
            else:
                item = self.create_polygon(coords, **options)
            label = None
            if spec.label:
                anchor = 'center' if spec.kind == 'knot' else 's'
                strokes = [self.create_text(0, 0, text=spec.label, font=self.TEXT_FONT,
                                           anchor=anchor, fill='black',
                                           tags=('scene', 'overlay', 'text_stroke')+control_tags)
                           for _ in self.STROKE_OFFSETS] if spec.kind != 'knot' else []
                label = self.create_text(0, 0, text=spec.label, font=self.TEXT_FONT,
                                         anchor=anchor,
                                         fill='black' if spec.kind == 'knot' else UI_TEXT,
                                         tags=('scene', 'overlay', 'label')+control_tags)
                self._label_strokes[label] = strokes
            self._marker_items.append((item, label, spec))
        self.tag_raise('text_stroke')
        self.tag_raise('label')
        self.itemconfigure('point_controls', state='normal' if self.controls_visible else 'hidden')
        self._size = (max(1, self.winfo_width()), max(1, self.winfo_height()))
        self.fit()
        # Project before Tk's first paint; only subsequent navigation needs
        # coalescing, and a newly selected map must not flash at world scale.
        self._draw_view()

    def clear_scene(self) -> None:
        self._cancel_pending()
        self._pan_position = None
        self.configure(cursor='')
        self.delete('scene')
        self._path_items.clear()
        self._marker_items.clear()
        self._label_strokes.clear()
        self._aa_paths.clear()
        self._aa_bounds.clear()
        self._aa_key = None
        self._aa_photo = None
        self._aa_item = None
        self.scene = None

    def toggle_controls(self, _event: Any = None) -> str:
        """Toggle point annotations without rebuilding the scene or camera."""
        self.controls_visible = not self.controls_visible
        self.itemconfigure('point_controls', state='normal' if self.controls_visible else 'hidden')
        self._draw_view()
        return 'break'

    def toggle_aa(self, _event: Any = None) -> str:
        """Switch between native Tk and antialiased rendering."""
        self.aa_enabled = not self.aa_enabled
        self._aa_key = None
        self._draw_view()
        return 'break'

    @staticmethod
    def _render_path(path: VectorPath) -> RenderPath:
        vertices = list(zip(path.coords[::2], path.coords[1::2]))
        # Tk's closed raw splines omit the final endpoint of the last cubic.
        if path.closed and path.smooth:
            vertices.append(vertices[0])
        codes = [RenderPath.MOVETO] + [RenderPath.CURVE4 if path.smooth else RenderPath.LINETO]*(len(vertices)-1)
        if path.closed:
            vertices.append(vertices[0])
            codes.append(RenderPath.CLOSEPOLY)
        return RenderPath(vertices, codes)

    def _render_antialiased(self) -> None:
        """Render at 2x resolution with Agg AA, then filter with bilinear."""
        # Native Agg AA while dragging; full supersampling on release.
        factor = 1 if self._pan_position is not None else self.AA_SCALE
        width, height = self._size
        key = (width, height, self.scale, *self.offset, self.controls_visible, factor)
        if key == self._aa_key:
            return
        renderer = RendererAgg(width*factor, height*factor, 72)
        screen = Affine2D().scale(factor, -factor).translate(0, height*factor)
        world = Affine2D().scale(self.scale).translate(*self.offset) + screen

        def draw(path: RenderPath, transform: Affine2D, color: str,
                 line_width: float, fill: str = '', dash: tuple[int, ...] = ()) -> None:
            gc = renderer.new_gc()
            gc.set_antialiased(True)
            gc.set_foreground(color)
            gc.set_linewidth(line_width*factor)
            gc.set_capstyle('round')
            gc.set_joinstyle('round')
            if dash:
                gc.set_dashes(0, [v*factor for v in dash])
            renderer.draw_path(gc, path, transform, to_rgba(fill) if fill else None)
            gc.restore()

        background = RenderPath([(0, 0), (width, 0), (width, height), (0, height), (0, 0)], closed=True)
        draw(background, screen, PLOT_BG, 0, PLOT_BG)
        for (spec, path), (left, top, right, bottom) in zip(self._aa_paths, self._aa_bounds):
            if spec.source_id.startswith('control_') and not self.controls_visible:
                continue
            margin = spec.width+12
            if (right*self.scale+self.offset[0] < -margin or
                    left*self.scale+self.offset[0] > width+margin or
                    bottom*self.scale+self.offset[1] < -margin or
                    top*self.scale+self.offset[1] > height+margin):
                continue
            draw(path, world, spec.outline, spec.width, spec.fill, spec.dash)
            if spec.arrow == 'last':
                # Fixed-pixel arrow at the final curve derivative.
                end = path.vertices[-1]
                previous = next((v for v in reversed(path.vertices[:-1]) if any(v != end)), end)
                dx, dy = end-previous
                length = math.hypot(dx, dy)
                if length:
                    ux, uy = dx/length, dy/length
                    x, y = end*self.scale+self.offset
                    arrow = RenderPath([(x, y), (x-10*ux-4*uy, y-10*uy+4*ux),
                                        (x-10*ux+4*uy, y-10*uy-4*ux), (x, y)],
                                       [RenderPath.MOVETO, RenderPath.LINETO, RenderPath.LINETO, RenderPath.CLOSEPOLY])
                    draw(arrow, screen, spec.outline, 0, spec.outline)

        for spec in self.scene.markers:
            if spec.kind in ('knot', 'handle') and not self.controls_visible:
                continue
            x, y = (spec.position[k]*self.scale+self.offset[k] for k in (0, 1))
            if not (-12 <= x <= width+12 and -12 <= y <= height+12):
                continue
            coords = self._symbol_coords(spec.kind, x, y)
            if spec.kind in ('coin', 'start', 'knot', 'handle'):
                radius = (coords[2]-coords[0])/2
                path = RenderPath.unit_circle()
                transform = Affine2D().scale(radius).translate(x, y) + screen
            else:
                vertices = list(zip(coords[::2], coords[1::2]))
                path = RenderPath(vertices+[vertices[0]], closed=True)
                transform = screen
            draw(path, transform, PLOT_BG, 0 if spec.kind == 'knot' else 1, spec.color)

        pixels = Image.frombuffer('RGBA', (width*factor, height*factor),
                                  renderer.buffer_rgba(), 'raw', 'RGBA', 0, 1)
        if factor > 1:
            pixels = pixels.resize((width, height), Image.Resampling.BILINEAR)
        self._aa_photo = ImageTk.PhotoImage(pixels, master=self)
        if self._aa_item is None:
            self._aa_item = self.create_image(0, 0, anchor='nw', image=self._aa_photo, tags=('scene', 'aa'))
        else:
            self.coords(self._aa_item, 0, 0)
            self.itemconfigure(self._aa_item, image=self._aa_photo, state='normal')
        self.itemconfigure('geometry', state='hidden')
        self.itemconfigure('marker', state='hidden')
        self.tag_lower('aa')
        self._aa_key = key

    @classmethod
    def _symbol_coords(cls, kind: str, x: float, y: float) -> tuple[float, ...]:
        if kind in ('coin', 'start', 'knot', 'handle'):
            radius = {'coin': 4, 'start': 6, 'knot': 7.5, 'handle': 2}[kind]
            return (x-radius, y-radius, x+radius, y+radius)
        symbol = {'SpringObject': 'up', 'speed_boost': 'right', 'end': 'square',
                  'checkpoint': 'cross', 'target': 'x', 'explosive_barrel': 'x'}.get(kind, kind)
        offsets = cls.SYMBOLS.get(symbol, cls.SYMBOLS['diamond'])
        return tuple(value+(x if i%2==0 else y) for i, value in enumerate(offsets))

    def _fit_scale(self) -> float:
        if self.scene is None:
            return 1.0
        left, top, right, bottom = self.scene.extent
        w, h = self._size
        return min(max(1, w-24)/max(EPSILON, right-left),
                   max(1, h-24)/max(EPSILON, bottom-top))

    def fit(self, _event: Any = None) -> str:
        self._pan_position = None
        self.configure(cursor='')
        self._fit_mode = True
        if self.scene:
            self.scale = self._fit_scale()
            left, top, right, bottom = self.scene.extent
            self.offset = [self._size[0]/2-(left+right)*self.scale/2,
                           self._size[1]/2-(top+bottom)*self.scale/2]
            self._force_project = True
            self._schedule_draw()
        return 'break'

    def _resize(self, event: Any) -> None:
        previous = self._size
        self._size = (max(1, event.width), max(1, event.height))
        if self._fit_mode:
            self.fit()
        else:
            self.offset[0] += (self._size[0]-previous[0])/2
            self.offset[1] += (self._size[1]-previous[1])/2
            self._force_project = True
            self._schedule_draw()

    def _wheel(self, event: Any) -> str:
        number = getattr(event, 'num', None)
        steps = 1 if number == 4 else -1 if number == 5 else getattr(event, 'delta', 0)/120
        if self.scene is not None and steps and self._pan_position is None:
            factor = self.ZOOM_STEP**max(-8, min(8, steps))
            fit_scale = self._fit_scale()
            scale = min(fit_scale*40, max(fit_scale*.15, self.scale*factor))
            factor = scale/self.scale
            self.offset = [event.x+(self.offset[0]-event.x)*factor,
                           event.y+(self.offset[1]-event.y)*factor]
            self.scale = scale
            self._fit_mode = False
            self._schedule_draw()
        return 'break'

    def _start_pan(self, event: Any) -> str:
        self.focus_set()
        if self.scene is not None:
            self._draw_view()
            self._pan_position = (event.x, event.y)
            self._fit_mode = False
            self.configure(cursor='fleur')
        return 'break'

    def _drag(self, event: Any) -> str:
        if self._pan_position is not None:
            self.offset[0] += (event.x-self._pan_position[0])*self.PAN_GAIN
            self.offset[1] += (event.y-self._pan_position[1])*self.PAN_GAIN
            self._pan_position = (event.x, event.y)
            self._schedule_draw()
        return 'break'

    def _end_pan(self, _event: Any = None) -> str:
        if self._pan_position is not None:
            self._pan_position = None
            self.configure(cursor='')
            self._draw_view()
        return 'break'

    def _schedule_draw(self) -> None:
        if self.scene and self._draw_after is None:
            self._draw_after = self.after(self.FRAME_MS, self._draw_view)

    def _project_markers(self) -> None:
        for item, label, spec in self._marker_items:
            x, y = (spec.position[k]*self.scale+self.offset[k] for k in (0, 1))
            self.coords(item, *self._symbol_coords(spec.kind, x, y))
            if label is not None:
                dx, dy = (0, 0) if spec.kind == 'knot' else self.LABEL_OFFSET
                self.coords(label, x+dx, y+dy)
                for stroke, (sx, sy) in zip(self._label_strokes[label], self.STROKE_OFFSETS):
                    self.coords(stroke, x+dx+sx, y+dy+sy)

    def _draw_view(self) -> None:
        self._cancel_pending()
        if self.scene is None:
            return
        if self._force_project:
            for item, path in self._path_items:
                self.coords(item, *(value*self.scale+self.offset[i%2] for i,value in enumerate(path.coords)))
            self._project_markers()
        elif self.scale != self._drawn_scale:
            factor = self.scale/self._drawn_scale
            # Scale only the terrain/ramp/trajectory layer. Icon dimensions,
            # stroke widths, text font and text offsets remain in screen pixels.
            super().scale('geometry', 0, 0, factor, factor)
            self.move('geometry', self.offset[0]-factor*self._drawn_offset[0],
                      self.offset[1]-factor*self._drawn_offset[1])
            self._project_markers()
        else:
            self.move('scene', self.offset[0]-self._drawn_offset[0],
                      self.offset[1]-self._drawn_offset[1])
        self._drawn_scale = self.scale
        self._drawn_offset = self.offset.copy()
        self._force_project = False
        if self.aa_enabled:
            self._render_antialiased()
        else:
            self.itemconfigure('aa', state='hidden')
            self.itemconfigure('geometry', state='normal')
            self.itemconfigure('marker', state='normal')
            self.itemconfigure('point_controls', state='normal' if self.controls_visible else 'hidden')

    def _cancel_pending(self) -> None:
        if self._draw_after is not None:
            self.after_cancel(self._draw_after)
            self._draw_after = None

    def destroy(self) -> None:
        self._cancel_pending()
        self.scene = None
        self._path_items.clear()
        self._marker_items.clear()
        self._label_strokes.clear()
        self._aa_paths.clear()
        self._aa_bounds.clear()
        self._aa_photo = None
        super().destroy()


class PreviewLegend(ttk.Frame):
    """Fixed, responsive footer shared by the map and obstacle browsers."""

    def __init__(self, master: Any) -> None:
        super().__init__(master, padding=(12, 4, 12, 4))
        self.labels: list[tk.Label] = []
        self.columns = 0
        self.bind('<Configure>', self._layout)

    def set_entries(self, entries: tuple[tuple[str, str, str], ...]) -> None:
        for label in self.labels:
            label.destroy()
        self.labels = [tk.Label(self, text=f'{symbol}  {name}', fg=color,
                                bg=UI_BG, font=('Segoe UI', 9, 'bold'))
                       for name, color, symbol in entries]
        self._layout(force=True)

    def _layout(self, _event: Any = None, *, force: bool = False) -> None:
        columns = max(1, self.winfo_width() // 190)
        if columns == self.columns and not force:
            return
        for column in range(self.columns):
            self.columnconfigure(column, weight=0)
        self.columns = columns
        for i, label in enumerate(self.labels):
            label.grid(row=i//columns, column=i%columns, sticky='w', padx=(0, 12), pady=2)
        for column in range(columns):
            self.columnconfigure(column, weight=1)


@dataclass
class ObstacleCard:
    variant_id: str
    scene: VectorLevel
    background: int
    edit_button: int
    paths: list[tuple[int, VectorPath]]
    markers: list[tuple[int, VectorMarker]]


@dataclass
class ObstacleGroup:
    title: str
    title_item: int
    rule_item: int
    cards: list[ObstacleCard]


def compile_obstacle_card(family: dict[str, Any], variant: dict[str, Any],
                          outline_only: bool = False) -> VectorLevel:
    """Actual obstacle geometry only, without map reference points or guides."""
    module = build_variant(family, variant, scale=family.get('_worldScale', 1))
    data = as_level([module], variant['id'], variant['id'], preview=True)
    errors = [issue.message for issue in validate(data, .05) if issue.severity == 'error']
    if errors:
        raise ValueError(f"{variant['id']}: {'; '.join(errors[:2])}")
    scene = compile_vector_level(data, outline_only, show_controls=False)
    shape_ids = {shape['id'] for group in GROUPS
                 for shape in data[group] if 'points' in shape}
    paths = tuple(path for path in scene.paths if path.source_id in shape_ids)
    markers = tuple(marker for marker in scene.markers
                    if not marker.label and marker.kind not in ('target', 'checkpoint'))
    # Fit the shapes themselves, not the invisible Start/End/Top/Bottom or arcs.
    positions = [point for path in paths for point in zip(path.coords[::2], path.coords[1::2])]
    positions.extend(marker.position for marker in markers)
    xs, ys = zip(*positions) if positions else ((0, 1), (0, 1))
    return VectorLevel(paths, markers, (min(xs), min(ys), max(xs), max(ys)), ())


class ObstacleEditorWindow:
    """Edit a variant's local-space Bezier points and persist shape overrides."""

    def __init__(self, library: 'ObstacleLibraryWindow', family: dict[str, Any],
                 variant: dict[str, Any]) -> None:
        self.library = library
        self.family = family
        self.variant = copy.deepcopy(variant)
        self.module = build_variant(family, self.variant, scale=family.get('_worldScale', 1))
        self.shapes = [(group, shape) for group in GROUPS for shape in self.module[group]
                       if 'points' in shape]
        self.shape_index = 0
        self.shape_mode = tk.StringVar(master=library.window, value=self.shapes[0][0])
        self.selected: int | None = None
        self.drag: tuple[str, int] | None = None
        self.pan_anchor: tuple[int, int, float, float] | None = None
        self.dirty = False
        self.changed_shapes: set[tuple[str, str]] = set()
        self.added_shapes = {(group, shape['id']) for group, shapes in
                             self.variant.get('addedShapes', {}).items() for shape in shapes}
        self.converted_shapes = set(self.variant.get('shapeTypeOverrides', {}))
        self.bounds = (0.0, 0.0, 1.0, 1.0)
        self.zoom = 1.0
        self.view_offset = (0.0, 0.0)
        self.window = tk.Toplevel(library.window)
        self.window.title(f"Edit obstacle - {variant['id']}")
        self.window.geometry('1100x760')
        self.window.minsize(780, 520)
        self.window.configure(bg=UI_BG)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        toolbar = ttk_frame(self.window, padding=10)
        toolbar.grid(row=0, column=0, sticky='ew')
        top_line = ttk_frame(toolbar)
        top_line.pack(fill='x')
        mode_line = ttk_frame(toolbar)
        mode_line.pack(fill='x', pady=(6, 0))
        tk.Label(top_line, text=variant['id'], bg=UI_BG, fg=UI_TEXT,
                 font=('Segoe UI', 12, 'bold')).pack(side='left', padx=(0, 16))
        labels = [f'{group} / {shape["id"]}' for group, shape in self.shapes]
        self.shape_choice = ttk.Combobox(top_line, values=labels, state='readonly', width=34)
        self.shape_choice.current(0)
        self.shape_choice.pack(side='left', padx=(0, 12))
        self.shape_choice.bind('<<ComboboxSelected>>', self.change_shape)
        for group, label in (('MainPlatform', 'Z Ground'), ('RampPlatform', 'X Platform'),
                             ('FreePlatform', 'V Free Platform'), ('deadzone', 'C Deadzone')):
            ttk.Radiobutton(mode_line, text=label, variable=self.shape_mode, value=group,
                            command=lambda value=group: self.convert_selected_shape(value)).pack(side='left', padx=3)
        tk.Label(top_line, text='Grid', bg=UI_BG, fg=UI_TEXT).pack(side='left', padx=(12, 4))
        self.grid_size = tk.StringVar(value='1')
        grid_entry = ttk.Entry(top_line, textvariable=self.grid_size, width=7)
        grid_entry.pack(side='left')
        grid_entry.bind('<Return>', self.redraw)
        grid_entry.bind('<FocusOut>', self.redraw)
        self._button(top_line, 'Fit', self.fit).pack(side='right')

        body = ttk_frame(self.window, padding=(10, 0, 10, 10))
        body.grid(row=1, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=PLOT_BG, highlightthickness=1,
                                highlightbackground=UI_BORDER, cursor='crosshair', takefocus=True)
        self.canvas.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        self.canvas.bind('<Configure>', self.redraw)
        self.canvas.bind('<ButtonPress-1>', self.mouse_down)
        self.canvas.bind('<B1-Motion>', self.mouse_move)
        self.canvas.bind('<ButtonRelease-1>', self.mouse_up)
        self.canvas.bind('<ButtonPress-2>', self.pan_start)
        self.canvas.bind('<B2-Motion>', self.pan_move)
        self.canvas.bind('<ButtonRelease-2>', self.pan_end)
        self.canvas.bind('<MouseWheel>', self.mouse_wheel)
        self.canvas.bind('<Button-4>', self.mouse_wheel)
        self.canvas.bind('<Button-5>', self.mouse_wheel)
        for key, mode in (('1', 'linear'), ('2', 'broken'), ('3', 'continuous')):
            self.window.bind(f'<KeyPress-{key}>',
                             lambda event, value=mode: self.mode_hotkey(event, value))
        for key, dx, dy in (('a', -1, 0), ('s', 0, -1), ('d', 1, 0), ('w', 0, 1)):
            for letter in (key, key.upper()):
                self.window.bind(f'<KeyPress-{letter}>',
                                 lambda event, x=dx, y=dy: self.nudge_hotkey(event, x, y))
        for key, group in (('z', 'MainPlatform'), ('x', 'RampPlatform'),
                           ('v', 'FreePlatform'), ('c', 'deadzone')):
            for letter in (key, key.upper()):
                self.window.bind(f'<KeyPress-{letter}>',
                                 lambda event, value=group: self.shape_mode_hotkey(event, value))
        panel = ttk_frame(body, padding=12)
        panel.grid(row=0, column=1, sticky='ns')
        tk.Label(panel, text='POINT', bg=UI_BG, fg=UI_TEXT,
                 font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(0, 8))
        self.fields: dict[str, tk.StringVar] = {}
        for key, label in (('x', 'X'), ('y', 'Y'), ('in_x', 'Tangent In X'),
                           ('in_y', 'Tangent In Y'), ('out_x', 'Tangent Out X'),
                           ('out_y', 'Tangent Out Y')):
            tk.Label(panel, text=label, bg=UI_BG, fg=UI_MUTED).pack(anchor='w')
            variable = tk.StringVar()
            self.fields[key] = variable
            entry = ttk.Entry(panel, textvariable=variable, width=18)
            entry.pack(fill='x', pady=(0, 6))
            entry.bind('<Return>', self.apply_fields)
        tk.Label(panel, text='Tangent Mode', bg=UI_BG, fg=UI_MUTED).pack(anchor='w')
        self.mode = tk.StringVar(value='broken')
        mode_box = ttk.Combobox(panel, textvariable=self.mode, state='readonly',
                                values=('linear', 'broken', 'continuous'), width=16)
        mode_box.pack(fill='x', pady=(0, 10))
        mode_box.bind('<<ComboboxSelected>>', self.change_mode)
        self._button(panel, 'Apply values', self.apply_fields).pack(fill='x', pady=3)
        self._button(panel, 'Add point after', self.add_point).pack(fill='x', pady=3)
        self._button(panel, 'Remove point', self.remove_point).pack(fill='x', pady=3)
        self._button(panel, 'Add shape', self.add_shape).pack(fill='x', pady=(12, 3))
        tk.Label(panel, text='Wheel: zoom  |  Middle drag: pan\nCtrl+drag: snap  |  WASD: move point 1\n1/2/3: tangent  |  Z/X/C/V: convert shape',
                 bg=UI_BG, fg=UI_MUTED, justify='left').pack(anchor='w', pady=(16, 0))
        footer = ttk_frame(self.window, padding=(10, 0, 10, 10))
        footer.grid(row=2, column=0, sticky='ew')
        self.status = tk.StringVar(value='Select a point to edit.')
        tk.Label(footer, textvariable=self.status, bg=UI_BG, fg=UI_MUTED).pack(side='left')
        self._button(footer, 'Save', self.save).pack(side='right', padx=(8, 0))
        self._button(footer, 'Cancel', self.close).pack(side='right')
        self.fit()

    @staticmethod
    def _button(parent: Any, label: str, command: Any) -> tk.Button:
        return ObstacleLibraryWindow._button(parent, label, command)

    def current(self) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        group, shape = self.shapes[self.shape_index]
        return group, shape, shape['points']

    @staticmethod
    def is_surface_shape(group: str, shape: dict[str, Any]) -> bool:
        return group == 'MainPlatform' or (group == 'RampPlatform' and shape.get('closed') and
                                            'drivingSurfaceCount' in shape.get('metadata', {}))

    @staticmethod
    def sync_terrain_closure(shape: dict[str, Any]) -> None:
        if shape.get('metadata', {}).get('freeBottomCorners'):
            return
        count = shape['metadata']['drivingSurfaceCount']
        surface = shape['points'][:count]
        ceiling = min(point['y'] for point in surface)-.01
        shape['points'][-2].update(x=surface[-1]['x'],
                                    y=min(shape['points'][-2]['y'], ceiling))
        shape['points'][-1].update(x=surface[0]['x'],
                                    y=min(shape['points'][-1]['y'], ceiling))

    def change_shape(self, _event: Any = None) -> None:
        self.shape_index = self.shape_choice.current()
        self.shape_mode.set(self.shapes[self.shape_index][0])
        self.selected = None
        self.redraw()

    def convert_selected_shape(self, target: str) -> None:
        group, shape, _points = self.current()
        self.shape_mode.set(group)
        if target == group:
            return
        if target not in ('MainPlatform', 'RampPlatform', 'FreePlatform', 'deadzone'):
            raise ValueError(f'Unknown shape type: {target}')
        converted = copy.deepcopy(shape)
        if not converted.get('closed') and target not in ('RampPlatform', 'FreePlatform'):
            converted = ground(shape['id'], converted['points'])
        if target == 'MainPlatform':
            converted['closed'] = True
            converted.setdefault('metadata', {})['drivingSurfaceCount'] = len(converted['points'])-2
            try:
                surface_count(converted)
            except ValueError:
                converted['metadata']['freeBottomCorners'] = True
                try:
                    surface_count(converted)
                except ValueError as exc:
                    self.status.set(str(exc))
                    return
        elif target == 'deadzone':
            if len(converted['points']) < 4:
                self.status.set('Deadzone needs at least 4 points')
                return
            converted['closed'] = True
        was_added = (group, shape['id']) in self.added_shapes
        self.module[group].remove(shape)
        shape.clear()
        shape.update(converted)
        self.module[target].append(shape)
        self.shapes[self.shape_index] = (target, shape)
        if was_added:
            self.added_shapes.remove((group, shape['id']))
            self.added_shapes.add((target, shape['id']))
        else:
            self.converted_shapes.add(shape['id'])
        self.changed_shapes.add((target, shape['id']))
        self.dirty = True
        self.shape_mode.set(target)
        self.shape_choice.configure(values=[f'{name} / {item["id"]}' for name, item in self.shapes])
        self.shape_choice.current(self.shape_index)
        self.select(min(self.selected, len(shape['points'])-1) if self.selected is not None else None)

    def shape_mode_hotkey(self, event: Any, group: str) -> str | None:
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return None
        self.convert_selected_shape(group)
        return 'break'

    def fit(self) -> None:
        xs, ys = [], []
        for _group, shape in self.shapes:
            for point in shape['points']:
                xs.append(point['x'])
                ys.append(point['y'])
                for key in ('tangentIn', 'tangentOut'):
                    tangent = point.get(key, {})
                    xs.append(point['x'] + tangent.get('x', 0))
                    ys.append(point['y'] + tangent.get('y', 0))
        self.bounds = (min(xs)-2, min(ys)-2, max(xs)+2, max(ys)+2)
        self.zoom = 1.0
        self.view_offset = (0.0, 0.0)
        self.redraw()

    def transform(self) -> tuple[float, float, float]:
        left, bottom, right, top = self.bounds
        width, height = max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height())
        scale = min((width-48)/max(1, right-left), (height-48)/max(1, top-bottom))*self.zoom
        return (scale, (width-(left+right)*scale)/2+self.view_offset[0],
                (height+(bottom+top)*scale)/2+self.view_offset[1])

    def screen(self, x: float, y: float) -> tuple[float, float]:
        scale, ox, oy = self.transform()
        return x*scale+ox, -y*scale+oy

    def world(self, x: float, y: float) -> tuple[float, float]:
        scale, ox, oy = self.transform()
        return (x-ox)/scale, (oy-y)/scale

    def snap_value(self, value: float) -> float:
        size = float(self.grid_size.get())
        if not math.isfinite(size) or size <= 0:
            raise ValueError('Grid size must be positive')
        return round(round(value/size)*size, 4)

    @staticmethod
    def faded_color(color: str, alpha: float = .2) -> str:
        return '#' + ''.join(f'{round(int(PLOT_BG[i:i+2], 16)*(1-alpha) + int(color[i:i+2], 16)*alpha):02x}'
                             for i in (1, 3, 5))

    def mouse_wheel(self, event: Any) -> str:
        number = getattr(event, 'num', None)
        direction = 1 if number == 4 else -1 if number == 5 else (1 if event.delta > 0 else -1)
        before = self.world(event.x, event.y)
        self.zoom = max(0.2, min(40.0, self.zoom*(1.2 if direction > 0 else 1/1.2)))
        after = self.screen(*before)
        self.view_offset = (self.view_offset[0]+event.x-after[0],
                            self.view_offset[1]+event.y-after[1])
        self.redraw()
        return 'break'

    def pan_start(self, event: Any) -> str:
        self.canvas.focus_set()
        self.pan_anchor = (event.x, event.y, *self.view_offset)
        return 'break'

    def pan_move(self, event: Any) -> str:
        if self.pan_anchor is not None:
            x, y, offset_x, offset_y = self.pan_anchor
            self.view_offset = (offset_x+event.x-x, offset_y+event.y-y)
            self.redraw()
        return 'break'

    def pan_end(self, _event: Any) -> str:
        self.pan_anchor = None
        return 'break'

    def redraw(self, _event: Any = None) -> None:
        canvas = self.canvas
        canvas.delete('all')
        group, shape, points = self.current()
        try:
            major = float(self.grid_size.get())
            if math.isfinite(major) and major > 0:
                width, height = canvas.winfo_width(), canvas.winfo_height()
                left = self.world(0, 0)[0]
                right = self.world(width, 0)[0]
                bottom = self.world(0, height)[1]
                top = self.world(0, 0)[1]
                spacing = major/2 if self.transform()[0]*major/2 >= 3 else major
                if (right-left)/spacing <= 250 and (top-bottom)/spacing <= 250:
                    for n in range(math.ceil(left/spacing), math.floor(right/spacing)+1):
                        x = self.screen(n*spacing, 0)[0]
                        minor = spacing < major and n % 2 != 0
                        canvas.create_line(x, 0, x, height,
                                           fill='#192335' if minor else '#27364d')
                    for n in range(math.ceil(bottom/spacing), math.floor(top/spacing)+1):
                        y = self.screen(0, n*spacing)[1]
                        minor = spacing < major and n % 2 != 0
                        canvas.create_line(0, y, width, y,
                                           fill='#192335' if minor else '#27364d')
        except ValueError:
            pass
        for shape_index, (_other_group, other_shape) in enumerate(self.shapes):
            if shape_index == self.shape_index:
                continue
            route = sample_curve(other_shape, 20)
            coords = [value for x, y in route for value in self.screen(x, y)]
            canvas.create_line(*coords, fill=self.faded_color(UI_ACCENT), width=2,
                               smooth=False, tags=(f'shape:{shape_index}',))
        route = sample_curve(shape, 20)
        coords = [value for x, y in route for value in self.screen(x, y)]
        canvas.create_line(*coords, fill=UI_ACCENT, width=3, smooth=False,
                           tags=(f'shape:{self.shape_index}',))
        for index, point in enumerate(points):
            x, y = self.screen(point['x'], point['y'])
            for key, color in (('tangentIn', '#38bdf8'), ('tangentOut', '#f472b6')):
                if point['tangentMode'] == 'linear':
                    continue
                tangent = point[key]
                hx, hy = self.screen(point['x']+tangent['x'], point['y']+tangent['y'])
                canvas.create_line(x, y, hx, hy, fill=color, width=1)
                canvas.create_oval(hx-5, hy-5, hx+5, hy+5, fill=color, outline='',
                                  tags=(f'handle:{key}:{index}',))
            fill = '#facc15' if index == self.selected else '#f8fafc'
            canvas.create_oval(x-6, y-6, x+6, y+6, fill=fill, outline=PLOT_BG,
                              tags=(f'point:{index}',))
            canvas.create_text(x+10, y-10, text=str(index+1), fill=UI_TEXT,
                               anchor='w', font=('Segoe UI', 9))

    def select(self, index: int | None) -> None:
        self.selected = index
        if index is not None:
            point = self.current()[2][index]
            for key, value in (('x', point['x']), ('y', point['y']),
                               ('in_x', point['tangentIn']['x']), ('in_y', point['tangentIn']['y']),
                               ('out_x', point['tangentOut']['x']), ('out_y', point['tangentOut']['y'])):
                self.fields[key].set(str(round(value, 4)))
            self.mode.set(point['tangentMode'])
            group, shape, _points = self.current()
            if self.is_surface_shape(group, shape) and index >= shape['metadata']['drivingSurfaceCount']:
                self.status.set(f'Bottom point {index+1} selected')
            else:
                self.status.set(f'Point {index+1} selected')
        self.redraw()

    def set_tangent_mode(self, mode: str) -> None:
        if self.selected is None:
            self.status.set('Select a point before changing tangent mode')
            return
        group, shape, points = self.current()
        point = points[self.selected]
        if point['tangentMode'] == mode:
            return
        point['tangentMode'] = mode
        if self.is_surface_shape(group, shape) and self.selected >= shape['metadata']['drivingSurfaceCount']:
            shape['metadata']['freeBottomCorners'] = True
        if mode in ('broken', 'continuous'):
            point['tangentIn'] = {'x': -1, 'y': 0}
            point['tangentOut'] = {'x': 1, 'y': 0}
        self.dirty = True
        self.changed_shapes.add((group, shape['id']))
        self.select(self.selected)

    def change_mode(self, _event: Any = None) -> None:
        self.set_tangent_mode(self.mode.get())

    def mode_hotkey(self, event: Any, mode: str) -> str | None:
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return None
        self.set_tangent_mode(mode)
        return 'break'

    def nudge_hotkey(self, event: Any, dx: int, dy: int) -> str | None:
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return None
        self.nudge_point(dx, dy)
        return 'break'

    def nudge_point(self, dx: int, dy: int) -> None:
        if self.selected is None:
            self.status.set('Select a point before moving it')
            return
        group, shape, points = self.current()
        point = points[self.selected]
        x, y = round(point['x']+dx, 4), round(point['y']+dy, 4)
        count = shape.get('metadata', {}).get('drivingSurfaceCount', len(points))
        if self.is_surface_shape(group, shape) and self.selected >= count:
            shape['metadata']['freeBottomCorners'] = True
        elif self.is_surface_shape(group, shape) and (
            (self.selected > 0 and x < points[self.selected-1]['x']) or
            (self.selected+1 < count and x > points[self.selected+1]['x'])
        ):
            self.status.set('Surface points must run left to right')
            return
        point['x'], point['y'] = x, y
        if self.is_surface_shape(group, shape):
            self.sync_terrain_closure(shape)
        self.dirty = True
        self.changed_shapes.add((group, shape['id']))
        self.select(self.selected)

    def mouse_down(self, event: Any) -> None:
        self.canvas.focus_set()
        self.drag = None
        for item in reversed(self.canvas.find_overlapping(event.x-6, event.y-6, event.x+6, event.y+6)):
            tags = self.canvas.gettags(item)
            tag = next((value for value in tags if value.startswith(('point:', 'handle:'))), None)
            if tag:
                parts = tag.split(':')
                index = int(parts[-1])
                self.select(index)
                self.drag = ('point' if parts[0] == 'point' else parts[1], index)
                return
            other = next((value for value in tags if value.startswith('shape:')), None)
            if other:
                shape_index = int(other.split(':')[1])
                if shape_index != self.shape_index:
                    self.shape_choice.current(shape_index)
                    self.change_shape()
                return
        self.select(None)

    def mouse_move(self, event: Any) -> None:
        if self.drag is None:
            return
        kind, index = self.drag
        group, shape, points = self.current()
        point = points[index]
        try:
            x, y = self.world(event.x, event.y)
            if event.state & 0x0004:
                x, y = self.snap_value(x), self.snap_value(y)
            else:
                x, y = round(x, 4), round(y, 4)
        except ValueError as exc:
            self.status.set(str(exc))
            return
        if kind == 'point':
            if self.is_surface_shape(group, shape):
                count = shape['metadata']['drivingSurfaceCount']
                if index >= count:
                    shape['metadata']['freeBottomCorners'] = True
                else:
                    if index > 0:
                        x = max(x, points[index-1]['x'])
                    if index+1 < count:
                        x = min(x, points[index+1]['x'])
            point['x'], point['y'] = x, y
            if self.is_surface_shape(group, shape):
                self.sync_terrain_closure(shape)
        else:
            if self.is_surface_shape(group, shape) and index >= shape['metadata']['drivingSurfaceCount']:
                shape['metadata']['freeBottomCorners'] = True
            tangent = {'x': round(x-point['x'], 4), 'y': round(y-point['y'], 4)}
            point[kind] = tangent
            if point['tangentMode'] == 'continuous':
                opposite = 'tangentOut' if kind == 'tangentIn' else 'tangentIn'
                point[opposite] = {'x': -tangent['x'], 'y': -tangent['y']}
        self.dirty = True
        self.changed_shapes.add((group, self.current()[1]['id']))
        self.select(index)

    def mouse_up(self, _event: Any) -> None:
        self.drag = None

    def apply_fields(self, _event: Any = None) -> None:
        if self.selected is None:
            return
        group, shape, points = self.current()
        try:
            values = {key: float(variable.get()) for key, variable in self.fields.items()}
            if any(not math.isfinite(value) for value in values.values()):
                raise ValueError('Values must be finite')
            x, y = round(values['x'], 4), round(values['y'], 4)
            if self.is_surface_shape(group, shape):
                count = shape['metadata']['drivingSurfaceCount']
                if self.selected >= count:
                    shape['metadata']['freeBottomCorners'] = True
                elif ((self.selected > 0 and x < points[self.selected-1]['x']) or
                      (self.selected+1 < count and x > points[self.selected+1]['x'])):
                    raise ValueError('Surface points must run left to right')
        except ValueError as exc:
            self.status.set(str(exc))
            return
        point = points[self.selected]
        old_in = point['tangentIn']
        old_out = point['tangentOut']
        point['x'], point['y'] = x, y
        if self.is_surface_shape(group, shape):
            self.sync_terrain_closure(shape)
        point['tangentIn'] = {'x': values['in_x'], 'y': values['in_y']}
        point['tangentOut'] = {'x': values['out_x'], 'y': values['out_y']}
        point['tangentMode'] = self.mode.get()
        if self.mode.get() == 'continuous':
            if point['tangentIn'] != old_in and point['tangentOut'] == old_out:
                point['tangentOut'] = {key: -value for key, value in point['tangentIn'].items()}
            else:
                point['tangentIn'] = {key: -value for key, value in point['tangentOut'].items()}
        self.dirty = True
        self.changed_shapes.add((group, self.current()[1]['id']))
        self.select(self.selected)

    def add_point(self) -> None:
        group, shape, points = self.current()
        if self.selected is None:
            self.select(0)
        index = self.selected
        if self.is_surface_shape(group, shape) and index >= shape['metadata']['drivingSurfaceCount']:
            self.status.set('Add points on the driving surface; the bottom has two corners')
            return
        count = shape['metadata']['drivingSurfaceCount'] if self.is_surface_shape(group, shape) else len(points)
        following = points[(index+1) % len(points)] if index+1 < count or (shape.get('closed') and not self.is_surface_shape(group, shape)) else None
        if following is None:
            x, y = points[index]['x']+2, points[index]['y']
        else:
            x, y = (points[index]['x']+following['x'])/2, (points[index]['y']+following['y'])/2
        new = {'x': round(x, 4), 'y': round(y, 4), 'tangentIn': {'x': 0, 'y': 0},
               'tangentOut': {'x': 0, 'y': 0}, 'tangentMode': 'linear', 'corner': False}
        shape['points'].insert(index+1, new)
        if self.is_surface_shape(group, shape):
            shape['metadata']['drivingSurfaceCount'] += 1
            self.sync_terrain_closure(shape)
        self.dirty = True
        self.changed_shapes.add((group, shape['id']))
        self.select(index+1)

    def add_shape(self) -> None:
        known = {shape['id'] for _group, shape in self.shapes}
        number = 1
        while f'custom_shape_{number:02d}' in known:
            number += 1
        group = self.current()[0]
        if group not in ('MainPlatform', 'RampPlatform', 'FreePlatform', 'deadzone'):
            raise ValueError(f'Unknown shape type: {group}')
        if group in ('deadzone', 'FreePlatform'):
            shape = linear_polygon(f'custom_shape_{number:02d}',
                                   [(0, 8), (3, 8), (3, 5), (0, 5)])
        else:
            shape = ground(f'custom_shape_{number:02d}',
                           [vertex(0, 8, mode='linear'), vertex(3, 8, mode='linear')])
        self.module[group].append(shape)
        self.shapes.append((group, shape))
        self.added_shapes.add((group, shape['id']))
        self.changed_shapes.add((group, shape['id']))
        self.dirty = True
        self.shape_choice.configure(values=[f'{group} / {item["id"]}' for group, item in self.shapes])
        self.shape_index = len(self.shapes)-1
        self.shape_choice.current(self.shape_index)
        self.fit()
        self.select(0)

    def remove_point(self) -> None:
        if self.selected is None:
            return
        group, shape, points = self.current()
        if self.is_surface_shape(group, shape) and self.selected >= shape['metadata']['drivingSurfaceCount']:
            self.status.set('The two bottom corners are required')
            return
        minimum = 2 if self.is_surface_shape(group, shape) or not shape.get('closed') else 4 if group == 'deadzone' else 3
        count = shape['metadata']['drivingSurfaceCount'] if self.is_surface_shape(group, shape) else len(points)
        if count <= minimum:
            self.status.set(f'Keep at least {minimum} points')
            return
        shape['points'].pop(self.selected)
        if self.is_surface_shape(group, shape):
            shape['metadata']['drivingSurfaceCount'] -= 1
            self.sync_terrain_closure(shape)
        self.dirty = True
        self.changed_shapes.add((group, shape['id']))
        self.select(min(self.selected, len(shape['points'])-1))

    def proposed_variant(self) -> dict[str, Any]:
        overrides = copy.deepcopy(self.variant.get('geometryOverrides', {}))
        type_overrides = copy.deepcopy(self.variant.get('shapeTypeOverrides', {}))
        inverse_scale = 1/self.family.get('_worldScale', 1)
        for group, shape in self.shapes:
            if shape['id'] in self.converted_shapes:
                type_overrides[shape['id']] = {'group': group,
                                               'shape': scale_world_data(shape, inverse_scale)}
            elif (group, shape['id']) in self.changed_shapes and (group, shape['id']) not in self.added_shapes:
                if self.is_surface_shape(group, shape):
                    overrides.setdefault(group, {})[shape['id']] = {
                        'drivingSurfaceCount': shape['metadata']['drivingSurfaceCount'],
                        'freeBottomCorners': shape['metadata'].get('freeBottomCorners', False),
                        'points': scale_world_data(shape['points'], inverse_scale)}
                else:
                    overrides.setdefault(group, {})[shape['id']] = scale_world_data(
                        shape['points'], inverse_scale)
        added = {group: [scale_world_data(shape, inverse_scale)
                         for name, shape in self.shapes
                         if name == group and (name, shape['id']) in self.added_shapes]
                 for group in ('MainPlatform', 'RampPlatform', 'FreePlatform', 'deadzone')}
        proposed = copy.deepcopy(self.variant)
        if overrides:
            proposed['geometryOverrides'] = overrides
        if any(added.values()):
            proposed['addedShapes'] = {group: shapes for group, shapes in added.items() if shapes}
        if type_overrides:
            proposed['shapeTypeOverrides'] = type_overrides
        return proposed

    def save(self) -> None:
        from tkinter import messagebox
        if not self.dirty:
            self.close()
            return
        try:
            proposed = self.proposed_variant()
            compile_obstacle_card(self.family, proposed, self.library.outline_only)
            catalog = json.loads((ROOT / 'library/obstacle_catalog.json').read_text(encoding='utf-8'))
            group_data = next(item for item in catalog['groups'] if item['type'] == self.family['type'])
            source = ROOT / 'library' / group_data['path']
            family_data = json.loads(source.read_text(encoding='utf-8'))
            target = next(item for item in family_data['variants'] if item['id'] == proposed['id'])
            if proposed.get('geometryOverrides'):
                target['geometryOverrides'] = proposed['geometryOverrides']
            if proposed.get('addedShapes'):
                target['addedShapes'] = proposed['addedShapes']
            if proposed.get('shapeTypeOverrides'):
                target['shapeTypeOverrides'] = proposed['shapeTypeOverrides']
            source.write_text(json.dumps(family_data, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            messagebox.showerror('Cannot save obstacle', str(exc), parent=self.window)
            return
        self.dirty = False
        self.library.reload()
        self.close()

    def close(self) -> None:
        if self.dirty:
            from tkinter import messagebox
            if not messagebox.askyesno('Discard changes?', 'Discard unsaved point changes?', parent=self.window):
                return
        self.window.destroy()


class ObstacleLibraryWindow:
    """All library shapes on one retained Canvas; scrolling never rebuilds them."""

    COLUMNS = 5
    GAP = 12
    GROUP_TITLE_HEIGHT = 28
    CELL_HEIGHT = 150
    CELL_PADDING = 18

    def __init__(self, parent: tk.Misc, *, outline_only: bool = False) -> None:
        self.window = tk.Toplevel(parent)
        self.window.title('Bike Stunt - Obstacle Library')
        self.window.geometry('1480x860')
        self.window.minsize(900, 500)
        self.window.configure(bg=UI_BG)
        self.outline_only = outline_only
        self.cards: list[ObstacleCard] = []
        self.groups: list[ObstacleGroup] = []
        self.editors: dict[str, ObstacleEditorWindow] = {}
        self._layout_after: str | None = None
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        toolbar = ttk_frame(self.window, padding=(12, 8))
        toolbar.grid(row=0, column=0, columnspan=2, sticky='ew')
        toolbar.columnconfigure(0, weight=1)
        tk.Label(toolbar, text='OBSTACLE LIBRARY', bg=UI_BG, fg=UI_TEXT,
                 font=('Segoe UI', 11, 'bold')).grid(row=0, column=0, sticky='w')
        self._button(toolbar, 'Refresh', self.reload).grid(row=0, column=1, sticky='e')
        self.canvas = tk.Canvas(self.window, bg=UI_BG, highlightthickness=0,
                                takefocus=True, yscrollincrement=24)
        self.canvas.grid(row=1, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(self.window, orient='vertical', command=self.canvas.yview)
        scrollbar.grid(row=1, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.bind('<Configure>', self._resize)
        self.canvas.bind('<Button-1>', lambda _event: self.canvas.focus_set())
        # Only this window scrolls; the map preview keeps its own wheel zoom.
        self.window.bind('<MouseWheel>', self._wheel)
        self.window.bind('<Button-4>', self._wheel)
        self.window.bind('<Button-5>', self._wheel)
        self.window.bind('<Home>', lambda _event: self.canvas.yview_moveto(0))
        self.window.bind('<End>', lambda _event: self.canvas.yview_moveto(1))
        self.window.bind('<Prior>', lambda _event: self.canvas.yview_scroll(-1, 'pages'))
        self.window.bind('<Next>', lambda _event: self.canvas.yview_scroll(1, 'pages'))
        self.window.bind('<F5>', self.reload)
        self.window.bind('<Destroy>', self._destroyed, add='+')
        self.reload()

    @staticmethod
    def _button(parent: Any, text: str, command: Any) -> tk.Button:
        return tk.Button(parent, text=text, command=command, padx=10, pady=5,
                         bg=UI_SURFACE, fg=UI_TEXT, activebackground=UI_SELECTED,
                         activeforeground=UI_TEXT, disabledforeground=UI_MUTED,
                         relief='flat', highlightthickness=0)

    def reload(self, _event: Any = None) -> str:
        try:
            families = load_catalog()
            # Build in memory before replacing the current grid. A bad source
            # must not leave a partially loaded or silently incomplete library.
            scenes = [(family['type'], [(variant['id'], compile_obstacle_card(family, variant, self.outline_only))
                                         for variant in family['variants']])
                      for family in families.values()]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            from tkinter import messagebox
            messagebox.showerror('Cannot load obstacle library', str(exc), parent=self.window)
            return 'break'
        self.canvas.delete('all')
        self.cards.clear()
        self.groups.clear()
        for group_index, (type_id, variants) in enumerate(scenes):
            group_tag = f'group:{group_index}'
            title = self.canvas.create_text(0, 0, text=type_id.replace('_', ' ').upper(),
                                            fill=UI_TEXT, font=('Segoe UI', -10, 'bold'),
                                            anchor='sw', tags=(group_tag, 'group-title'))
            rule = self.canvas.create_rectangle(0, 0, 1, 1, fill=UI_ACCENT, outline='',
                                                tags=(group_tag, 'group-rule'))
            group = ObstacleGroup(type_id, title, rule, [])
            for variant_id, scene in variants:
                index = len(self.cards)
                tag = f'card:{index}'
                background = self.canvas.create_rectangle(0, 0, 1, 1, fill=PLOT_BG,
                                                           outline='', tags=(tag, 'card'))
                edit_button = self.canvas.create_text(0, 0, text='⋯', fill=UI_TEXT,
                                                      font=('Segoe UI', 15, 'bold'),
                                                      anchor='center', tags=(tag, 'edit'))
                self.canvas.tag_bind(edit_button, '<Button-1>',
                                     lambda _event, value=variant_id: self.open_editor(value))
                card = ObstacleCard(variant_id, scene, background, edit_button, [], [])
                for path in scene.paths:
                    options: dict[str, Any] = dict(width=path.width, tags=(tag, 'geometry'),
                                                  smooth='raw' if path.smooth else False, splinesteps=64)
                    if path.dash:
                        options['dash'] = path.dash
                    if path.closed:
                        item = self.canvas.create_polygon(path.coords, fill=path.fill,
                                                           outline=path.outline, **options)
                    else:
                        item = self.canvas.create_line(path.coords, fill=path.outline, **options)
                    card.paths.append((item, path))
                for marker in scene.markers:
                    coords = PreviewCanvas._symbol_coords(marker.kind, 0, 0)
                    options = dict(fill=marker.color, outline=PLOT_BG, width=1, tags=(tag, 'marker'))
                    if marker.kind == 'coin':
                        item = self.canvas.create_oval(coords, **options)
                    else:
                        item = self.canvas.create_polygon(coords, **options)
                    card.markers.append((item, marker))
                self.canvas.tag_raise(edit_button)
                self.cards.append(card)
                group.cards.append(card)
            self.groups.append(group)
        self.window.title(f'Bike Stunt - Obstacle Library ({len(self.cards)})')
        self._layout()
        return 'break'

    def _resize(self, _event: Any) -> None:
        if self._layout_after is None:
            self._layout_after = self.window.after(16, self._layout)

    def _layout(self) -> None:
        if self._layout_after is not None:
            self.window.after_cancel(self._layout_after)
            self._layout_after = None
        fraction = self.canvas.yview()[0]
        width = max(1, self.canvas.winfo_width())
        cell_width = max(1, (width-self.GAP*(self.COLUMNS+1))/self.COLUMNS)
        y = self.GAP
        for group in self.groups:
            self.canvas.coords(group.title_item, self.GAP+10, y+self.GROUP_TITLE_HEIGHT-7)
            self.canvas.coords(group.rule_item, self.GAP, y+self.GROUP_TITLE_HEIGHT-3,
                               width-self.GAP, y+self.GROUP_TITLE_HEIGHT-1)
            y += self.GROUP_TITLE_HEIGHT
            for index, card in enumerate(group.cards):
                x = self.GAP+(index % self.COLUMNS)*(cell_width+self.GAP)
                card_y = y+(index // self.COLUMNS)*(self.CELL_HEIGHT+self.GAP)
                self.canvas.coords(card.background, x, card_y, x+cell_width,
                                   card_y+self.CELL_HEIGHT)
                self.canvas.coords(card.edit_button, x+cell_width-15, card_y+15)
                left, top, right, bottom = card.scene.extent
                scale = min(max(1, cell_width-2*self.CELL_PADDING)/max(EPSILON, right-left),
                            (self.CELL_HEIGHT-2*self.CELL_PADDING)/max(EPSILON, bottom-top))
                offset = (x+cell_width/2-(left+right)*scale/2,
                          card_y+self.CELL_HEIGHT/2-(top+bottom)*scale/2)
                for item, path in card.paths:
                    self.canvas.coords(item, *(value*scale+offset[i%2] for i, value in enumerate(path.coords)))
                for item, marker in card.markers:
                    mx, my = marker.position[0]*scale+offset[0], marker.position[1]*scale+offset[1]
                    self.canvas.coords(item, *PreviewCanvas._symbol_coords(marker.kind, mx, my))
            y += math.ceil(len(group.cards)/self.COLUMNS)*(self.CELL_HEIGHT+self.GAP)
        height = max(self.canvas.winfo_height(), y)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self.canvas.yview_moveto(fraction)

    def open_editor(self, variant_id: str) -> None:
        editor = self.editors.get(variant_id)
        if editor is not None and editor.window.winfo_exists():
            editor.window.lift()
            editor.window.focus_set()
            return
        try:
            family = next(family for family in load_catalog().values()
                          if any(item['id'] == variant_id for item in family['variants']))
            variant = next(item for item in family['variants'] if item['id'] == variant_id)
            self.editors[variant_id] = ObstacleEditorWindow(self, family, variant)
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            from tkinter import messagebox
            messagebox.showerror('Cannot open obstacle editor', str(exc), parent=self.window)

    def _wheel(self, event: Any) -> str:
        number = getattr(event, 'num', None)
        delta = 120 if number == 4 else -120 if number == 5 else getattr(event, 'delta', 0)
        if delta:
            steps = int(delta/120) or (1 if delta > 0 else -1)
            self.canvas.yview_scroll(-3*steps, 'units')
        return 'break'

    def _destroyed(self, event: Any) -> None:
        if event.widget is self.window:
            if self._layout_after is not None:
                self.window.after_cancel(self._layout_after)
                self._layout_after = None
            self.cards.clear()
            self.groups.clear()

class LevelBrowserApp:
    """Read-only desktop browser for a directory of level JSON files."""

    COLUMNS = 5
    CACHE_LEVELS = 8

    def __init__(self, root: tk.Tk, levels: list[Path], level_directory: Path,
                 start_path: Path | None, tolerance: float, outline_only: bool) -> None:
        self.root = root
        self.levels = levels
        self.level_directory = level_directory
        self.tolerance = tolerance
        self.outline_only = outline_only
        self.legend_visible = True
        self.current_data: dict[str, Any] | None = None
        self._warning_suffix = ''
        self.selected = 0
        if start_path:
            try:
                self.selected = levels.index(start_path.resolve())
            except ValueError:
                pass
        self.canvas: PreviewCanvas | None = None
        self._level_cache: OrderedDict[Any, Any] = OrderedDict()
        self.obstacle_library: ObstacleLibraryWindow | None = None
        self.level_buttons: list[tk.Button] = []
        self.status = tk.StringVar(value="Select a level | Press H to hide/show the map legend")

        root.title("Bike Stunt Level Visualizer")
        root.minsize(1050, 620)
        root.geometry("1480x860")
        root.configure(background=UI_BG)
        for name in tkfont.names(root):
            tkfont.nametofont(name, root=root).configure(weight='bold')
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=UI_BG)
        style.configure("TScrollbar", background=UI_SURFACE, troughcolor=UI_PANEL,
                        bordercolor=UI_BORDER, arrowcolor=UI_TEXT)
        root.bind("<Key-h>", self.toggle_legend)
        root.bind("<Key-H>", self.toggle_legend)
        root.bind("<Key-1>", self.toggle_point_legend)
        root.bind("<Key-2>", self.toggle_aa)
        root.bind("<Key-f>", lambda event: self.canvas.fit() if self.canvas else None)
        root.bind("<Key-F>", lambda event: self.canvas.fit() if self.canvas else None)
        root.bind("<F5>", self.refresh_levels)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        content = ttk_frame(root, padding=8)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=0)
        content.rowconfigure(0, weight=1)

        self.preview = ttk_frame(content, padding=0)
        self.preview.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.preview.columnconfigure(0, weight=1)
        self.preview.rowconfigure(1, weight=1)
        self.preview_title = tk.Label(self.preview, text='', bg=UI_BG, fg=UI_TEXT,
                                      font=('Segoe UI', 11, 'bold'), pady=6)
        self.preview_title.grid(row=0, column=0, sticky='ew')
        self.canvas = PreviewCanvas(self.preview)
        self.canvas.grid(row=1, column=0, sticky='nsew')

        side = ttk_frame(content, padding=8, relief="groove", borderwidth=1)
        self.right_panel = side
        side.grid(row=0, column=1, sticky="ns")
        actions = ttk_frame(side)
        actions.pack(fill='x', pady=(0, 12))
        ObstacleLibraryWindow._button(actions, 'Obstacle library', self.open_obstacle_library).pack(
            side='left', fill='x', expand=True, padx=(0, 4))
        ObstacleLibraryWindow._button(actions, 'Refresh', self.refresh_levels).pack(
            side='right', fill='x', padx=(4, 0))
        tk.Label(side, text="LEVELS", font=("Segoe UI", 11, "bold"), bg=UI_BG, fg=UI_TEXT).pack(anchor="w")
        self.level_count = tk.StringVar(value=f"{len(levels)} level(s) | 5 columns")
        tk.Label(side, textvariable=self.level_count, bg=UI_BG, fg=UI_MUTED).pack(anchor="w", pady=(0, 8))

        nav = ttk_frame(side)
        nav.pack(fill="x", pady=(0, 8))
        self.previous_button = tk.Button(nav, text="◀ Prev", command=lambda: self.step(-1), width=11,
                                         bg=UI_SURFACE, fg=UI_TEXT, activebackground=UI_SELECTED,
                                         activeforeground=UI_TEXT, disabledforeground=UI_MUTED,
                                         highlightthickness=0, relief="flat")
        self.previous_button.pack(side="left")
        self.next_button = tk.Button(nav, text="Next ▶", command=lambda: self.step(1), width=11,
                                     bg=UI_SURFACE, fg=UI_TEXT, activebackground=UI_SELECTED,
                                     activeforeground=UI_TEXT, disabledforeground=UI_MUTED,
                                     highlightthickness=0, relief="flat")
        self.next_button.pack(side="right")

        list_shell = ttk_frame(side)
        list_shell.pack(fill="both", expand=True)
        self.list_canvas = tk.Canvas(list_shell, width=385, highlightthickness=0, background=UI_PANEL)
        scrollbar = tk.Scrollbar(list_shell, orient="vertical", command=self.list_canvas.yview)
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = ttk_frame(self.list_canvas, padding=6)
        self.grid_window = self.list_canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", self._resize_scroll_region)
        self.list_canvas.bind("<Configure>", self._resize_grid_width)
        # The preview consumes its own wheels. Only descendants of the right
        # panel may scroll this list, including buttons within its grid.
        root.bind("<MouseWheel>", self._mousewheel, add="+")
        root.bind("<Button-4>", self._mousewheel, add="+")
        root.bind("<Button-5>", self._mousewheel, add="+")

        self.legend_frame = PreviewLegend(root)
        self.legend_frame.grid(row=1, column=0, sticky='ew')
        footer = ttk_frame(root, padding=(12, 0, 12, 8))
        footer.grid(row=2, column=0, sticky="ew")
        tk.Label(footer, textvariable=self.status, anchor="w", bg=UI_BG, fg=UI_MUTED).pack(fill="x")

        self.build_level_grid()
        if levels:
            # Let Tk paint the shell first; expensive level rendering no longer
            # blocks the window from appearing during startup.
            self.status.set("Loading level preview…")
            root.after_idle(lambda: self.select(self.selected))
        else:
            self.status.set("No level JSON files found. Pass a map file or a folder with --levels-dir.")

    def _resize_scroll_region(self, _event: Any) -> None:
        self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))

    def _resize_grid_width(self, event: Any) -> None:
        self.list_canvas.itemconfigure(self.grid_window, width=event.width)

    def _mousewheel(self, event: Any) -> str | None:
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        # winfo_containing returns any widget under the pointer, not just a
        # descendant of the canvas on which the query was made.
        while widget is not None and widget is not self.right_panel:
            widget = getattr(widget, "master", None)
        if widget is not self.right_panel:
            return None
        number = getattr(event, "num", None)
        delta = 120 if number == 4 else -120 if number == 5 else getattr(event, "delta", 0)
        if delta:
            units = -int(delta / 120) or (-1 if delta > 0 else 1)
            self.list_canvas.yview_scroll(units, "units")
        return "break"

    def _set_legend(self, entries: tuple[tuple[str, str, str], ...]) -> None:
        self.legend_frame.set_entries(entries)

    def open_obstacle_library(self) -> None:
        if self.obstacle_library is None or not self.obstacle_library.window.winfo_exists():
            self.obstacle_library = ObstacleLibraryWindow(self.root, outline_only=self.outline_only)
        self.obstacle_library.window.deiconify()
        self.obstacle_library.window.lift()
        self.obstacle_library.window.focus_set()

    def _update_status(self) -> None:
        state = 'legend shown' if self.legend_visible else 'legend hidden'
        self.status.set(f'{self.selected+1}/{len(self.levels)}  {self.levels[self.selected].name}'
                        f'{self._warning_suffix} | Wheel: zoom 1.45x | Left drag: pan 1.6x | F / double-click: fit | H: {state}'
                        f' | 1: point legend {"shown" if self.canvas and self.canvas.controls_visible else "hidden"}'
                        f' | 2: AA {"on" if self.canvas and self.canvas.aa_enabled else "off"}')

    def build_level_grid(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.level_buttons.clear()
        for index, level in enumerate(self.levels):
            label = level.stem.replace("_", " ")
            if len(label) > 13:
                label = label[:12] + "…"
            button = tk.Button(
                self.grid_frame,
                text=f"{index + 1}\n{label}",
                command=lambda position=index: self.select(position),
                width=10,
                height=3,
                wraplength=65,
                relief="flat",
                bg=UI_SURFACE,
                fg=UI_TEXT,
                activebackground=UI_SELECTED,
                activeforeground=UI_TEXT,
                highlightthickness=1,
                highlightbackground=UI_BORDER,
                highlightcolor=UI_ACCENT,
            )
            button.grid(row=index // self.COLUMNS, column=index % self.COLUMNS, padx=3, pady=3, sticky="nsew")
            self.level_buttons.append(button)
        for column in range(self.COLUMNS):
            self.grid_frame.columnconfigure(column, weight=1)

    def refresh_levels(self, _event: Any = None) -> str:
        """Rescan the level directory and force the selected file to reload."""
        current_path = self.levels[self.selected] if self.levels else None
        refreshed = discover_levels(self.level_directory)
        self.levels = refreshed
        self._level_cache.clear()
        self.level_count.set(f"{len(refreshed)} level(s) | {self.COLUMNS} columns")
        if current_path in refreshed:
            self.selected = refreshed.index(current_path)
        else:
            self.selected = min(self.selected, max(0, len(refreshed)-1))
        self.build_level_grid()
        if refreshed:
            self.select(self.selected)
        else:
            self.current_data = None
            self._warning_suffix = ''
            self.preview_title.configure(text='')
            if self.canvas:
                self.canvas.clear_scene()
            self._set_legend(())
            self.previous_button.configure(state='disabled')
            self.next_button.configure(state='disabled')
            self.status.set(f"No level JSON files found in {self.level_directory}")
        return 'break'

    def select(self, index: int, *, preserve_view: bool = False) -> None:
        if not self.levels:
            return
        self.selected = max(0, min(index, len(self.levels) - 1))
        path = self.levels[self.selected]
        try:
            stat = path.stat()
            cache_key = (path.resolve(), stat.st_mtime_ns, stat.st_size, self.tolerance, self.outline_only)
            cached = self._level_cache.pop(cache_key, None)
            if cached is None:
                data = load_json(path)
                issues = validate(data, self.tolerance)
            else:
                data, issues, scene = cached
            errors = [issue for issue in issues if issue.severity == "error"]
            if errors:
                raise ValueError("; ".join(issue.message for issue in errors[:2]))
            if cached is None:
                scene = compile_vector_level(data, self.outline_only)
            # A changed file supersedes any older cached revision of that path.
            for old_key in list(self._level_cache):
                if old_key[0] == cache_key[0]:
                    del self._level_cache[old_key]
            self._level_cache[cache_key] = (data, issues, scene)
            while len(self._level_cache) > self.CACHE_LEVELS:
                self._level_cache.popitem(last=False)
            self.current_data = data
            self.preview_title.configure(text=data.get('map', {}).get('name', path.stem))
            if self.canvas and (not preserve_view or self.canvas.scene is not scene):
                self.canvas.set_scene(scene)
            self._set_legend(scene.legend)
            warnings = [issue.message for issue in issues if issue.severity == "warning"]
            self._warning_suffix = f" | Warning: {warnings[0]}" if warnings else ""
            self._update_status()
        except (OSError, ValueError, SystemExit) as exc:
            self.status.set(f"Cannot preview {path.name}: {exc}")
        for position, button in enumerate(self.level_buttons):
            button.configure(bg=UI_SELECTED if position == self.selected else UI_SURFACE,
                             relief="sunken" if position == self.selected else "flat")
        self.previous_button.configure(state="normal" if self.selected > 0 else "disabled")
        self.next_button.configure(state="normal" if self.selected < len(self.levels) - 1 else "disabled")
        if not preserve_view:
            self.list_canvas.yview_moveto(max(0, (self.selected // self.COLUMNS - 1) / max(1, math.ceil(len(self.levels) / self.COLUMNS))))

    def step(self, direction: int) -> None:
        self.select(self.selected + direction)

    def toggle_aa(self, _event: Any = None) -> str:
        if self.canvas:
            self.canvas.toggle_aa()
        if self.current_data is not None:
            self._update_status()
        return 'break'

    def toggle_point_legend(self, _event: Any = None) -> str:
        if self.canvas:
            self.canvas.toggle_controls()
        if self.current_data is not None:
            self._update_status()
        return 'break'

    def toggle_legend(self, _event: Any = None) -> str:
        """Hide/show the fixed footer without rendering or rereading the map."""
        if not self.levels:
            return "break"
        self.legend_visible = not self.legend_visible
        if self.legend_visible:
            self.legend_frame.grid()
        else:
            self.legend_frame.grid_remove()
        if self.current_data is not None:
            self._update_status()
        return "break"


def ttk_frame(parent: Any, **kwargs: Any) -> tk.Frame:
    return ttk.Frame(parent, **kwargs)


def launch_browser(level_directory: Path, start_path: Path | None, tolerance: float, outline_only: bool) -> None:
    root = tk.Tk()
    LevelBrowserApp(root, discover_levels(level_directory), level_directory,
                    start_path, tolerance, outline_only)
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read and visualize a Bike Stunt level JSON without modifying it.")
    parser.add_argument("level", type=Path, nargs="?", help="Level JSON file, or a directory to browse")
    parser.add_argument("--levels-dir", type=Path, help="Directory of JSON levels for the right-side browser")
    parser.add_argument("--output", "-o", type=Path, help="Write a preview image (.png, .svg, or .pdf) instead of opening a window")
    parser.add_argument("--summary", action="store_true", help="Print only validation and the level overview")
    parser.add_argument("--outline-only", action="store_true", help="Render terrain outlines without the ground fill")
    parser.add_argument("--merge-tolerance", type=float, default=0.05, help="Reserved tolerance for future geometry checks (default: 0.05)")
    args = parser.parse_args()
    if args.merge_tolerance < 0:
        parser.error("--merge-tolerance must be non-negative")

    workspace_directory = Path(__file__).resolve().parent
    generated_levels_directory = workspace_directory / "levels"
    default_directory = generated_levels_directory if generated_levels_directory.is_dir() else workspace_directory / "examples"
    chosen = args.level.resolve() if args.level else None
    level_directory = args.levels_dir.resolve() if args.levels_dir else (chosen if chosen and chosen.is_dir() else (chosen.parent if chosen else default_directory))
    start_path = chosen if chosen and chosen.is_file() else None
    if not args.summary and not args.output:
        launch_browser(level_directory, start_path, args.merge_tolerance, args.outline_only)
        return 0
    if not start_path:
        parser.error("--summary and --output require a level JSON file, not a directory")
    data = load_json(start_path)
    issues = validate(data, args.merge_tolerance)
    for issue in issues:
        print(f"{issue.severity.upper()}: {issue.message}")
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        print(f"Validation failed with {len(errors)} error(s).", file=sys.stderr)
        return 2
    print(overview(data, args.merge_tolerance))
    if not args.summary:
        visualize(data, args.merge_tolerance, args.outline_only, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
