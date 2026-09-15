"""Keep jump families visually and structurally distinct in the catalog."""
import unittest

from obstacle_library import build_variant, load_catalog, sample_curve
from terrain_export import surface_count


class JumpFamilyGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.families = load_catalog()

    def module(self, family_name, variant_index=0):
        family = self.families[family_name]
        return build_variant(family, family["variants"][variant_index])

    @staticmethod
    def surface(shape):
        return shape["points"][:surface_count(shape)]

    def test_each_family_has_a_declared_unique_profile(self):
        expected = {
            "kicker": "short_hard_lip",
            "curved_ramp": "compression_scoop",
            "gap": "takeoff_and_landing_banks",
            "step_up": "elevated_landing_shelf",
            "step_down": "descending_landing_shelf",
        }
        for name, profile in expected.items():
            with self.subTest(name=name):
                self.assertEqual(self.families[name]["geometryProfile"], profile)
                self.assertEqual(self.module(name)["geometryProfile"], profile)

    def test_family_geometry_has_the_expected_signature(self):
        kicker = self.module("kicker")
        scoop = self.module("curved_ramp")
        gap = self.module("gap")
        step_up = self.module("step_up")
        step_down = self.module("step_down")

        self.assertTrue(self.surface(kicker["MainPlatform"][0])[-1]["corner"])
        self.assertLess(min(y for _, y in sample_curve({"points": self.surface(scoop["MainPlatform"][0])})), -.1)
        self.assertEqual(gap["MainPlatform"][1]["id"], "landing_recovery")
        self.assertEqual(step_up["MainPlatform"][1]["id"], "elevated_shelf")
        self.assertEqual(step_down["MainPlatform"][1]["id"], "descending_shelf")

        launch_height = self.surface(step_up["MainPlatform"][0])[-1]["y"]
        self.assertGreater(self.surface(step_up["MainPlatform"][1])[0]["y"], launch_height)
        launch_height = self.surface(step_down["MainPlatform"][0])[-1]["y"]
        self.assertLess(self.surface(step_down["MainPlatform"][1])[0]["y"], launch_height)


if __name__ == "__main__":
    unittest.main()
