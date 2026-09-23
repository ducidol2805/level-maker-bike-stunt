"""Every family lowers its hazards once without changing any other geometry."""
import copy
import unittest
from unittest.mock import patch

from obstacle_library import as_level, build_variant, load_catalog, place
from terrain_export import export_level


class DeadzoneOffsetTests(unittest.TestCase):
    def test_all_variants_lower_only_deadzone_y_by_two(self):
        zone_count = 0
        for family in load_catalog().values():
            for variant in family['variants']:
                with self.subTest(variant=variant['id']):
                    with patch('obstacle_library.DEADZONE_Y_OFFSET', 0):
                        original = build_variant(family, variant)
                    expected = copy.deepcopy(original)
                    for zone in expected['deadzone']:
                        zone_count += 1
                        for point in zone['points']:
                            point['y'] -= 2
                    actual = build_variant(family, variant)
                    self.assertEqual(actual, expected)
                    placed = place(actual, 20, -7, 'offset_test')
                    for local, world in zip(actual['deadzone'], placed['deadzone']):
                        for a, b in zip(local['points'], world['points']):
                            self.assertAlmostEqual(b['x'], a['x']+20)
                            self.assertAlmostEqual(b['y'], a['y']-7)
                    level = as_level([placed], variant['id'], variant['name'])
                    exported = export_level(level)
                    self.assertEqual(exported['deadzone'], placed['deadzone'])
                    self.assertEqual(export_level(exported)['deadzone'], exported['deadzone'])
        self.assertGreater(zone_count, 0)
