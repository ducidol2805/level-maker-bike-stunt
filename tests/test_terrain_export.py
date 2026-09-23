"""Post-merge tangent continuity at flat-to-curve joins."""
import copy
import math
import unittest

from obstacle_library import ground, vertex
from terrain_export import export_level, surface_count


class TerrainTangentTests(unittest.TestCase):
    def test_free_bottom_corners_survive_export_without_merging(self):
        left = ground('free', [vertex(0, 0, mode='linear'), vertex(6, 0, mode='linear')])
        left['metadata']['freeBottomCorners'] = True
        left['points'][-2].update(x=8, y=2)
        left['points'][-1].update(x=-5, y=-1)
        right = ground('generated', [vertex(6, 0, mode='linear'), vertex(12, 0, mode='linear')])
        result = export_level({'MainPlatform': [left, right]}, boundary_padding=0)
        self.assertEqual(len(result['MainPlatform']), 2)
        self.assertEqual([(p['x'], p['y']) for p in result['MainPlatform'][0]['points'][-2:]],
                         [(8, 2), (-5, -1)])
        self.assertEqual([p['y'] for p in result['MainPlatform'][1]['points'][-2:]], [-3, -3])

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
