"""Unity import regressions for short ground-union seams."""
import copy
import json
import unittest

from bike_stunt.obstacle_library import ROOT, vertex
from bike_stunt.spline_spacing import collapse_union_edges, validate_spline_spacing
from bike_stunt.terrain_export import export_level, surface_count
from bike_stunt.world_scale import scale_world_data


class SplineSpacingTests(unittest.TestCase):
    def test_tiny_underside_step_is_collapsed_without_changing_road(self):
        shape = {'id': 'union', 'closed': True, 'points': [
            vertex(0, 0, outgoing=(2, 1)), vertex(10, 2, incoming=(-2, -1)),
            vertex(10, -7.501803), vertex(5, -7.501803),
            vertex(5, -7.5), vertex(0, -7.5)],
            'metadata': {'terrainUnion': True, 'freeBottomCorners': True,
                         'drivingSurfaceCount': 2}}
        road = copy.deepcopy(shape['points'][:2])
        collapse_union_edges(shape)
        self.assertEqual(len(shape['points']), 5)
        self.assertEqual(shape['points'][:2], road)
        self.assertEqual(surface_count(shape), 2)
        level = {'MainPlatform': [shape]}
        validate_spline_spacing(level)
        self.assertEqual(export_level(export_level(level, boundary_padding=0), boundary_padding=0),
                         export_level(level, boundary_padding=0))

    def test_closed_seam_keeps_first_road_knot_and_surface_count(self):
        shape = {'id': 'seam', 'closed': True, 'points': [
            vertex(0, 0, outgoing=(2, 1)), vertex(8, 2), vertex(8, -4),
            vertex(.001, -.001, incoming=(-1, -2))],
            'metadata': {'terrainUnion': True, 'freeBottomCorners': True,
                         'drivingSurfaceCount': 2}}
        collapse_union_edges(shape)
        self.assertEqual(len(shape['points']), 3)
        self.assertEqual((shape['points'][0]['x'], shape['points'][0]['y']), (0, 0))
        self.assertEqual(shape['points'][0]['tangentOut'], {'x': 2, 'y': 1})
        self.assertEqual(surface_count(shape), 2)

    def test_validator_reports_group_shape_and_indices(self):
        shape = {'id': 'bad', 'closed': False, 'points': [vertex(0, 0), vertex(.003606, 0)]}
        with self.assertRaisesRegex(ValueError, r'MainPlatform/bad: points 0/1 too close'):
            validate_spline_spacing({'MainPlatform': [shape]})

    def test_validator_checks_closed_seam_but_not_open_endpoints(self):
        shape = {'id': 'seam', 'closed': False,
                 'points': [vertex(0, 0), vertex(1, 1), vertex(.001, 0)]}
        validate_spline_spacing({'RampPlatform': [shape]})
        shape['closed'] = True
        with self.assertRaisesRegex(ValueError, r'points 2/0 too close'):
            validate_spline_spacing({'RampPlatform': [shape]})

    def test_scaled_cleanup_uses_same_threshold(self):
        shape = {'id': 'scale', 'closed': True, 'points': [vertex(0, 0), vertex(10, 0),
            vertex(10, -4), vertex(5, -4), vertex(5, -3.96), vertex(0, -4)],
            'metadata': {'terrainUnion': True, 'freeBottomCorners': True,
                         'drivingSurfaceCount': 2}}
        raw = export_level({'MainPlatform': [shape]}, boundary_padding=0)
        scaled = scale_world_data(raw, 2)
        scaled['map'] = {'worldScale': 2}
        self.assertEqual(export_level(scaled, boundary_padding=0), scaled)

    def test_maps_17_and_29_have_safe_union_spacing(self):
        from bike_stunt.campaign import build_authored_level, validate_authored
        recipes = json.loads((ROOT/'library/campaign_recipes.json').read_text(encoding='utf-8'))['maps']
        for number in (17, 29):
            with self.subTest(map=number):
                source = build_authored_level(next(r for r in recipes if r['number'] == number))
                exported = export_level(source)
                validate_authored(source, exported)
                scaled = scale_world_data(exported, 2)
                scaled['map']['worldScale'] = 2
                validate_spline_spacing(scaled)
                self.assertEqual(export_level(scaled), scaled)


if __name__ == '__main__':
    unittest.main()
