import math
import unittest

from bike_stunt.library_variants import merge_close_platform_points
from obstacle_library import build_variant, load_catalog, vertex
from terrain_export import surface_count
import copy


class PlatformPointMergingTests(unittest.TestCase):
    def test_library_platforms_have_no_tiny_adjacent_vertices(self):
        family = load_catalog()["platform"]
        tolerance = 0.05 * family.get("_worldScale", 1)
        for variant in family["variants"]:
            module = build_variant(family, variant, scale=family.get("_worldScale", 1))
            for shape in module["RampPlatform"]:
                with self.subTest(variant=variant["id"], shape=shape["id"]):
                    points = shape["points"]
                    pairs = list(zip(points, points[1:]))
                    if shape.get("closed"):
                        pairs.append((points[-1], points[0]))
                    for a, b in pairs:
                        self.assertGreaterEqual(
                            math.dist((a["x"], a["y"]), (b["x"], b["y"])), tolerance
                        )

    def test_close_corner_merges_average_position_and_rounds(self):
        shape = {
            "closed": True,
            "points": [
                vertex(0, 0), vertex(1, 1), vertex(2, 0),
                vertex(2, -0.01), vertex(0, -0.01),
            ],
            "metadata": {"drivingSurfaceCount": 3},
        }
        self.assertTrue(merge_close_platform_points(shape))
        self.assertEqual(len(shape["points"]), 3)
        self.assertEqual(shape["metadata"]["drivingSurfaceCount"], 3)
        self.assertEqual((shape["points"][0]["x"], shape["points"][0]["y"]), (0, -0.01))
        self.assertEqual((shape["points"][-1]["x"], shape["points"][-1]["y"]), (2, -0.01))

    def test_merged_bottom_corner_survives_library_override_rebuild(self):
        family = load_catalog()["platform"]
        variant = copy.deepcopy(next(v for v in family["variants"] if v["id"] == "platform.arched_bridge"))
        source = build_variant(family, variant, scale=1)
        shape = next(shape for shape in source["RampPlatform"]
                     if shape.get("metadata", {}).get("mergedClosePoints"))
        variant.setdefault("geometryOverrides", {}).setdefault("RampPlatform", {})[shape["id"]] = {
            "drivingSurfaceCount": shape["metadata"]["drivingSurfaceCount"],
            "freeBottomCorners": shape["metadata"].get("freeBottomCorners", False),
            "mergedClosePoints": True,
            "points": copy.deepcopy(shape["points"]),
        }
        rebuilt = build_variant(family, variant, scale=family["_worldScale"])
        result = next(item for item in rebuilt["RampPlatform"] if item["id"] == shape["id"])
        self.assertTrue(result["metadata"]["mergedClosePoints"])
        self.assertEqual(surface_count(result), shape["metadata"]["drivingSurfaceCount"])


if __name__ == "__main__":
    unittest.main()
