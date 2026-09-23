"""Keep jump families visually and structurally distinct in the catalog."""
import math
import unittest

from obstacle_library import build_variant, load_catalog, place, profile, sample_curve
from spring_object import spring_trajectory
from terrain_export import surface_count


class JumpFamilyGeometryTests(unittest.TestCase):
    def test_ramps_and_explosive_loop_routes_are_continuous(self):
        families = load_catalog()
        for family in families.values():
            for variant in family["variants"]:
                module = build_variant(family, variant)
                for shape in module["RampPlatform"]:
                    if shape.get("closed"):
                        count = surface_count(shape)
                        self.assertEqual(shape["points"][0]["tangentMode"], "broken")
                        self.assertEqual(shape["points"][count-1]["tangentMode"], "broken")
                        self.assertTrue(all(point["tangentMode"] == "linear"
                                            for point in shape["points"][count:]))
                        continue
                    self.assertTrue(all(point["tangentMode"] == "continuous"
                                        for point in shape["points"]), variant["id"])
                for item in module["InteractableObject"]:
                    if item.get("type") == "explosive_ramp":
                        self.assertTrue(all(point["tangentMode"] == "continuous"
                                            for point in item["points"]), variant["id"])

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
        scoop_surface = self.surface(scoop["MainPlatform"][0])
        self.assertLess(min(y for _, y in sample_curve({"points": scoop_surface})), -.1)
        self.assertEqual(gap["MainPlatform"][1]["id"], "landing_recovery")
        self.assertEqual(step_up["MainPlatform"][1]["id"], "elevated_shelf")
        self.assertEqual(step_down["MainPlatform"][1]["id"], "descending_shelf")

        launch_height = self.surface(step_up["MainPlatform"][0])[-1]["y"]
        self.assertGreater(self.surface(step_up["MainPlatform"][1])[0]["y"], launch_height)
        launch_height = self.surface(step_down["MainPlatform"][0])[-1]["y"]
        self.assertLess(self.surface(step_down["MainPlatform"][1])[0]["y"], launch_height)

    def test_generated_launch_ascent_curves_up_without_a_shoulder(self):
        for name in ("curved_ramp", "gap", "step_up", "step_down"):
            family = self.families[name]
            for variant in family["variants"]:
                # Authored launches follow library data, checked separately below.
                if variant.get('geometryOverrides', {}).get('MainPlatform', {}).get('launch') is not None:
                    continue
                with self.subTest(variant=variant["id"]):
                    surface = self.surface(build_variant(family, variant)["MainPlatform"][0])
                    # Test slope progression, not just height: a monotonically
                    # rising S-curve can still have the unwanted convex hump.
                    left, right = surface[-2:]
                    controls = [(left["x"], left["y"]),
                                (left["x"]+left["tangentOut"]["x"], left["y"]+left["tangentOut"]["y"]),
                                (right["x"]+right["tangentIn"]["x"], right["y"]+right["tangentIn"]["y"]),
                                (right["x"], right["y"])]
                    slopes = [(b[1]-a[1])/(b[0]-a[0]) for a,b in zip(controls, controls[1:])]
                    self.assertEqual(slopes[0], 0)
                    self.assertTrue(all(a <= b+1e-9 for a,b in zip(slopes, slopes[1:])))
                    samples = sample_curve({"points": surface[-2:]}, 400)
                    slopes = [(b[1]-a[1])/(b[0]-a[0]) for a,b in zip(samples, samples[1:])]
                    self.assertTrue(all(a <= b+1e-9 for a,b in zip(slopes, slopes[1:])))
                    p = variant["parameters"]
                    self.assertEqual((surface[0]["x"], surface[0]["y"]), (0, 0))
                    self.assertAlmostEqual(surface[-1]["x"], p["approach"]+p["launch"])
                    self.assertAlmostEqual(surface[-1]["y"], p["height"])
                    handle = surface[-1]["tangentIn"]
                    self.assertAlmostEqual(handle["y"]/handle["x"], math.tan(math.radians(p["angle"])), places=5)

    def test_curved_ramps_preserve_library_geometry(self):
        family = self.families["curved_ramp"]
        for variant in family["variants"]:
            with self.subTest(variant=variant["id"]):
                p = variant["parameters"]
                surface = self.surface(build_variant(family, variant)["MainPlatform"][0])
                override = variant.get('geometryOverrides', {}).get('MainPlatform', {}).get('launch')
                if override is not None:
                    expected = (override['points'][:override['drivingSurfaceCount']]
                                if isinstance(override, dict) else override)
                    self.assertEqual(surface, expected)
                    continue
                expected = profile([(0,0), (p["approach"]*.68,0),
                                    (p["approach"]+p["launch"]*.18, -min(1.4,p["height"]*.32))],
                                   {1:0,2:0})
                self.assertEqual(surface[:2], expected[:2])
                self.assertEqual(surface[2]["y"], expected[2]["y"])
                self.assertEqual(surface[2]["tangentIn"], expected[2]["tangentIn"])

    def test_expanded_families_have_ten_distinct_variants(self):
        for name in ("platform", "spring_high", "spring_far"):
            with self.subTest(name=name):
                variants = self.families[name]["variants"]
                self.assertEqual(len(variants), 10)
                self.assertEqual(len({variant["id"] for variant in variants}), 10)

        platform_profiles = {v["parameters"]["profile"] for v in self.families["platform"]["variants"]}
        self.assertEqual(len(platform_profiles), 10)
        for name in ("spring_high", "spring_far"):
            shape_pairs = {(v["parameters"].get("approachProfile", "flat"),
                            v["parameters"].get("landingProfile", "flat"))
                           for v in self.families[name]["variants"]}
            self.assertGreaterEqual(len(shape_pairs), 6)

    def test_expanded_variants_build_valid_geometry_and_spring_arcs(self):
        for name in ("platform", "spring_high", "spring_far"):
            for variant in self.families[name]["variants"]:
                with self.subTest(variant=variant["id"]):
                    module = build_variant(self.families[name], variant)
                    for shape in module["MainPlatform"]:
                        self.assertGreaterEqual(surface_count(shape), 2)
                    if name == "platform":
                        self.assertGreaterEqual(len(module["RampPlatform"]), 1)
                    else:
                        spring = next(o for o in module["InteractableObject"]
                                      if o["type"] == "SpringObject")
                        end = spring_trajectory(spring)[-1]
                        target = spring["properties"]["targetPosition"]
                        self.assertAlmostEqual(end[0], target["x"])
                        self.assertAlmostEqual(end[1], target["y"])

    def test_selected_platforms_are_disconnected_momentum_transfers(self):
        expected = {"platform.flat_bridge": 3, "platform.wave_bridge": 3,
                    "platform.double_hump": 4}
        family = self.families["platform"]
        for variant in family["variants"]:
            if variant["id"] not in expected:
                continue
            with self.subTest(variant=variant["id"]):
                module = build_variant(family, variant)
                ramps = module["RampPlatform"]
                self.assertEqual(len(ramps), expected[variant["id"]])
                intervals = [(self.surface(shape)[0]["x"], self.surface(shape)[-1]["x"])
                             for shape in ramps]
                self.assertTrue(all(0 < right[0]-left[1] <= 2
                                    for left, right in zip(intervals, intervals[1:])))
                self.assertTrue(all(shape["metadata"]["requiresMomentumTransfer"]
                                    for shape in ramps[:-1]))
                self.assertGreater(max(point["y"] for shape in ramps
                                       for point in shape["points"]), 3)

    def test_all_platforms_are_closed_with_separate_undersides(self):
        family = self.families["platform"]
        for variant in family["variants"]:
            with self.subTest(variant=variant["id"]):
                module = build_variant(family, variant)
                for shape in module["RampPlatform"]:
                    self.assertTrue(shape["closed"])
                    count = surface_count(shape)
                    self.assertEqual(count, len(shape["points"])-2)
                    self.assertEqual(shape["points"][0]["tangentIn"], {"x":0,"y":0})
                    self.assertEqual(shape["points"][count-1]["tangentOut"], {"x":0,"y":0})
                    self.assertTrue(all(p["tangentMode"] == "linear" for p in shape["points"][-2:]))
                if "totalLength" in variant["parameters"]:
                    self.assertEqual(module["ports"]["exit"]["x"], variant["parameters"]["totalLength"])

    def test_platform_bases_touch_ground_and_have_no_flat_tails(self):
        family = self.families["platform"]
        for variant in family["variants"]:
            with self.subTest(variant=variant["id"]):
                module = build_variant(family, variant)
                for shape in module["RampPlatform"]:
                    surface = self.surface(shape)
                    self.assertTrue(all(p["y"] == -.01 for p in shape["points"][-2:]))
                    self.assertFalse(surface[0]["y"] == surface[1]["y"] == 0)
                    self.assertFalse(surface[-2]["y"] == surface[-1]["y"] == 0)
                translated = place(module, 20, -8, "base_test")
                for shape in translated["RampPlatform"]:
                    for point in shape["points"][-2:]:
                        self.assertAlmostEqual(point["y"], -8.01)

    def test_explosive_ring_variants_have_deterministic_reveals(self):
        family = self.families["explosive_loop"]
        expected = {"explosive_loop.round": 1, "explosive_loop.double_ring": 2,
                    "explosive_loop.triple_ring": 3}
        self.assertEqual({v["id"] for v in family["variants"]}, set(expected))
        for variant in family["variants"]:
            with self.subTest(variant=variant["id"]):
                module = build_variant(family, variant)
                connectors = [o for o in module["InteractableObject"]
                              if o["type"] == "explosive_ramp"]
                self.assertEqual(len(connectors), expected[variant["id"]])
                revealed_shape = next(s for s in module["RampPlatform"]
                                      if s["id"] == "revealed_route")
                revealed = revealed_shape["metadata"]
                self.assertEqual(revealed["initialState"], "hidden")
                self.assertFalse(revealed["collisionBeforeReveal"])
                self.assertTrue(revealed["collisionAfterReveal"])
                self.assertNotEqual(revealed_shape["points"][0]["tangentOut"]["y"], 0)
                self.assertEqual(revealed_shape["points"][-1]["tangentIn"]["y"], 0)
                self.assertEqual(connectors[0]["points"][0]["tangentOut"]["y"], 0)
                self.assertTrue(all(o["properties"]["debrisCollision"] == "visual_only"
                                    for o in connectors))
                self.assertTrue(all(o["properties"]["commitTrigger"]["mode"] ==
                                    "rear_wheel_clears_route_progress" for o in connectors))
                placed = place(module, 30, 4, "test")
                self.assertTrue(all(o["properties"]["revealsRoute"].startswith("test_")
                                    for o in placed["InteractableObject"]
                                    if o["type"] == "explosive_ramp"))

    def test_round_loop_uses_symmetric_tangent_arms(self):
        family = self.families["explosive_loop"]
        variant = next(v for v in family["variants"] if v["id"] == "explosive_loop.round")
        module = build_variant(family, variant)
        connector = next(o for o in module["InteractableObject"]
                         if o["type"] == "explosive_ramp")
        revealed = next(s for s in module["RampPlatform"] if s["id"] == "revealed_route")
        entry_vector = tuple(connector["points"][1][axis]-connector["points"][0][axis]
                             for axis in ("x","y"))
        exit_vector = tuple(revealed["points"][1][axis]-revealed["points"][0][axis]
                            for axis in ("x","y"))
        self.assertAlmostEqual(math.hypot(*entry_vector),math.hypot(*exit_vector),places=5)
        self.assertAlmostEqual(entry_vector[0],exit_vector[0],places=5)
        self.assertAlmostEqual(entry_vector[1],-exit_vector[1],places=5)
        self.assertAlmostEqual(connector["points"][0]["y"],revealed["points"][-1]["y"])
        self.assertAlmostEqual(connector["points"][0]["x"],
                               variant["parameters"]["approach"]-1)
        self.assertAlmostEqual(revealed["points"][-1]["x"],
                               variant["parameters"]["approach"]+2*variant["parameters"]["radiusX"]+1)
        self.assertEqual(connector["points"][0]["tangentOut"]["y"], 0)
        self.assertEqual(revealed["points"][-1]["tangentIn"]["y"], 0)
        self.assertNotEqual(connector["points"][1]["tangentIn"]["y"], 0)
        self.assertNotEqual(revealed["points"][0]["tangentOut"]["y"], 0)
        ring = next(s for s in module["RampPlatform"] if s["id"] == "pre_route")
        center_y = (variant["parameters"]["radiusY"]+
                    variant["parameters"]["ringGroundClearance"])
        self.assertAlmostEqual(connector["points"][1]["x"],
                               variant["parameters"]["approach"]+2*variant["parameters"]["radiusX"])
        self.assertAlmostEqual(revealed["points"][0]["x"],variant["parameters"]["approach"])
        self.assertAlmostEqual(connector["points"][1]["y"],center_y)
        self.assertAlmostEqual(revealed["points"][0]["y"],center_y)
        self.assertAlmostEqual(max(point["y"] for point in ring["points"]),
                               center_y+variant["parameters"]["radiusY"])
        self.assertAlmostEqual(connector["points"][0]["tangentOut"]["x"],
                               (connector["points"][1]["x"]-connector["points"][0]["x"])*.64)
        self.assertAlmostEqual(abs(connector["points"][1]["tangentIn"]["y"]),3.5)
        center_x = variant["parameters"]["approach"]+variant["parameters"]["radiusX"]
        crossing = min(sample_curve(connector,200),key=lambda point: abs(point[0]-center_x))
        self.assertGreaterEqual(crossing[1],0)
        self.assertLess(crossing[1],.25)

    def test_multi_ring_variants_are_direct_copies_of_round(self):
        family = self.families["explosive_loop"]
        for variant_id, count in (("explosive_loop.double_ring",2),
                                  ("explosive_loop.triple_ring",3)):
            variant = next(v for v in family["variants"] if v["id"] == variant_id)
            module = build_variant(family,variant)
            for stage in range(2,count+1):
                previous_reveal_id = "revealed_route" if stage == 2 else f"revealed_route_{stage-1}"
                connector_id = f"connector_{stage}"
                previous_reveal = next(s for s in module["RampPlatform"]
                                       if s["id"] == previous_reveal_id)
                connector = next(o for o in module["InteractableObject"]
                                 if o["id"] == connector_id)
                self.assertEqual(previous_reveal["points"][-1]["x"],connector["points"][0]["x"])
                self.assertEqual(previous_reveal["points"][-1]["y"],connector["points"][0]["y"])
                self.assertEqual(connector["properties"]["revealsRoute"],f"revealed_route_{stage}")


if __name__ == "__main__":
    unittest.main()
