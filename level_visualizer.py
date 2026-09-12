#!/usr/bin/env python3
"""Read, validate, summarize, and visualize Bike Stunt level JSON.

This is intentionally a read-only inspection tool: it never rewrites the
source level.  It can optionally render a PNG, SVG, or PDF preview.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tkinter as tk
import time
from collections import OrderedDict
from tkinter import ttk
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from obstacle_library import sample_curve

try:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon
except ImportError as exc:  # pragma: no cover - user-facing dependency check
    raise SystemExit("Missing dependency. Run: pip install -r requirements.txt") from exc


Point = tuple[float, float]
EPSILON = 1e-9
KNOWN_OBJECT_TYPES = {"explosive_ramp", "explosive_barrel", "speed_boost", "coin"}


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

    for group, minimum in (("MainPlatform", 3), ("RampPlatform", 2), ("deadzone", 3)):
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
        f"Deadzones: {len(deadzones)} | Checkpoints: {len(required_group(data, 'CheckPoint'))} | Interactables: {len(required_group(data, 'InteractableObject'))}",
    ))


def bounds(data: dict[str, Any]) -> tuple[float, float, float, float]:
    found: list[Point] = []
    for name in ("Start", "End", "Top", "Bottom"):
        if name in data:
            found.append(point(data[name], name))
    for group in ("MainPlatform", "RampPlatform", "deadzone"):
        for index, shape in enumerate(required_group(data, group)):
            found.extend(points(shape, f"{group}[{index}]", 3 if group == "deadzone" else 2))
    for group in ("CheckPoint", "InteractableObject"):
        for index, entry in enumerate(required_group(data, group)):
            if entry.get("type") == "explosive_ramp":
                found.extend(points(entry, f"{group}[{index}]"))
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


def build_figure(data: dict[str, Any], tolerance: float, outline_only: bool, show_legend: bool = True) -> Figure:
    figure = Figure(figsize=(16, 8))
    ax = figure.add_subplot(111)
    ax.set_title(data.get("map", {}).get("name", "Bike Stunt Level"))
    ax.set_xlabel("World X (units)")
    ax.set_ylabel("World Y (units)")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, alpha=0.25, linewidth=0.6)

    for index, zone in enumerate(required_group(data, "deadzone")):
        line = points(zone, f"deadzone[{index}]", 3)
        ax.add_patch(Polygon(line, closed=zone.get("closed", True), facecolor="#ef4444", edgecolor="#991b1b", alpha=0.28, label="Deadzone" if index == 0 else None))

    for index, shape in enumerate(required_group(data, "MainPlatform")):
        line = points(shape, f"MainPlatform[{index}]", 3)
        ax.add_patch(Polygon(line, closed=True, facecolor="none" if outline_only else "#4ade80", edgecolor="#14532d", linewidth=2.8, alpha=0.82, label="MainPlatform terrain" if index == 0 else None))

    for index, shape in enumerate(required_group(data, "RampPlatform")):
        draw_path(ax, points(shape, f"RampPlatform[{index}]"), color="#d97706", linewidth=3.6, solid_capstyle="round", label="RampPlatform" if index == 0 else None)

    marker_groups: dict[str, list[Point]] = {}
    for index, item in enumerate(required_group(data, "InteractableObject")):
        kind = item.get("type")
        if kind == "explosive_ramp":
            draw_path(ax, points(item, f"InteractableObject[{index}]"), color="#dc2626", linewidth=4, linestyle="--", label="Explosive ramp")
            continue
        marker_groups.setdefault(kind, []).append(point(item["transform"], f"InteractableObject[{index}].transform"))
    for kind, positions in marker_groups.items():
        xs, ys = zip(*positions)
        style = {"explosive_barrel": ("X", "#dc2626"), "speed_boost": (">", "#2563eb"), "coin": ("o", "#eab308")}.get(kind, ("$?$", "#6b7280"))
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
        ax.annotate(name, (x, y), xytext=(7, 7), textcoords="offset points", fontsize=9, weight="bold")

    x0, x1, y0, y1 = bounds(data)
    ax.set(xlim=(x0, x1), ylim=(y0, y1))
    if show_legend:
        handles, labels = ax.get_legend_handles_labels()
        unique = dict(zip(labels, handles))
        figure.legend(
            unique.values(), unique.keys(),
            loc="lower center",
            bbox_to_anchor=(0.5, 0.012),
            ncol=min(5, len(unique)),
            framealpha=0.94,
            fontsize=9,
        )
        figure.subplots_adjust(left=0.06, right=0.985, top=0.93, bottom=0.17)
    else:
        figure.subplots_adjust(left=0.06, right=0.985, top=0.93, bottom=0.09)
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


class PreviewCanvas(FigureCanvasTkAgg):
    """Cache the static frame; navigation redraws axes with live tick labels."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._frame = None
        super().__init__(*args, **kwargs)

    def draw(self) -> None:
        ax = self.figure.axes[0]
        # Render the static layer once, then composite live axes before blit.
        animated = ax.get_animated()
        ax.set_animated(True)
        try:
            FigureCanvasAgg.draw(self)
        finally:
            ax.set_animated(animated)
        self._frame = self.copy_from_bbox(self.figure.bbox)
        ax.draw(self.get_renderer())
        self.blit()

    def draw_navigation(self) -> None:
        if self._frame is None or self._idle_draw_id is not None:
            self.draw()
            return
        self.restore_region(self._frame)
        self.figure.axes[0].draw(self.get_renderer())
        self.blit()


