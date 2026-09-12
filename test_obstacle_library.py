import hashlib
import json
import math
import unittest
from obstacle_library import load_catalog, build_variant, sample_curve, build_campaign_level


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.families=load_catalog()
        cls.modules=[build_variant(f,v) for f in cls.families.values() for v in f['variants']]

    def test_counts_and_unique_geometry(self):
        self.assertEqual(len(self.families),16)
        self.assertEqual(len({m['id'] for m in self.modules}),80)
        for family in self.families.values():
            self.assertTrue(5<=len(family['variants'])<=10)
            geometry=[]
            for v in family['variants']:
                m=build_variant(family,v)
                geometry.append(json.dumps([m[g] for g in ('MainPlatform','RampPlatform','InteractableObject')],sort_keys=True))
            self.assertEqual(len(set(geometry)),len(geometry))

    def test_closed_ground_open_ramps(self):
        for m in self.modules:
            for group in ('MainPlatform','RampPlatform','InteractableObject','deadzone'):
                for shape in m[group]:
                    line=sample_curve(shape)
                    self.assertTrue(all(math.isfinite(a) and math.isfinite(b) for a,b in line),m['id'])
                    if group in ('MainPlatform','deadzone'):
                        self.assertTrue(shape['closed'])
                        self.assertEqual(line[0],line[-1])
                    else:
                        self.assertFalse(shape['closed'])

    def test_loop_c1_join(self):
        for m in (m for m in self.modules if m['type']=='explosive_loop'):
            a=m['InteractableObject'][0]['points'][-1]
            b=m['RampPlatform'][0]['points'][0]
            self.assertEqual((a['x'],a['y']),(b['x'],b['y']))
            for axis in ('x','y'):
                self.assertAlmostEqual(-a['tangentIn'][axis],b['tangentOut'][axis])

    def test_bezier_not_polyline(self):
        m=next(m for m in self.modules if m['id']=='hill.round_roller')
        pts=m['MainPlatform'][0]['points']
        x,y=sample_curve({'points':pts[:5],'closed':False})[6]
        self.assertGreater(abs(y-x/5),.01)

    def test_jump_recovery_checkpoint(self):
        for m in self.modules:
            u=m['jumpUnit']
            if u:
                self.assertGreaterEqual(u['recovery'][1]-u['recovery'][0],10)
                self.assertGreater(u['flight'][1],u['flight'][0])
                for cp in m['checkpointCandidates']:
                    self.assertGreater(cp['x'],u['landing'][1])
                    self.assertLess(cp['x'],u['recovery'][1])

    def test_campaign_diversity_and_contract(self):
        hashes=set()
        for n in range(1,51):
            level=build_campaign_level(n)
            self.assertEqual(sum(o['type']=='coin' for o in level['InteractableObject']),10)
            threshold=max(8,(level['End']['x']-level['Start']['x'])*.08)
            for cp in level['CheckPoint']:
                self.assertGreaterEqual(level['End']['x']-cp['transform']['x'],threshold)
            obs=level['design']['obstacles']
            for a,b in zip(obs,obs[1:]):
                self.assertEqual(a['ports']['exit'],b['ports']['entry'])
            geometry=json.dumps([level[g] for g in ('MainPlatform','RampPlatform')],sort_keys=True)
            hashes.add(hashlib.sha256(geometry.encode()).hexdigest())
        self.assertEqual(len(hashes),50)
        self.assertEqual(build_campaign_level(23),build_campaign_level(23))


if __name__=='__main__':
    unittest.main()
