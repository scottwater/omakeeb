import math
import unittest
from omakeeb.definitions import Key, load_definition
from omakeeb.geometry import bounds, hit_test, key_polygon, place_keys, point_in_polygon


class GeometryTest(unittest.TestCase):
    def test_rotation_uses_cluster_origin(self):
        key = Key(0, 0, 2, 1, rotation=90, rotation_x=1, rotation_y=1)
        points = key_polygon(key)
        for actual, expected in zip(points, [(1, 2), (1, 3), (0, 3), (0, 2)]):
            self.assertAlmostEqual(actual[0], expected[0])
            self.assertAlmostEqual(actual[1], expected[1])
        self.assertEqual(tuple(round(x) for x in bounds([key])), (0, 2, 1, 3))

    def test_rotated_hit_does_not_select_bounding_box_corner(self):
        key = Key(2, 3, 0, 0, rotation=45)
        placed = place_keys([key], 300, 200, max_unit=100)[0]
        self.assertIs(hit_test([placed], *placed.center), key)
        left, top, right, bottom = placed.bounds
        self.assertIsNone(hit_test([placed], left + 1, top + 1))
        self.assertTrue(point_in_polygon(*placed.points[0], placed.points))
        self.assertAlmostEqual(placed.angle, math.pi / 4)

    def test_scale_and_gap_use_same_hit_transform(self):
        a, b = Key(0, 0, 0, 0), Key(1, 5, 1, 0)
        for width, height in [(600, 250), (120, 80), (1000, 300)]:
            placed = place_keys([a, b], width, height)
            self.assertIs(hit_test(placed, *placed[0].center), a)
            self.assertIs(hit_test(placed, *placed[1].center), b)
            gap_x = (placed[0].center[0] + placed[1].center[0]) / 2
            self.assertIsNone(hit_test(placed, gap_x, placed[0].center[1]))

    def test_alternative_layout_reanchors_before_transform(self):
        raw = {"name": "Split", "matrix": {"rows": 1, "cols": 3}, "layouts": {"labels": [["Space", "Full", "Split"]], "keymap": [["0,0", {"w": 2}, "0,1\n\n\n0,0"], [{"y": 2}, "0,1\n\n\n0,1", "0,2\n\n\n0,1"]]}}
        definition = load_definition(raw)
        normal = definition.visible_keys({0: 0})
        alternate = definition.visible_keys({0: 1})
        self.assertEqual(bounds(normal), bounds(alternate))
        self.assertEqual(alternate[1].address, (0, 1))
        self.assertEqual(alternate[1].x, 1)
        self.assertEqual(alternate[1].y, 0)
        placed = place_keys(alternate, 500, 200)
        self.assertEqual(hit_test(placed, *placed[-1].center).address, (0, 2))

    def test_no_keys_and_invalid_canvas(self):
        self.assertEqual(place_keys([], 100, 100), [])
        self.assertEqual(place_keys([Key(0, 0, 0, 0)], 0, 0), [])
        with self.assertRaises(ValueError):
            place_keys([], float("nan"), 100)


if __name__ == "__main__":
    unittest.main()
