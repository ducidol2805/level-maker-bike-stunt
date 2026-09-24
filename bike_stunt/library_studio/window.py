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
from bike_stunt.rendering import (
    PreviewCanvas, PreviewLegend, VectorLevel, VectorMarker, VectorPath, compile_vector_level,
    shape_line_color,
)
from bike_stunt.map_viewer.validation import validate
from bike_stunt.library_studio.editor_session import EditorSession
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
    scene = compile_vector_level(data, outline_only, show_controls=False, show_shape_names=False)
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


class ObstacleEditorWindow(EditorSession):
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
        toolbar = ttk.Frame(self.window, padding=10)
        toolbar.grid(row=0, column=0, sticky='ew')
        top_line = ttk.Frame(toolbar)
        top_line.pack(fill='x')
        mode_line = ttk.Frame(toolbar)
        mode_line.pack(fill='x', pady=(6, 0))
        self.variant_label = tk.Label(top_line, text=variant['id'], bg=UI_BG, fg=UI_TEXT,
                                      font=('Segoe UI', 12, 'bold'))
        self.variant_label.pack(side='left', padx=(0, 16))
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

        body = ttk.Frame(self.window, padding=(10, 0, 10, 10))
        body.grid(row=1, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=PLOT_BG, highlightthickness=1,
                                highlightbackground=UI_BORDER, cursor='arrow', takefocus=True)
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
        panel = ttk.Frame(body, padding=12)
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
        tk.Label(panel, text='Wheel: zoom  |  Middle drag: pan\nShift+drag: snap  |  WASD: move point/shape 1\n1/2/3: tangent  |  Z/X/C/V: convert shape',
                 bg=UI_BG, fg=UI_MUTED, justify='left').pack(anchor='w', pady=(16, 0))
        footer = ttk.Frame(self.window, padding=(10, 0, 10, 10))
        footer.grid(row=2, column=0, sticky='ew')
        self.status = tk.StringVar(value='Select a point to edit.')
        tk.Label(footer, textvariable=self.status, bg=UI_BG, fg=UI_MUTED).pack(side='left')
        self._button(footer, 'Save', self.save).pack(side='right', padx=(8, 0))
        self._button(footer, 'Revert', self.revert).pack(side='right')
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
            converted = ground(shape['id'], converted['points'], depth=15 if target == 'MainPlatform' else 3)
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
    def faded_color(color: str, alpha: float = .4) -> str:
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
                                           fill=self.faded_color('#192335' if minor else '#27364d', .5))
                    for n in range(math.ceil(bottom/spacing), math.floor(top/spacing)+1):
                        y = self.screen(0, n*spacing)[1]
                        minor = spacing < major and n % 2 != 0
                        canvas.create_line(0, y, width, y,
                                           fill=self.faded_color('#192335' if minor else '#27364d', .5))
        except ValueError:
            pass
        for shape_index, (other_group, other_shape) in enumerate(self.shapes):
            if shape_index == self.shape_index:
                continue
            route = sample_curve(other_shape, 20)
            coords = [value for x, y in route for value in self.screen(x, y)]
            canvas.create_line(*coords, fill=self.faded_color(shape_line_color(other_group, other_shape)), width=2,
                               smooth=False, tags=(f'shape:{shape_index}',))
            for index, point in enumerate(other_shape['points']):
                x, y = self.screen(point['x'], point['y'])
                canvas.create_oval(x-6, y-6, x+6, y+6, fill=self.faded_color('#f8fafc'),
                                   outline=PLOT_BG, tags=(f'shape:{shape_index}',))
                canvas.create_text(x+10, y-10, text=str(index+1), fill=self.faded_color(UI_TEXT),
                                   anchor='w', font=('Segoe UI', 9), tags=(f'shape:{shape_index}',))
        route = sample_curve(shape, 20)
        coords = [value for x, y in route for value in self.screen(x, y)]
        canvas.create_line(*coords, fill=shape_line_color(group, shape), width=3, smooth=False,
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
        group, shape, points = self.current()
        if self.selected is None:
            for point in points:
                point['x'] = round(point['x']+dx, 4)
                point['y'] = round(point['y']+dy, 4)
            self.dirty = True
            self.changed_shapes.add((group, shape['id']))
            self.status.set(f"Shape {shape['id']} moved ({dx:+}, {dy:+})")
            self.redraw()
            return
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
            if event.state & 0x0001:
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

    def apply_fields(self, _event: Any = None) -> bool:
        if self.selected is None:
            return False
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
            return False
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
        return True

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
                           [vertex(0, 8, mode='linear'), vertex(3, 8, mode='linear')],
                           depth=15 if group == 'MainPlatform' else 3)
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

    def save(self) -> bool:
        from tkinter import messagebox
        if self.pending_fields():
            if not self.apply_fields():
                return False
        if not self.dirty:
            return True
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
            return False
        self.variant = copy.deepcopy(proposed)
        self.changed_shapes.clear()
        self.dirty = False
        self.library.reload()
        self.status.set('Saved.')
        return True



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
        self.editor: ObstacleEditorWindow | None = None
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self._layout_after: str | None = None
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self.window, padding=(12, 8))
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
        if self.cards:
            self.open_editor(self.cards[0].variant_id)

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
                self.canvas.tag_bind(tag, '<Button-1>',
                                     lambda _event, value=variant_id: self.open_editor(value))
                self.canvas.tag_raise(edit_button)
                self.cards.append(card)
                group.cards.append(card)
            self.groups.append(group)
        self.window.title(f'Bike Stunt - Obstacle Library ({len(self.cards)})')
        self._layout()
        self.highlight_selection()
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

    def highlight_selection(self) -> None:
        selected = self.editor.variant['id'] if self.editor else None
        for card in self.cards:
            self.canvas.itemconfigure(card.background,
                                      fill=UI_SELECTED if card.variant_id == selected else PLOT_BG)

    def open_editor(self, variant_id: str) -> None:
        if self.editor is not None and self.editor.variant['id'] == variant_id:
            self.editor.window.lift()
            return
        try:
            family = next(family for family in load_catalog().values()
                          if any(item['id'] == variant_id for item in family['variants']))
            variant = next(item for item in family['variants'] if item['id'] == variant_id)
            if self.editor is None:
                self.editor = ObstacleEditorWindow(self, family, variant)
            elif not self.editor.load_variant(family, variant):
                return
            self.editors = {variant_id: self.editor}
            self.highlight_selection()
            self.editor.window.lift()
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            from tkinter import messagebox
            messagebox.showerror('Cannot open obstacle editor', str(exc), parent=self.window)

    def close(self, destroy: Any = None) -> None:
        if self.editor is None or self.editor.confirm_leave():
            (destroy or self.window.destroy)()

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
