"""Library-owned coin placement and its campaign/demo integration."""
import copy
import json
import math
import unittest

from generate_campaign import build_authored_level, validate_authored
from obstacle_library import (ROOT, as_level, assign_module_coins, build_variant,
                              load_catalog, place)
from terrain_export import export_level, surface_count


def coins(module):
    return [o for o in module['InteractableObject'] if o['type']=='coin']


class LibraryCoinTests(unittest.TestCase):
    def test_all_variants_own_three_stable_coins_in_both_assembly_modes(self):
        for family in load_catalog().values():
            for variant in family['variants']:
                with self.subTest(variant=variant['id']):
                    module = build_variant(family,variant)
                    original = copy.deepcopy(coins(module))
                    self.assertEqual(len(original),3)
                    self.assertEqual(len({c['id'] for c in original}),3)
                    assign_module_coins(module)
                    self.assertEqual(coins(module),original)
                    for preview in (True,False):
                        level = as_level([module],variant['id'],variant['name'],preview=preview)
                        self.assertEqual(coins(level),original)
                        self.assertEqual(level['design']['coinCount'],3)

    def test_placement_translates_coins_and_ring_references(self):
        family = load_catalog()['explosive_loop']
        for variant in family['variants']:
            module = build_variant(family,variant)
            original = copy.deepcopy(module)
            placed = place(module,40,-12,'demo')
            self.assertEqual(module,original)
            for local,world in zip(coins(module),coins(placed)):
                self.assertEqual(world['id'],'demo_'+local['id'])
                self.assertAlmostEqual(world['transform']['x'],local['transform']['x']+40)
                self.assertAlmostEqual(world['transform']['y'],local['transform']['y']-12)
                ring = next(r for r in placed['RampPlatform']
                            if r['id']==world['properties']['supportingRingId'])
                right,left = ring['points'][0],ring['points'][-1]
                cx,cy = (right['x']+left['x'])/2,(right['y']+left['y'])/2
                rx = abs(right['x']-left['x'])/2
                ry = max(p['y'] for p in ring['points'])-cy
                p = world['transform']
                self.assertLess(((p['x']-cx)/rx)**2+((p['y']-cy)/ry)**2,1)

    def test_platform_coins_have_actual_supporting_segments(self):
        family = load_catalog()['platform']
        for variant in family['variants']:
            module = build_variant(family,variant)
            for coin in coins(module):
                x = coin['transform']['x']
                self.assertTrue(any(shape['points'][0]['x']<=x<=shape['points'][surface_count(shape)-1]['x']
                                    for shape in module['RampPlatform']))
            # Closing a platform must not put rewards on sides or undersides.
            open_copy = copy.deepcopy(module)
            for shape in open_copy['RampPlatform']:
                shape['points'] = shape['points'][:surface_count(shape)]
                shape['closed'] = False
                shape['metadata'].pop('drivingSurfaceCount')
            assign_module_coins(open_copy)
            self.assertEqual(coins(module), coins(open_copy))


class CampaignCoinTests(unittest.TestCase):
    def test_campaigns_preserve_three_coins_per_instance(self):
        families = load_catalog()
        recipes = json.loads((ROOT/'library/campaign_recipes.json').read_text(encoding='utf-8'))['maps']
        for recipe in recipes:
            with self.subTest(map=recipe['name']):
                source = build_authored_level(recipe,families)
                exported = export_level(source)
                validate_authored(source,exported)
                self.assertEqual(coins(source),coins(exported))
                expected = 3*len(source['design']['sequence'])
                self.assertEqual(len(coins(source)),expected)
                self.assertEqual(source['design']['coinCount'],expected)
                if 'lengthPlan' in recipe:
                    actual_length = exported['End']['x']-exported['Start']['x']
                    self.assertAlmostEqual(actual_length,recipe['lengthPlan']['baselineLength']*1.5,places=5)
                    self.assertGreaterEqual(recipe['lengthPlan']['addedChallengeCount'],2)
                    if recipe['number']%10 == 0:
                        self.assertEqual(source['design']['difficulty'],'extreme')
                for stage in source['design']['sequence']:
                    selected = [c for c in coins(source) if c['properties']['beatId']==stage['beatId']]
                    self.assertEqual(len(selected),3)
                    self.assertTrue(all(c['id'].startswith(stage['instanceId']+'_coin_') for c in selected))
                    self.assertTrue(all(c['properties']['sourceVariant']==stage['variant'] for c in selected))


if __name__ == '__main__':
    unittest.main()
