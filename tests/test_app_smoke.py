"""Exercise package entry points and real Tk callbacks after module moves."""
import copy
import json
from pathlib import Path
import runpy
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bike_stunt.library_studio.window import ObstacleLibraryWindow, compile_obstacle_card
from bike_stunt.map_viewer.canvas import PreviewCanvas, compile_vector_level
from bike_stunt.map_viewer.validation import discover_levels, load_json
from bike_stunt.obstacle_library import ROOT, build_variant, load_catalog


class SceneSmokeTests(unittest.TestCase):
    def test_all_saved_maps_compile_with_point_controls(self):
        paths = discover_levels(ROOT / 'levels')
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(level=path.name):
                scene = compile_vector_level(load_json(path))
                self.assertTrue(scene.paths)
                self.assertTrue(any(marker.kind == 'knot' for marker in scene.markers))
                self.assertIn('tangentIn', [entry[0] for entry in scene.legend])

    def test_all_library_cards_compile(self):
        families = load_catalog()
        self.assertTrue(families)
        for family in families.values():
            for variant in family['variants']:
                with self.subTest(variant=variant['id']):
                    scene = compile_obstacle_card(family, variant, False)
                    self.assertTrue(scene.paths)


class TkSmokeTests(unittest.TestCase):
    def setUp(self):
        self.callback_errors = []
        self.dialog = patch('tkinter.messagebox.showerror', side_effect=AssertionError)
        self.dialog.start()
        self.addCleanup(self.dialog.stop)

    def make_root(self):
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.addCleanup(root.destroy)
        return root

    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from TkSmokeTests.descendants(child)

    def test_map_viewer_entry_point_and_navigation(self):
        root = self.make_root()

        def exercise():
            root.withdraw()
            root.update()
            canvas = next(w for w in self.descendants(root) if isinstance(w, PreviewCanvas))
            self.assertIsNotNone(canvas.scene)
            self.assertFalse(canvas.controls_visible)
            self.assertFalse(canvas.aa_enabled)
            canvas._resize(SimpleNamespace(width=800, height=500))
            canvas.toggle_controls()
            canvas.toggle_aa()
            self.assertTrue(canvas.controls_visible)
            self.assertIsNotNone(canvas._aa_photo)
            scale = canvas.scale
            canvas._wheel(SimpleNamespace(delta=120, x=400, y=250))
            canvas._draw_view()
            self.assertGreater(canvas.scale, scale)
            offset = canvas.offset.copy()
            canvas._start_pan(SimpleNamespace(x=400, y=250))
            canvas._drag(SimpleNamespace(x=430, y=280))
            canvas._draw_view()
            canvas._end_pan()
            self.assertNotEqual(canvas.offset, offset)
            canvas.fit()
            canvas._draw_view()
            self.assertTrue(canvas._fit_mode)
            self.assertEqual(self.callback_errors, [])

        with patch('tkinter.Tk', return_value=root), patch.object(root, 'mainloop', exercise):
            with patch('sys.argv', ['apps.map_viewer']):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_module('apps.map_viewer', run_name='__main__')
                self.assertEqual(result.exception.code, 0)

    def test_library_studio_entry_point(self):
        root = self.make_root()
        expected = sum(len(f['variants']) for f in load_catalog().values())

        def exercise():
            root.update()
            studio_editor = next(w for w in self.descendants(root)
                                 if isinstance(w, tk.Toplevel) and w.title().startswith('Edit obstacle'))
            self.assertTrue(studio_editor.winfo_exists())
            canvas = next(w for w in self.descendants(root)
                          if isinstance(w, tk.Canvas) and w.find_withtag('card'))
            self.assertEqual(len(canvas.find_withtag('card')), expected)
            self.assertEqual(self.callback_errors, [])

        with patch('tkinter.Tk', return_value=root), patch.object(root, 'mainloop', exercise):
            with patch('sys.argv', ['apps.library_studio']):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_module('apps.library_studio', run_name='__main__')
                self.assertEqual(result.exception.code, 0)

    def test_library_editor_save_round_trip(self):
        root = self.make_root()
        studio = ObstacleLibraryWindow(root)
        root.update()
        family = load_catalog()['kicker']
        variant = family['variants'][0]
        studio.open_editor(variant['id'])
        editor = studio.editors[variant['id']]
        editor.select(1)
        editor.nudge_point(1, 0)
        group, shape, points = editor.current()
        expected = copy.deepcopy(points[1])

        # Save into an isolated catalog; never edit the project's library.
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            library = temp_root / 'library'
            library.mkdir()
            catalog = {'groups': [{'type': family['type'], 'path': 'kicker.json'}]}
            (library / 'obstacle_catalog.json').write_text(json.dumps(catalog), encoding='utf-8')
            source = library / 'kicker.json'
            source.write_text(json.dumps(family), encoding='utf-8')
            with patch('bike_stunt.library_studio.window.ROOT', temp_root):
                with patch.object(studio, 'reload') as reload_library:
                    editor.save()
                    reload_library.assert_called_once()
            saved = json.loads(source.read_text(encoding='utf-8'))['variants'][0]
            module = build_variant(family, saved, scale=family['_worldScale'])
            changed = next(item for item in module[group] if item['id'] == shape['id'])
            self.assertEqual(changed['points'][1], expected)
        self.assertTrue(editor.window.winfo_exists())
        self.assertFalse(editor.dirty)
        self.assertEqual(self.callback_errors, [])


if __name__ == '__main__':
    unittest.main()
