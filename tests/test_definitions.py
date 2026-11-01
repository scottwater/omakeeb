import json
from pathlib import Path
import tempfile
import unittest

from omakeeb.definitions import Catalog, DefinitionError, demo_definition, load_definition, load_definition_file, layout_choices, pack_layout_options, unpack_layout_options


def raw(keys=None):
    return {"name": "Test", "vendorId": "0x1234", "productId": "0x5678", "matrix": {"rows": 2, "cols": 3}, "layouts": {"keymap": keys or [["0,0", "0,1", "0,2"], ["1,0", "1,1", "1,2"]]}}


class DefinitionsTest(unittest.TestCase):
    def test_matrix_addresses_not_visual_order(self):
        definition = load_definition(raw([[{"w": 2, "x": .5}, "1,2", "0,0"]]))
        self.assertEqual(definition.keys[0].address, (1, 2))
        self.assertEqual(definition.keys[1].x, 2.5)
        self.assertEqual(definition.keys[1].w, 1)

    def test_invalid_matrix_and_geometry(self):
        for bad in [raw([["2,0"]]), raw([["0,3"]]), raw([["A"]]), raw([[{"w": 0}, "0,0"]]), raw([[{"x": float("inf")}, "0,0"]])]:
            with self.subTest(bad=bad), self.assertRaises(DefinitionError):
                load_definition(bad)
        item = raw()
        item["matrix"]["rows"] = True
        with self.assertRaises(DefinitionError):
            load_definition(item)

    def test_variants_encoder_and_rotation_preserved(self):
        definition = load_definition(raw([[{"r": 12, "rx": 3, "ry": 2}, "0,0\n\n\n0,0", "0,0\n\n\n0,1", "1,2\n\n\n\n\n\n\n\n\ne0"]]))
        self.assertEqual(len(definition.visible_keys()), 2)
        self.assertEqual(definition.visible_keys({0: 1})[0].variant, (0, 1))
        self.assertEqual(definition.encoder_count, 1)
        self.assertEqual(definition.keys[0].rotation_x, 3)
        self.assertEqual(definition.keys[0].rotation, 12)

    def test_catalog_complete_validated_and_known_keyboard(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Catalog(custom_dir=directory)
            self.assertGreater(len(catalog), 2000)
            self.assertEqual(len(catalog.all()), len(catalog))
            nk = catalog.find(0x8968, 0x4E54)
            self.assertEqual(len(nk), 1)
            self.assertEqual(nk[0].rows, 6)
            self.assertEqual(nk[0].cols, 17)
            # Shared USB IDs must remain explicit candidates, never a guessed model.
            for name in ("Candidate A", "Candidate B"):
                item = raw()
                item["name"] = name
                path = Path(directory) / f"{name}.json"
                path.write_text(json.dumps(item))
                catalog.add_file(path, persist=False)
            self.assertEqual(len(catalog.find(0x1234, 0x5678)), 2)

    def test_persist_import_and_detached_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_text(json.dumps(raw()))
            custom = Path(directory) / "custom"
            catalog = Catalog(custom_dir=custom)
            definition = catalog.add_file(path)
            loaded = Catalog(custom_dir=custom).get(definition.id)
            self.assertEqual(loaded.raw, definition.raw)
            copied = loaded.to_dict()
            copied["matrix"]["rows"] = 50
            self.assertEqual(loaded.rows, 2)
            self.assertEqual(loaded.raw["matrix"]["rows"], 2)
            self.assertEqual((custom / f"{definition.id}.json").stat().st_mode & 0o777, 0o600)

    def test_bad_json_reports_definition_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("{bad}")
            with self.assertRaises(DefinitionError):
                load_definition_file(path)

    def test_vial_encoder_directions_are_not_matrix_addresses(self):
        definition = load_definition(raw([["0,0", "8,0\n\n\n\n\n\n\n\n\ne", "8,1\n\n\n\n\n\n\n\n\ne"]]), vial=True)
        self.assertEqual(definition.encoder_count, 9)
        self.assertEqual(definition.keys[1].address, (-1, -1))
        self.assertEqual(definition.keys[1].encoder_direction, 0)
        self.assertEqual(definition.keys[2].encoder_direction, 1)

    def test_layout_bits_preserve_unknown_high_bits(self):
        labels = ["Split backspace", ["Bottom row", "ANSI", "Tsangan", "Split"]]
        self.assertEqual(unpack_layout_options(labels, 6), {0: 1, 1: 2})
        self.assertEqual(pack_layout_options(labels, {0: 1, 1: 2}), 6)
        self.assertEqual(pack_layout_options(labels, {0: 0}, previous=0x80000006), 0x80000002)
        with self.assertRaises(DefinitionError):
            pack_layout_options(labels, {1: 3})
        with self.assertRaises(DefinitionError):
            unpack_layout_options(labels, 3)

    def test_usb_catalog_search(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(Catalog(custom_dir=directory).search("8968:4E54")[0].name, "NK_ Classic TKL")

    def test_demo_matrix_matches_ui(self):
        definition = demo_definition()
        self.assertEqual((definition.rows, definition.cols), (5, 15))
        self.assertEqual(next(k for k in definition.keys if k.address == (4, 6)).w, 6.25)


if __name__ == "__main__":
    unittest.main()
