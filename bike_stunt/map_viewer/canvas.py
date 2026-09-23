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
from bike_stunt.map_viewer.validation import (
    TANGENT_COLORS, bounds, point, points, required_group, spline_controls,
)

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
