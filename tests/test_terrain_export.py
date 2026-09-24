"""Post-merge tangent continuity at flat-to-curve joins."""
import copy
import math
import unittest

from obstacle_library import ground, vertex
from terrain_export import export_level, normalize_main_platform, surface_count


class TerrainTangentTests(unittest.TestCase):
    def test_merge_rechecks_previous_seam_without_moving_bottoms(self):
        left = ground('a', [vertex(0, 0), vertex(6, 4)], depth=8)
        middle = ground('b', [vertex(6, 4), vertex(12, 4)], depth=2)
        right = ground('c', [vertex(12, 4), vertex(18, 4)], depth=12)
        source = {'MainPlatform': [left, middle, right],
                  'InteractableObject': [{'properties': {'postDestroyRoute': 'c'}}]}
        result = export_level(source, boundary_padding=0)
        self.assertEqual(len(result['MainPlatform']), 1)
        self.assertEqual(result['MainPlatform'][0]['points'][-2:],
                         [right['points'][-2], left['points'][-1]])
        self.assertEqual(result['MainPlatform'][0]['metadata']['sourceShapeIds'], ['a', 'b', 'c'])
        self.assertEqual(result['InteractableObject'][0]['properties']['postDestroyRoute'], 'a')
        self.assertEqual(export_level(result, boundary_padding=0), result)

    def test_merge_does_not_align_different_bottom_heights(self):
        left = ground('left', [vertex(0, 0), vertex(6, 0)], depth=8)
        right = ground('right', [vertex(6, 0), vertex(12, 0)], depth=12)
        separate = ground('separate', [vertex(20, 0), vertex(26, 0)], depth=20)
        source = {'MainPlatform': [left, right, separate]}
        self.assertEqual(normalize_main_platform(source), source)
        result = export_level(source, boundary_padding=0)
        self.assertEqual(len(result['MainPlatform']), 2)
        self.assertEqual(result['MainPlatform'][0]['points'][-2:],
                         [right['points'][-2], left['points'][-1]])
        self.assertEqual(result['MainPlatform'][1]['points'][-2:], separate['points'][-2:])
        self.assertEqual(export_level(result, boundary_padding=0), result)

    def test_level_08_terrain_merges_into_both_spring_approaches(self):
        import json
        from bike_stunt.campaign import build_authored_level
        from bike_stunt.obstacle_library import ROOT

        recipes = json.loads((ROOT / 'library/campaign_recipes.json').read_text(encoding='utf-8'))
        recipe = next(item for item in recipes['maps'] if item['number'] == 8)
        source = build_authored_level(recipe)
        exported = export_level(source)
        for terrain, spring in (('s02_7_terrain', 's03_83_spring_approach'),
                                ('s07_7_terrain', 's08_83_spring_approach')):
            merged = next(shape for shape in exported['MainPlatform']
                          if terrain in shape.get('metadata', {}).get('sourceShapeIds', []))
            self.assertIn(spring, merged['metadata']['sourceShapeIds'])
            labels = {label['id'] for label in merged['metadata']['sourceShapeLabels']}
            self.assertTrue({terrain, spring}.issubset(labels))
        self.assertEqual(export_level(exported), exported)
        self.assertEqual(exported['FreePlatform'], source['FreePlatform'])
        self.assertEqual(exported['deadzone'], source['deadzone'])

    def test_edited_aligned_bottoms_merge_and_keep_outer_corners(self):
        for free_left in (False, True):
            with self.subTest(free_left=free_left):
                left = ground('before', [vertex(0, 0), vertex(6, 0, incoming=(-2, 0))])
                right = ground('spring', [vertex(6, 0, outgoing=(2, 0)), vertex(12, 0)])
                edited = left if free_left else right
                edited['metadata']['freeBottomCorners'] = True
                for p in edited['points'][-2:]:
                    p['y'] = -6
                source = {'MainPlatform': [left, right],
                          'InteractableObject': [{'properties': {'postDestroyRoute': 'spring'}}]}
                original = copy.deepcopy(source)
                result = export_level(source, boundary_padding=0)
                self.assertEqual(source, original)
                self.assertEqual(len(result['MainPlatform']), 1)
                merged = result['MainPlatform'][0]
                self.assertEqual(merged['points'][-2:], [right['points'][-2], left['points'][-1]])
                self.assertTrue(merged['metadata']['freeBottomCorners'])
                self.assertEqual(merged['metadata']['sourceShapeIds'], ['before', 'spring'])
                self.assertEqual(merged['points'][1]['tangentMode'], 'continuous')
                self.assertEqual(result['InteractableObject'][0]['properties']['postDestroyRoute'], 'before')
                self.assertEqual(export_level(result, boundary_padding=0), result)

    def test_curved_underside_merges_but_real_gap_stays_open(self):
        for gap, curve in ((1, 0), (0, 1)):
            with self.subTest(gap=gap, curve=curve):
                left = ground('left', [vertex(0, 0), vertex(6, 0)])
                right = ground('right', [vertex(6+gap, 0), vertex(12, 0)])
                right['metadata']['freeBottomCorners'] = True
                right['points'][-1].update(tangentMode='broken', tangentIn={'x': curve, 'y': 0})
                result = export_level({'MainPlatform': [left, right]}, boundary_padding=0)
                self.assertEqual(len(result['MainPlatform']), 2 if gap else 1)
                self.assertEqual(export_level(result, boundary_padding=0), result)

    def test_overhanging_ground_merges_on_contact(self):
        left = ground('free', [vertex(0, 0, mode='linear'), vertex(6, 0, mode='linear')])
        left['metadata']['freeBottomCorners'] = True
        left['points'][-2].update(x=8, y=2)
        left['points'][-1].update(x=-5, y=-1)
        right = ground('generated', [vertex(6, 0, mode='linear'), vertex(12, 0, mode='linear')])
        result = export_level({'MainPlatform': [left, right]}, boundary_padding=0)
        self.assertEqual(len(result['MainPlatform']), 1)
        self.assertEqual(result['MainPlatform'][0]['metadata']['sourceShapeIds'], ['free', 'generated'])
        self.assertEqual(export_level(result, boundary_padding=0), result)

    def test_merges_smooth_endpoint_join_as_continuous(self):
        left = [vertex(0, 0, mode='linear'), vertex(6, 0, incoming=(-6, 0))]
        right = [vertex(6, 0, outgoing=(4, 0)), vertex(12, 1, incoming=(-2, 0))]
        result = export_level({'MainPlatform': [ground('a', left), ground('b', right)]},
                              boundary_padding=0)
        join = result['MainPlatform'][0]['points'][1]
        self.assertEqual(join['tangentMode'], 'continuous')
        self.assertEqual(join['tangentIn'], {'x': -6, 'y': 0})
        self.assertEqual(join['tangentOut'], {'x': 4, 'y': 0})

    def test_extends_either_straight_side_after_merge(self):
        for straight_first in (True, False):
            with self.subTest(straight_first=straight_first):
                if straight_first:
                    left = [vertex(0,0,mode='linear'),vertex(6,0,mode='linear')]
                    right = [vertex(6,0,outgoing=(2,0)),vertex(12,4,incoming=(-2,0))]
                else:
                    left = [vertex(0,4,outgoing=(2,0)),vertex(6,0,incoming=(-2,0))]
                    right = [vertex(6,0,mode='linear'),vertex(12,0,mode='linear')]
                source = {'MainPlatform':[ground('a',left),ground('b',right)]}
                original = copy.deepcopy(source)
                result = export_level(source,boundary_padding=0)
                self.assertEqual(source,original)
                self.assertEqual(len(result['MainPlatform']),1)
                points = result['MainPlatform'][0]['points']
                join = points[1]
                self.assertEqual((join['x'],join['y']),(6,0))
                self.assertEqual(join['tangentIn'],{'x':-2,'y':0})
                self.assertEqual(join['tangentOut'],{'x':2,'y':0})
                self.assertEqual(join['tangentMode'],'continuous')
                self.assertEqual(points[0],original['MainPlatform'][0]['points'][0])
                self.assertEqual(points[2],original['MainPlatform'][1]['points'][1])
                self.assertEqual(export_level(result,boundary_padding=0),result)

    def test_short_sloped_straight_gets_bounded_opposite_handle(self):
        points = [vertex(0,0,mode='linear'),vertex(1,1,outgoing=(3,3)),
                  vertex(6,8,incoming=(-2,-1))]
        result = export_level({'MainPlatform':[ground('a',points)]},boundary_padding=0)
        join = result['MainPlatform'][0]['points'][1]
        a,b = join['tangentIn'],join['tangentOut']
        self.assertAlmostEqual(a['x']*b['y']-a['y']*b['x'],0)
        self.assertLess(a['x']*b['x']+a['y']*b['y'],0)
        self.assertAlmostEqual(math.hypot(a['x'],a['y']),math.sqrt(2)/3)

    def test_preserves_corners_and_nonstraight_segments(self):
        for corner, far_curve, angled in ((True,False,False),(False,True,False),(False,False,True)):
            with self.subTest(corner=corner,far_curve=far_curve,angled=angled):
                first = vertex(0,0,outgoing=(1,1) if far_curve else (0,0))
                joint = vertex(6,0,outgoing=(2,1) if angled else (2,0))
                joint['corner'] = corner
                points = [first,joint,vertex(12,4,incoming=(-2,0))]
                source = {'MainPlatform':[ground('a',points)]}
                result = export_level(source,boundary_padding=0)
                self.assertEqual(result['MainPlatform'][0]['points'][1],joint)

    def test_padding_and_underside_are_stable_on_reexport(self):
        source = {'MainPlatform':[ground('a',[vertex(0,0,outgoing=(2,0)),
                                              vertex(8,0,incoming=(-2,0))])],
                  'Start':{'x':1,'y':1},'End':{'x':7,'y':1}}
        result = export_level(source)
        self.assertEqual(export_level(result),result)
        self.assertEqual(result['Start'],source['Start'])
        self.assertEqual(result['End'],source['End'])
        shape = result['MainPlatform'][0]
        for point in shape['points'][surface_count(shape):]:
            self.assertEqual(point['tangentMode'],'linear')
            self.assertEqual(point['tangentIn'],{'x':0,'y':0})
            self.assertEqual(point['tangentOut'],{'x':0,'y':0})


if __name__ == '__main__':
    unittest.main()
