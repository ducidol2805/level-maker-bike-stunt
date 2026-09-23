"""Geometry contracts for recovering from a missed spring without respawning."""
import unittest

from bike_stunt.obstacle_library import as_level, build_variant, load_catalog, sample_curve
from bike_stunt.terrain_export import export_level, surface_count


class SpringFallbackTests(unittest.TestCase):
    def variants(self):
        for name in ('spring_high', 'spring_far'):
            family = load_catalog()[name]
            for variant in family['variants']:
                if variant['parameters'].get('fallbackRoute') == 'tunnel':
                    yield family, variant

    def test_generated_springs_have_lower_road_and_safe_exit(self):
        variants = list(self.variants())
        self.assertEqual(len(variants), 19)
        for family, variant in variants:
            for scale in (1, family['_worldScale']):
                with self.subTest(variant=variant['id'], scale=scale):
                    module = build_variant(family, variant, scale=scale)
                    road = module['MainPlatform'][-1]
                    points = road['points'][:surface_count(road)]
                    spring = next(o for o in module['InteractableObject'] if o['type'] == 'SpringObject')
                    if variant['parameters']['platformConnection'] == 'connected':
                        self.assertEqual(points[0]['x'], 0)
                        lip = next(p for p in points if p['x'] == spring['transform']['x'])
                        self.assertEqual(lip['y'], spring['transform']['y']-scale)
                    else:
                        self.assertGreater(points[0]['x'], spring['transform']['x'])
                        self.assertLess(points[0]['y'], spring['transform']['y']-scale)
                    self.assertEqual((points[-1]['x'], points[-1]['y']),
                                     (module['ports']['exit']['x'], module['ports']['exit']['y']))
                    line = sample_curve({'points': points}, 100)
                    self.assertTrue(all(b[0] > a[0] for a, b in zip(line, line[1:])))
                    self.assertLess(max(abs((b[1]-a[1])/(b[0]-a[0]))
                                        for a, b in zip(line, line[1:])), 2)
                    if module['deadzone']:
                        hazard_top = max(p['y'] for zone in module['deadzone'] for p in zone['points'])
                        self.assertLess(hazard_top, min(y for _, y in line)-scale)
                    cp = module['checkpointCandidates'][0]
                    self.assertGreater(cp['x'], points[-2]['x'])
                    self.assertEqual(cp['y'], points[-1]['y']+scale)

    def test_tunnel_has_clearance_along_curves_and_solid_roof(self):
        for family, variant in self.variants():
            with self.subTest(variant=variant['id']):
                module = build_variant(family, variant)
                road = module['MainPlatform'][-1]
                roof = module['FreePlatform'][0]
                count = roof['metadata']['drivingSurfaceCount']
                left, right = roof['points'][0]['x'], roof['points'][count-1]['x']
                floor = sample_curve({'points': [p for p in road['points'][:surface_count(road)]
                                                 if left <= p['x'] <= right]}, 100)
                ceiling = list(reversed(sample_curve({'points': roof['points'][count:]}, 100)))
                self.assertEqual(len(floor), len(ceiling))
                for lower, upper in zip(floor, ceiling):
                    self.assertAlmostEqual(lower[0], upper[0], places=5)
                    self.assertAlmostEqual(upper[1]-lower[1], 3, places=5)
                upper_surface = sample_curve({'points': roof['points'][:count]}, 100)
                self.assertGreaterEqual(min(y for _, y in upper_surface),
                                        max(y for _, y in ceiling)+2-1e-5)

    def test_first_five_separated_last_five_connected_in_both_families(self):
        for name in ('spring_high', 'spring_far'):
            family = load_catalog()[name]
            for index, variant in enumerate(family['variants']):
                with self.subTest(variant=variant['id']):
                    module = build_variant(family, variant, scale=family['_worldScale'])
                    if index < 5:
                        self.assertEqual(len(module['MainPlatform']), 2)
                        approach, road = module['MainPlatform']
                        lip = approach['points'][surface_count(approach)-1]
                        start = road['points'][0]
                        self.assertGreater(start['x'], lip['x'])
                        self.assertEqual(len(module['deadzone']), 1)
                        zone = module['deadzone'][0]['points']
                        self.assertLessEqual(min(p['x'] for p in zone), lip['x'])
                        self.assertGreaterEqual(max(p['x'] for p in zone), start['x'])
                        self.assertLessEqual(max(p['x'] for p in zone)-start['x'], 1.01)
                    else:
                        self.assertEqual(len(module['MainPlatform']), 1)
                        road = module['MainPlatform'][0]
                        points = road['points'][:surface_count(road)]
                        lip_x = variant['parameters']['approach']*family['_worldScale']
                        self.assertEqual(sum(p['x'] == lip_x for p in points), 1)
                        lip = next(p for p in points if p['x'] == lip_x)
                        self.assertLess(lip['tangentIn']['x'], 0)
                        self.assertGreater(lip['tangentOut']['x'], 0)
                        self.assertEqual(module['deadzone'], [])
                        exported = export_level(as_level([module], variant['id'], variant['name']))
                        self.assertEqual(len(exported['MainPlatform']), 1)
                        self.assertEqual(exported['deadzone'], [])

    def test_export_keeps_tunnel_roof_coins_and_exit(self):
        for family, variant in self.variants():
            with self.subTest(variant=variant['id']):
                module = build_variant(family, variant, scale=family['_worldScale'])
                level = as_level([module], variant['id'], variant['name'])
                exported = export_level(level)
                self.assertEqual(exported['FreePlatform'], level['FreePlatform'])
                self.assertEqual(exported['End'], level['End'])
                self.assertEqual(exported['deadzone'], level['deadzone'])
                self.assertEqual(export_level(exported), exported)
                self.assertEqual(sum(o['type'] == 'coin' for o in exported['InteractableObject']), 3)
