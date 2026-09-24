"""Stable library IDs, placed references and shape-name preview overlays."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bike_stunt.library_ids import assign_library_ids, validate_library_ids
from bike_stunt.obstacle_library import GROUPS, as_level, build_variant, load_catalog, place
from bike_stunt.rendering import PreviewCanvas, compile_vector_level
from bike_stunt.shape_labels import map_shape_labels
from bike_stunt.terrain_export import export_level
from bike_stunt.world_scale import scale_world_data


class LibraryIdentityTests(unittest.TestCase):
    def test_id_assignment_preserves_existing_ids_after_reordering_and_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / 'library'
            library.mkdir()
            catalog = library / 'obstacle_catalog.json'
            catalog.write_text(json.dumps({'groups': [{'path': 'test.json'}]}), encoding='utf-8')
            path = library / 'test.json'
            path.write_text(json.dumps({'variants': [{'id': 'a'}, {'id': 'b'}]}), encoding='utf-8')
            self.assertEqual(assign_library_ids(root), 2)
            first = json.loads(path.read_text(encoding='utf-8'))
            before = path.read_bytes()
            self.assertEqual(assign_library_ids(root), 0)
            self.assertEqual(path.read_bytes(), before)
            first['variants'].reverse()
            first['variants'].append({'id': 'c'})
            path.write_text(json.dumps(first), encoding='utf-8')
            self.assertEqual(assign_library_ids(root), 1)
            updated = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual([(v['id'], v['libID']) for v in updated['variants']],
                             [('b', 2), ('a', 1), ('c', 3)])
            updated['variants'] = [updated['variants'][1], {'id': 'd'}]
            path.write_text(json.dumps(updated), encoding='utf-8')
            assign_library_ids(root)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['variants'][-1]['libID'], 4)

    def test_invalid_or_duplicate_library_ids_are_rejected(self):
        for values in ((None,), (True,), (0,), (-1,), ('1',), (1, 1)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_library_ids({'id': str(i), 'libID': value} for i, value in enumerate(values))

    def test_all_library_shapes_use_persistent_ids_and_keep_references(self):
        families = load_catalog()
        variants = [v for f in families.values() for v in f['variants']]
        self.assertEqual(len(validate_library_ids(variants)), len(variants))
        for family in families.values():
            for variant in family['variants']:
                with self.subTest(variant=variant['id']):
                    local = build_variant(family, variant)
                    original = copy.deepcopy(local)
                    placed = place(local, 17, -8, 's04')
                    self.assertEqual(local, original)
                    ids = [item['id'] for group in GROUPS for item in placed[group]]
                    self.assertEqual(len(ids), len(set(ids)))
                    self.assertTrue(set(ids).isdisjoint(
                        item['id'] for group in GROUPS for item in place(local, 30, 0, 's05')[group]))
                    for group in GROUPS:
                        for source, item in zip(local[group], placed[group]):
                            if 'points' in source:
                                self.assertEqual(item['id'], f"s04_{variant['libID']}_{source['id']}")
                                self.assertEqual(item['metadata']['libName'], source['id'])
                                self.assertEqual(item['metadata']['libID'], variant['libID'])
                            for field in ('properties', 'metadata'):
                                meta = item.get(field, {})
                                for key in ('postDestroyRoute', 'revealsRoute', 'supportingRingId', 'supportingShapeId'):
                                    if key in meta:
                                        self.assertIn(meta[key], ids)
                                for key in ('revealsObjects', 'destroysRoutes'):
                                    self.assertTrue(set(meta.get(key, [])).issubset(ids))
                                if 'commitTrigger' in meta:
                                    self.assertIn(meta['commitTrigger']['routeId'], ids)
                    for join in placed['joins']:
                        self.assertIn(join['from'], ids)
                        self.assertIn(join['to'], ids)

    def test_merged_shapes_keep_labels_through_export_scale_and_hidden_controls(self):
        family = load_catalog()['flat']
        local = build_variant(family, family['variants'][0])
        first = place(local, 0, 0, 's00')
        second = place(local, local['ports']['exit']['x'], 0, 's01')
        source = as_level([first, second], 'test', 'test')
        expected = {item['id'] for item in source['MainPlatform']}
        exported = export_level(source)
        self.assertEqual(len(exported['MainPlatform']), 1)
        self.assertEqual(export_level(exported), exported)
        labels = list(map_shape_labels(exported))
        self.assertEqual({label['id'] for label in labels}, expected)
        scaled = scale_world_data(exported, 2)
        for original, new in zip(labels, map_shape_labels(scaled)):
            self.assertEqual(new, {**original, 'x': original['x'] * 2, 'y': original['y'] * 2})
        scene = compile_vector_level(scaled, show_controls=False)
        self.assertEqual({m.label for m in scene.markers if m.kind == 'shape_label'}, expected)
        self.assertFalse(any(m.kind in ('knot', 'handle') for m in scene.markers))
        self.assertFalse(any(m.kind == 'shape_label' for m in
                             compile_vector_level(scaled, show_shape_names=False).markers))

    def test_shape_text_tracks_pan_zoom_without_marker_symbols(self):
        canvas = SimpleNamespace(scale=3, offset=[40, -10], coords=Mock(),
                                 STROKE_OFFSETS=PreviewCanvas.STROKE_OFFSETS)
        spec = SimpleNamespace(position=(12, -4), kind='shape_label')
        canvas._marker_items = [(None, 7, spec)]
        canvas._label_strokes = {7: list(range(10, 18))}
        PreviewCanvas._project_markers(canvas)
        canvas.coords.assert_any_call(7, 76, -22)
        self.assertEqual(canvas.coords.call_count, 9)
        canvas.scale, canvas.offset = 2, [-5, 7]
        PreviewCanvas._project_markers(canvas)
        canvas.coords.assert_any_call(7, 19, -1)


if __name__ == '__main__':
    unittest.main()
