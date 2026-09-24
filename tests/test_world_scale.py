"""The Library and saved campaign must share one 2x world-space contract."""
import json
import unittest

from generate_campaign import build_authored_level
from obstacle_library import ROOT, build_variant, ground, load_catalog, sample_curve, vertex
from spring_object import spring_trajectory
from terrain_export import export_level, surface_count
from world_scale import scale_world_data


class WorldScaleTests(unittest.TestCase):
    def test_every_library_variant_scales_points_handles_and_coins(self):
        families = load_catalog()
        for family in families.values():
            for variant in family['variants']:
                with self.subTest(variant=variant['id']):
                    raw = build_variant(family, variant)
                    scaled = build_variant(family, variant, scale=family['_worldScale'])
                    self.assertEqual(scaled['worldScale'], 2)
                    for shape in scaled['MainPlatform']:
                        surface = shape['points'][:surface_count(shape)]
                        lowest = min(y for _, y in sample_curve({'points': surface}, 100))
                        self.assertGreaterEqual(lowest-max(p['y'] for p in shape['points'][-2:]), 15-1e-8)
                    for group in ('MainPlatform', 'RampPlatform', 'deadzone', 'InteractableObject'):
                        for source, output in zip(raw[group], scaled[group]):
                            if 'points' in source:
                                for point, changed in zip(source['points'], output['points']):
                                    for key in ('x', 'y'):
                                        self.assertEqual(changed[key], 2*point[key])
                                    for handle in ('tangentIn', 'tangentOut'):
                                        for key in ('x', 'y'):
                                            self.assertEqual(changed[handle][key], 2*point[handle][key])
                            if 'transform' in source:
                                for key in ('x', 'y'):
                                    self.assertEqual(output['transform'][key], 2*source['transform'][key])
                    self.assertEqual(sum(o['type']=='coin' for o in scaled['InteractableObject']), 3)

    def test_ground_depth_accounts_for_curves_below_their_knots(self):
        shape = ground('dip', [vertex(0, 0, outgoing=(2, -3)),
                               vertex(6, 0, incoming=(-2, -3))])
        surface = shape['points'][:surface_count(shape)]
        lowest = min(y for _, y in sample_curve({'points': surface}, 100))
        self.assertGreaterEqual(lowest-max(p['y'] for p in shape['points'][-2:]), 7.5)

    def test_saved_map_is_scaled_once_and_spring_arc_keeps_shape(self):
        families = load_catalog()
        recipes = json.loads((ROOT/'library/campaign_recipes.json').read_text(encoding='utf-8'))['maps']
        recipe = recipes[4]  # Map 05 contains a spring.
        source = export_level(build_authored_level(recipe, families))
        saved = json.loads((ROOT/'levels/campaign_05.json').read_text(encoding='utf-8'))
        saved_copy = dict(saved)
        saved_copy['map'] = dict(saved['map'])
        saved_copy['map'].pop('worldScale')
        saved_copy['design'] = dict(saved['design'])
        saved_copy['design'].pop('validationReport')
        self.assertEqual(saved_copy, scale_world_data(source, 2))
        self.assertEqual(saved['terrainExport']['boundaryPadding'], 40)
        self.assertEqual(export_level(saved), saved)
        before = next(o for o in source['InteractableObject'] if o['type']=='SpringObject')
        after = next(o for o in saved['InteractableObject'] if o['id']==before['id'])
        for (x, y), (sx, sy) in zip(spring_trajectory(before, 80), spring_trajectory(after, 80)):
            self.assertAlmostEqual(sx, 2*x)
            self.assertAlmostEqual(sy, 2*y)


if __name__ == '__main__':
    unittest.main()
