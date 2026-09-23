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
from bike_stunt.map_viewer.validation import validate, overview, visualize, load_json, discover_levels
from bike_stunt.rendering import PreviewCanvas, PreviewLegend, compile_vector_level
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
        tk.Button(actions, text='Refresh', command=self.refresh_levels).pack(side='right', fill='x')
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

    workspace_directory = Path(__file__).resolve().parents[2]
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
