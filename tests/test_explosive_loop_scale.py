import unittest

from obstacle_library import build_variant, load_catalog


class ExplosiveLoopScaleTests(unittest.TestCase):
    def test_ring_diameter_gaps_and_total_module_width(self):
        family = load_catalog()["explosive_loop"]
        for variant in family["variants"]:
            parameters = variant["parameters"]
            ring_count = parameters.get("sequenceRings", 1)
            with self.subTest(variant=variant["id"]):
                self.assertEqual(parameters["radiusX"], 4)
                self.assertEqual(parameters["radiusY"], 4)
                self.assertEqual(parameters["boundaryClearance"], 1)

                module = build_variant(family, variant, scale=family["_worldScale"])
                ring = next(shape for shape in module["RampPlatform"]
                            if shape["id"] == "pre_route")
                ring_x = [point["x"] for point in ring["points"]]
                self.assertAlmostEqual(max(ring_x) - min(ring_x), 16)

                connector = next(item for item in module["InteractableObject"]
                                 if item["id"] == "connector")
                reveal_id = "revealed_route" if ring_count == 1 else f"revealed_route_{ring_count}"
                reveal = next(shape for shape in module["RampPlatform"]
                              if shape["id"] == reveal_id)
                route_start = connector["points"][0]["x"]
                route_end = reveal["points"][-1]["x"]
                self.assertAlmostEqual((route_end - route_start), 20 * ring_count)
                rings = [next(shape for shape in module["RampPlatform"]
                              if shape["id"] == ("pre_route" if index == 1 else f"pre_route_{index}"))
                         for index in range(1, ring_count + 1)]
                self.assertAlmostEqual(min(point["x"] for point in rings[0]["points"]) - route_start, 2)
                self.assertAlmostEqual(route_end - max(point["x"] for point in rings[-1]["points"]), 2)

                recovery = next(shape for shape in module["MainPlatform"]
                                if shape["id"] == "exit_recovery")
                self.assertAlmostEqual(recovery["points"][0]["x"], route_end)


if __name__ == "__main__":
    unittest.main()