class LevelBrowserApp:
    """Read-only desktop browser for a directory of level JSON files."""

    COLUMNS = 5

    def __init__(self, root: tk.Tk, levels: list[Path], start_path: Path | None, tolerance: float, outline_only: bool) -> None:
        self.root = root
        self.levels = levels
        self.tolerance = tolerance
        self.outline_only = outline_only
        self.legend_visible = True
        self.current_data: dict[str, Any] | None = None
        self.selected = 0
        if start_path:
            try:
                self.selected = levels.index(start_path.resolve())
            except ValueError:
                pass
        self.canvas: PreviewCanvas | None = None
        self._preview_connections: list[int] = []
        self._pan_axes: Any = None
        self._level_cache: OrderedDict[Any, Any] = OrderedDict()
        self._last_preview_draw = 0.0
        self._preview_draw_after: str | None = None
        self.level_buttons: list[tk.Button] = []
        self.status = tk.StringVar(value="Select a level | Press H to hide/show the map legend")

        root.title("Bike Stunt Level Visualizer")
        root.minsize(1050, 620)
        root.geometry("1480x860")
        root.bind_all("<Key-h>", self.toggle_legend)
        root.bind_all("<Key-H>", self.toggle_legend)
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
        self.preview.rowconfigure(0, weight=1)

        side = ttk_frame(content, padding=8, relief="groove", borderwidth=1)
        self.right_panel = side
        side.grid(row=0, column=1, sticky="ns")
        tk.Label(side, text="LEVELS", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(side, text=f"{len(levels)} level(s) | 5 columns", fg="#4b5563").pack(anchor="w", pady=(0, 8))

        nav = ttk_frame(side)
        nav.pack(fill="x", pady=(0, 8))
        self.previous_button = tk.Button(nav, text="◀ Prev", command=lambda: self.step(-1), width=11)
        self.previous_button.pack(side="left")
        self.next_button = tk.Button(nav, text="Next ▶", command=lambda: self.step(1), width=11)
        self.next_button.pack(side="right")

        list_shell = ttk_frame(side)
        list_shell.pack(fill="both", expand=True)
        self.list_canvas = tk.Canvas(list_shell, width=385, highlightthickness=0, background="#f8fafc")
        scrollbar = tk.Scrollbar(list_shell, orient="vertical", command=self.list_canvas.yview)
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = ttk_frame(self.list_canvas, padding=6)
        self.grid_window = self.list_canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", self._resize_scroll_region)
        self.list_canvas.bind("<Configure>", self._resize_grid_width)
        # Add to the toplevel binding: Matplotlib also installs a wheel handler
        # there. Never replace it or route preview-wheel events into the list.
        root.bind("<MouseWheel>", self._mousewheel, add="+")
        root.bind("<Button-4>", self._mousewheel, add="+")
        root.bind("<Button-5>", self._mousewheel, add="+")

        footer = ttk_frame(root, padding=(12, 0, 12, 8))
        footer.grid(row=1, column=0, sticky="ew")
        tk.Label(footer, textvariable=self.status, anchor="w", fg="#334155").pack(fill="x")

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

    def _zoom_preview(self, event: Any) -> None:
        if not self.canvas or event.canvas is not self.canvas or not event.step:
            return
        if self._pan_axes is not None:
            return
        ax = self.canvas.figure.axes[0]
        left, right = ax.get_xlim()
        bottom, top = ax.get_ylim()
        if event.inaxes is ax and event.xdata is not None:
            anchor_x, anchor_y = event.xdata, event.ydata
        else:
            anchor_x, anchor_y = (left + right) / 2, (bottom + top) / 2
        factor = 1.2 ** (-max(-10, min(10, event.step)))
        if not 1e-4 <= (right-left)*factor <= 1e8:
            return
        ax.set_xlim(anchor_x+(left-anchor_x)*factor, anchor_x+(right-anchor_x)*factor)
        ax.set_ylim(anchor_y+(bottom-anchor_y)*factor, anchor_y+(top-anchor_y)*factor)
        self._schedule_preview_draw()

    def _start_preview_pan(self, event: Any) -> None:
        if not self.canvas or event.canvas is not self.canvas or event.button != 1:
            return
        ax = self.canvas.figure.axes[0]
        if event.inaxes is not ax:
            return
        self._pan_axes = ax
        ax.start_pan(event.x, event.y, 1)
        self.canvas.get_tk_widget().configure(cursor="fleur")

    def _drag_preview(self, event: Any) -> None:
        if self._pan_axes is None or not self.canvas or event.canvas is not self.canvas:
            return
        self._pan_axes.drag_pan(1, None, event.x, event.y)
        self._schedule_preview_draw()

    def _end_preview_pan(self, _event: Any = None, *, redraw: bool = True) -> None:
        if self._pan_axes is not None:
            self._pan_axes.end_pan()
            self._pan_axes = None
        if self.canvas:
            self.canvas.get_tk_widget().configure(cursor="")
        if redraw:
            self._schedule_preview_draw(immediate=True)

    def _schedule_preview_draw(self, *, immediate: bool = False) -> None:
        """Limit expensive Agg redraws to a responsive, stable frame rate."""
        if not self.canvas:
            return
        if immediate:
            if self._preview_draw_after is not None:
                self.root.after_cancel(self._preview_draw_after)
                self._preview_draw_after = None
            self._draw_preview_frame()
            return
        if self._preview_draw_after is not None:
            return
        elapsed = time.perf_counter() - self._last_preview_draw
        delay_ms = max(0, math.ceil((1 / 60 - elapsed) * 1000))
        self._preview_draw_after = self.root.after(delay_ms, self._draw_preview_frame)

    def _draw_preview_frame(self) -> None:
        self._preview_draw_after = None
        if self.canvas:
            self._last_preview_draw = time.perf_counter()
            self.canvas.draw_navigation()

    def build_level_grid(self) -> None:
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
                relief="groove",
                bg="#ffffff",
                activebackground="#dbeafe",
            )
            button.grid(row=index // self.COLUMNS, column=index % self.COLUMNS, padx=3, pady=3, sticky="nsew")
            self.level_buttons.append(button)
        for column in range(self.COLUMNS):
            self.grid_frame.columnconfigure(column, weight=1)

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
                data, issues, figure, initial_view = cached
            errors = [issue for issue in issues if issue.severity == "error"]
            if errors:
                raise ValueError("; ".join(issue.message for issue in errors[:2]))
            view = None
            if preserve_view and self.canvas:
                ax = self.canvas.figure.axes[0]
                view = (ax.get_xlim(), ax.get_ylim())
            if self.canvas:
                self._end_preview_pan(redraw=False)
                if self._preview_draw_after is not None:
                    self.root.after_cancel(self._preview_draw_after)
                    self._preview_draw_after = None
            self.current_data = data
            if cached is None:
                figure = build_figure(data, self.tolerance, self.outline_only, True)
                initial_view = (figure.axes[0].get_xlim(), figure.axes[0].get_ylim())
            self._level_cache[cache_key] = (data, issues, figure, initial_view)
            while len(self._level_cache) > 8:
                self._level_cache.popitem(last=False)
            for legend in figure.legends:
                legend.set_visible(self.legend_visible)
            figure.subplots_adjust(bottom=0.17 if self.legend_visible else 0.09)
            # Fill the preview viewport while keeping world X/Y at equal scale.
            # Zoom can now use the full panel even for a very long level.
            figure.axes[0].set_aspect("equal", adjustable="datalim")
            figure.axes[0].set_xlim((view or initial_view)[0])
            figure.axes[0].set_ylim((view or initial_view)[1])
            if self.canvas:
                previous_figure = self.canvas.figure
                for connection in self._preview_connections:
                    self.canvas.mpl_disconnect(connection)
                figure.set_size_inches(previous_figure.get_size_inches(), forward=False)
                figure.set_dpi(previous_figure.dpi)
                self.canvas.figure = figure
                figure.set_canvas(self.canvas)
                self.canvas._frame = None
            else:
                self.canvas = PreviewCanvas(figure, master=self.preview)
                self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
            self._preview_connections = [self.canvas.mpl_connect(name, handler) for name, handler in (
                ("scroll_event", self._zoom_preview),
                ("button_press_event", self._start_preview_pan),
                ("motion_notify_event", self._drag_preview),
                ("button_release_event", self._end_preview_pan),
                ("figure_leave_event", self._end_preview_pan),
            )]
            self.canvas.draw_idle()
            warnings = [issue.message for issue in issues if issue.severity == "warning"]
            suffix = f" | Warning: {warnings[0]}" if warnings else ""
            legend_state = "legend shown" if self.legend_visible else "legend hidden"
            self.status.set(f"{self.selected + 1}/{len(self.levels)}  {path}{suffix} | Wheel: zoom | Left drag: pan | H: {legend_state}")
        except (OSError, ValueError, SystemExit) as exc:
            self.status.set(f"Cannot preview {path.name}: {exc}")
        for position, button in enumerate(self.level_buttons):
            button.configure(bg="#bfdbfe" if position == self.selected else "#ffffff", relief="sunken" if position == self.selected else "groove")
        self.previous_button.configure(state="normal" if self.selected > 0 else "disabled")
        self.next_button.configure(state="normal" if self.selected < len(self.levels) - 1 else "disabled")
        if not preserve_view:
            self.list_canvas.yview_moveto(max(0, (self.selected // self.COLUMNS - 1) / max(1, math.ceil(len(self.levels) / self.COLUMNS))))

    def step(self, direction: int) -> None:
        self.select(self.selected + direction)

    def toggle_legend(self, _event: Any = None) -> str:
        """Toggle the legend without rebuilding geometry or reloading JSON."""
        if not self.levels:
            return "break"
        self.legend_visible = not self.legend_visible
        self.select(self.selected, preserve_view=True)
        return "break"


def ttk_frame(parent: Any, **kwargs: Any) -> tk.Frame:
    return ttk.Frame(parent, **kwargs)


def launch_browser(level_directory: Path, start_path: Path | None, tolerance: float, outline_only: bool) -> None:
    root = tk.Tk()
    LevelBrowserApp(root, discover_levels(level_directory), start_path, tolerance, outline_only)
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
