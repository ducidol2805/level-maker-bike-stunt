import copy
import json
import unittest
from pathlib import Path

from obstacle_library import ground, profile, build_campaign_level
from terrain_export import normalize_main_platform, export_level, surface_count


class TerrainExportTests(unittest.TestCase):
    def setUp(self):
        self.level = {'MainPlatform': [ground('a', profile([(0, 2), (10, 2)])),
                                      ground('b', profile([(10, 2), (20, -4)])),
                                      ground('c', profile([(25, -4), (30, -4)]))],
                      'Bottom': {'x': 5, 'y': 2},
                      'InteractableObject': [{'properties': {'postDestroyRoute': 'b'}}]}

    def test_common_floor_without_changing_surface_or_source(self):
        original = copy.deepcopy(self.level)
        result = normalize_main_platform(self.level)
        self.assertEqual(self.level, original)
        self.assertEqual(result['Bottom'], original['Bottom'])
        for before, after in zip(original['MainPlatform'], result['MainPlatform']):
            self.assertEqual(before['points'][:-2], after['points'][:-2])
            self.assertEqual([p['y'] for p in after['points'][-2:]], [-7, -7])

    def test_merge_preserves_handles_and_gap_and_fallback(self):
        source = self.level['MainPlatform']
        source[0]['points'][1]['tangentIn'] = {'x': -2, 'y': 1}
        source[1]['points'][0]['tangentOut'] = {'x': 3, 'y': -1}
        result = export_level(self.level, boundary_padding=0)
        self.assertEqual(len(result['MainPlatform']), 2)
        merged = result['MainPlatform'][0]
        self.assertEqual(surface_count(merged), 3)
        seam = merged['points'][1]
        self.assertEqual(seam['tangentIn'], {'x': -2, 'y': 1})
        self.assertEqual(seam['tangentOut'], {'x': 3, 'y': -1})
        self.assertEqual([p['x'] for p in merged['points'][-2:]], [20, 0])
        self.assertEqual(result['InteractableObject'][0]['properties']['postDestroyRoute'], 'a')
        self.assertEqual(export_level(result, boundary_padding=0), result)

    def test_separate_elevation_and_small_real_gap_not_merged(self):
        self.level['MainPlatform'][1]['points'][0]['y'] += .1
        self.assertEqual(len(export_level(self.level)['MainPlatform']), 3)
        self.setUp()
        for p in self.level['MainPlatform'][1]['points']:
            p['x'] += .001
        self.assertEqual(len(export_level(self.level)['MainPlatform']), 3)

    def test_linear_seam_does_not_activate_old_handles(self):
        p = self.level['MainPlatform'][0]['points'][1]
        p.update(tangentMode='linear', tangentIn={'x': -20, 'y': 8})
        result = export_level(self.level, boundary_padding=0)
        self.assertEqual(result['MainPlatform'][0]['points'][1]['tangentIn'], {'x': 0, 'y': 0})

    def test_all_campaigns_have_common_floor_and_valid_references(self):
        for number in range(1, 51):
            level = build_campaign_level(number)
            exported = export_level(level)
            for marker in ('Start', 'End', 'Top', 'Bottom', 'CheckPoint'):
                self.assertEqual(exported[marker], level[marker])
            main = exported['MainPlatform']
            self.assertEqual(len({p['y'] for s in main for p in s['points'][-2:]}), 1)
            self.assertLess(len(main), len(level['MainPlatform']))
            ids = {s['id'] for s in main}
            for obj in exported['InteractableObject']:
                ref = obj.get('properties', {}).get('postDestroyRoute')
                if ref:
                    self.assertIn(ref, ids)
            self.assertEqual(export_level(exported), exported)

    def test_invalid_closure_is_not_silently_changed(self):
        self.level['MainPlatform'][0]['points'][-1]['x'] = 3
        with self.assertRaises(ValueError):
            export_level(self.level)

    def test_boundary_padding_preserves_route_markers_and_original_curves(self):
        self.level.update(Start={'x': 1, 'y': 3}, End={'x': 29, 'y': -3})
        source = copy.deepcopy(self.level)
        result = export_level(self.level)
        first, last = result['MainPlatform'][0], result['MainPlatform'][-1]
        self.assertEqual(first['points'][0]['x'], -20)
        self.assertEqual(first['points'][-1]['x'], -20)
        self.assertEqual(last['points'][surface_count(last)-1]['x'], 50)
        self.assertEqual(last['points'][-2]['x'], 50)
        self.assertEqual(first['points'][0]['y'], first['points'][1]['y'])
        self.assertEqual(result['Start'], source['Start'])
        self.assertEqual(result['End'], source['End'])
        self.assertEqual(self.level, source)
        self.assertEqual(export_level(result), result)

    def test_single_platform_extends_both_sides_once(self):
        self.level['MainPlatform'] = self.level['MainPlatform'][:1]
        result = export_level(self.level)
        shape = result['MainPlatform'][0]
        self.assertEqual([p['x'] for p in shape['points']], [-20, 0, 10, 30, 30, -20])
        self.assertEqual(surface_count(shape), 4)
        self.assertEqual(export_level(result), result)

    def test_cinder_crown_export_and_preview_keep_authored_markers(self):
        root = Path(__file__).resolve().parent
        source = json.loads((root/'levels/00_cinder_crown.json').read_text(encoding='utf-8'))
        for result in (export_level(source),
                       json.loads((root/'exports/00_cinder_crown.json').read_text(encoding='utf-8')),
                       json.loads((root/'library/previews/00_cinder_crown.json').read_text(encoding='utf-8'))):
            for marker in ('Start', 'End', 'Top', 'Bottom', 'CheckPoint'):
                self.assertEqual(result[marker], source[marker])
            self.assertEqual(result['MainPlatform'][0]['points'][0]['x'], -20)
            self.assertEqual(result['MainPlatform'][-1]['points'][-2]['x'], 327)


if __name__ == '__main__':
    unittest.main()
