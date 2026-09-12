"""Exercise navigation callbacks on a hidden Tk preview, without user input."""
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from matplotlib.backend_bases import MouseEvent
from level_visualizer import LevelBrowserApp


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        base = Path(__file__).resolve().parent
        cls.app = LevelBrowserApp(cls.root, [base/'examples/sample_level.json',
                                           base/'levels/00_cinder_crown.json'], None, .05, False)

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


if __name__=='__main__':
    unittest.main()
