"""Exercise navigation callbacks on a hidden Tk preview, without user input."""
import tkinter as tk
import unittest
import tempfile
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from matplotlib.backend_bases import MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.text import Text
import numpy as np
from level_visualizer import LevelBrowserApp


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        base = Path(__file__).resolve().parent
        cls.app = LevelBrowserApp(cls.root, [base/'examples/sample_level.json',
                                           base/'levels/00_cinder_crown.json'], None, .05, False)
        cls.root.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        cls.root.update_idletasks()
        cls.root.destroy()

    def setUp(self):
        self.app.select(0)
        self.app.canvas.draw()

    def mouse(self, name, x, y, **kwargs):
        event = MouseEvent(name, self.app.canvas, x, y, **kwargs)
        self.app.canvas.callbacks.process(name, event)
        return event

    def test_list_wheel_only_inside_right_panel(self):
        event = SimpleNamespace(x_root=0, y_root=0, delta=-120)
        for widget, expected in [(self.app.level_buttons[0], True),
                                 (self.app.right_panel, True),
                                 (self.app.canvas.get_tk_widget(), False),
                                 (self.root, False), (None, False)]:
            with patch.object(self.root, 'winfo_containing', return_value=widget), \
                 patch.object(self.app.list_canvas, 'yview_scroll') as scroll:
                self.app._mousewheel(event)
                self.assertEqual(scroll.called, expected)

    def test_zoom_keeps_cursor_anchor_and_direction(self):
        ax = self.app.canvas.figure.axes[0]
        x = ax.bbox.x0+ax.bbox.width*.3
        y = ax.bbox.y0+ax.bbox.height*.6
        anchor = ax.transData.inverted().transform((x,y))
        width = ax.get_xlim()[1]-ax.get_xlim()[0]
        self.mouse('scroll_event', x, y, step=1)
        self.app.canvas.draw()
        self.assertAlmostEqual(ax.get_xlim()[1]-ax.get_xlim()[0], width/1.2)
        after = ax.transData.inverted().transform((x,y))
        for a,b in zip(anchor,after):
            self.assertAlmostEqual(a,b,delta=.15)
        self.mouse('scroll_event', x, y, step=-1)
        self.app.canvas.draw()
        self.assertAlmostEqual(ax.get_xlim()[1]-ax.get_xlim()[0], width)

    def test_left_drag_moves_map_without_scaling_and_release_stops(self):
        ax = self.app.canvas.figure.axes[0]
        x,y = ax.bbox.x0+ax.bbox.width*.5, ax.bbox.y0+ax.bbox.height*.5
        limits = ax.get_xlim()
        self.mouse('button_press_event', x, y, button=1)
        self.mouse('motion_notify_event', x+40, y+20)
        self.app.canvas.draw()
        after = ax.get_xlim()
        self.assertLess(after[0],limits[0])
        self.assertAlmostEqual(after[1]-after[0], limits[1]-limits[0])
        self.mouse('button_release_event', x+40,y+20,button=1)
        self.mouse('motion_notify_event', x+80,y+30)
        self.assertEqual(ax.get_xlim(),after)
        self.assertIsNone(self.app._pan_axes)

    def test_legend_toggle_preserves_zoom_and_level_switch_resets(self):
        ax = self.app.canvas.figure.axes[0]
        x,y = ax.bbox.x0+ax.bbox.width*.5, ax.bbox.y0+ax.bbox.height*.5
        self.mouse('scroll_event', x, y, step=3)
        self.app.canvas.draw()
        before = ax.get_xlim()
        self.app.toggle_legend()
        self.app.canvas.draw()
        after = self.app.canvas.figure.axes[0].get_xlim()
        for a,b in zip(before,after):
            self.assertAlmostEqual(a,b)
        self.app.select(1)
        self.app.canvas.draw()
        left,right = self.app.canvas.figure.axes[0].get_xlim()
        self.assertLess(left,0)
        self.assertGreater(right,307)

    def test_navigation_keeps_labels_and_matches_full_render(self):
        self.app.legend_visible = True
        self.app.select(0)
        self.root.update_idletasks()
        ax = self.app.canvas.figure.axes[0]
        texts = [t for t in self.app.canvas.figure.findobj(Text) if t.get_visible()]
        x, y = ax.bbox.x0 + ax.bbox.width * .5, ax.bbox.y0 + ax.bbox.height * .5
        self.mouse('scroll_event', x, y, step=2)
        self.mouse('button_press_event', x, y, button=1)
        self.mouse('motion_notify_event', x + 40, y + 20)
        self.app._schedule_preview_draw(immediate=True)
        self.assertTrue(all(t.get_visible() for t in texts))
        actual = np.asarray(self.app.canvas.buffer_rgba()).copy()
        FigureCanvasAgg.draw(self.app.canvas)
        np.testing.assert_array_equal(actual, np.asarray(self.app.canvas.buffer_rgba()))
        self.mouse('button_release_event', x + 40, y + 20, button=1)
        self.app.canvas.resize(SimpleNamespace(width=1100, height=720))
        self.root.update_idletasks()
        ax.set_xlim(500, 600)
        self.app.canvas.draw_navigation()
        resized = np.asarray(self.app.canvas.buffer_rgba()).copy()
        FigureCanvasAgg.draw(self.app.canvas)
        np.testing.assert_array_equal(resized, np.asarray(self.app.canvas.buffer_rgba()))

    def test_cached_level_reuses_canvas_and_invalidates_changed_file(self):
        widget = self.app.canvas.get_tk_widget()
        with patch('level_visualizer.build_figure', side_effect=AssertionError('Unexpected rebuild')):
            self.app.select(0)
        self.assertIs(self.app.canvas.get_tk_widget(), widget)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'level.json'
            shutil.copyfile(self.app.levels[0], path)
            original_levels = self.app.levels
            try:
                self.app.levels = [path]
                self.app.select(0)
                first = self.app.current_data
                path.write_text(path.read_text(encoding='utf-8').replace('"map"', '"updatedMap"'), encoding='utf-8')
                self.app.select(0)
                self.assertIsNot(self.app.current_data, first)
                self.assertIn('updatedMap', self.app.current_data)
            finally:
                self.app.levels = original_levels


if __name__=='__main__':
    unittest.main()
