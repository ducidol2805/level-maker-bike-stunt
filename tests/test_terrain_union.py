"""Ground contact/overlap regressions, including the map 02 recovery seam."""
import copy
import json
import unittest

from bike_stunt.obstacle_library import ROOT, ground, vertex, sample_curve
from bike_stunt.terrain_export import export_level, can_merge_terrain, surface_count
from bike_stunt.terrain_union import contains, segments


def rectangle(name, x0, y0, x1, y1):
    return ground(name, [vertex(x0, y1, mode='linear'), vertex(x1, y1, mode='linear')],
                  depth=y1-y0)


def area(shape):
    points = sample_curve(shape, 50)
    return abs(sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(points, points[1:])))/2


class GroundUnionTests(unittest.TestCase):
    def merge(self, shapes):
        source = {'MainPlatform': shapes, 'InteractableObject': [
            {'properties': {'postDestroyRoute': shapes[-1]['id']}}]}
        original = copy.deepcopy(source)
        result = export_level(source, boundary_padding=0)
        self.assertEqual(source, original)
        self.assertEqual(len(result['MainPlatform']), 1)
        self.assertEqual(export_level(result, boundary_padding=0), result)
        merged = result['MainPlatform'][0]
        self.assertEqual(result['InteractableObject'][0]['properties']['postDestroyRoute'], merged['id'])
        self.assertEqual(set(merged['metadata']['sourceShapeIds']), {s['id'] for s in shapes})
        surface_count(merged)
        return merged

    def test_partial_overlap_preserves_union_area(self):
        a, b = rectangle('a', 0, -4, 6, 0), rectangle('b', 4, -2, 10, 2)
        merged = self.merge([a, b])
        self.assertAlmostEqual(area(merged), 44, places=6)
        self.assertFalse(contains((1, 1), segments(merged)))

    def test_containment_and_identical_ground(self):
        a = rectangle('outer', 0, -4, 10, 0)
        for b in (rectangle('inner', 2, -3, 8, -1), rectangle('same', 0, -4, 10, 0)):
            with self.subTest(other=b['id']):
                merged = self.merge([a, b])
                self.assertAlmostEqual(area(merged), 40, places=6)

    def test_side_contact_at_different_road_heights(self):
        merged = self.merge([rectangle('a', 0, -4, 6, 0), rectangle('b', 6, -2, 12, 2)])
        self.assertAlmostEqual(area(merged), 48, places=6)

    def test_point_contact(self):
        merged = self.merge([rectangle('a', 0, -4, 6, 0), rectangle('b', 6, 0, 12, 4)])
        self.assertAlmostEqual(area(merged), 48, places=6)

    def test_nonadjacent_transitive_contacts(self):
        # B sorts between A and C but only C initially connects the two levels.
        shapes = [rectangle('a', 0, -4, 8, 0), rectangle('b', 2, 2, 6, 4),
                  rectangle('c', 6, -2, 10, 3)]
        self.merge(shapes)

    def test_x_overlap_without_ground_contact_stays_separate(self):
        a, b = rectangle('a', 0, -4, 6, 0), rectangle('b', 2, 1, 8, 4)
        self.assertFalse(can_merge_terrain(a, b))
        self.assertEqual(len(export_level({'MainPlatform': [a, b]}, boundary_padding=0)['MainPlatform']), 2)

    def test_uncut_bezier_surface_keeps_handles(self):
        a = ground('curve', [vertex(0, 0, outgoing=(2, 3)), vertex(6, 0, incoming=(-2, 3))])
        a['points'][0]['corner'] = True
        b = rectangle('inside', 1, -3, 5, -1)
        merged = self.merge([a, b])
        self.assertEqual(merged['points'][0]['tangentOut'], a['points'][0]['tangentOut'])
        self.assertEqual(merged['points'][1]['tangentIn'], a['points'][1]['tangentIn'])
        self.assertTrue(merged['points'][0]['corner'])

    def test_crossing_curves_keep_exposed_cubic_segments(self):
        a = ground('curve', [vertex(0, 0, outgoing=(2, 3)), vertex(6, 0, incoming=(-2, 3))])
        b = rectangle('crossing', 3, -3, 8, 1)
        merged = self.merge([a, b])
        handle = merged['points'][0]['tangentOut']
        self.assertAlmostEqual(handle['y']/handle['x'], 1.5)
        self.assertGreater(handle['x'], 0)
        self.assertLess(handle['x'], 2)
        self.assertTrue(any(abs(p['tangentIn']['y']) > .01 for p in merged['points']))
        # Union must retain exactly the same filled region away from the edge.
        edges = segments(merged)
        for x in (.5, 2, 4, 5, 7):
            for y in (-2, .5, 1.5, 3):
                expected = contains((x, y), segments(a)) or contains((x, y), segments(b))
                self.assertEqual(contains((x, y), edges), expected, (x, y))

    def test_map_02_recovery_and_fallback_share_one_ground(self):
        from bike_stunt.campaign import build_authored_level, validate_authored
        recipes = json.loads((ROOT/'library/campaign_recipes.json').read_text(encoding='utf-8'))
        source = build_authored_level(next(r for r in recipes['maps'] if r['number'] == 2))
        result = export_level(source)
        validate_authored(source, result)
        ids = {'s04_82_spring_lower_recovery', 's05_68_safe_fallback'}
        self.assertTrue(any(ids <= set(s.get('metadata', {}).get('sourceShapeIds', []))
                            for s in result['MainPlatform']))
        self.assertEqual(result['Start'], source['Start'])
        self.assertEqual(result['End'], source['End'])


if __name__ == '__main__':
    unittest.main()
