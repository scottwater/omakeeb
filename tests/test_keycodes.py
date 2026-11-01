import unittest
import json
from pathlib import Path
from omakeeb import keycodes


class KeycodesTest(unittest.TestCase):
    def test_every_catalog_name_round_trips_each_protocol(self):
        for protocol in range(9, 14):
            for key in keycodes.search(protocol=protocol):
                with self.subTest(protocol=protocol, name=key.name):
                    self.assertEqual(keycodes.parse(key.name, protocol=protocol), key.code)
                    self.assertEqual(keycodes.parse(keycodes.name(key.code, protocol=protocol), protocol=protocol), key.code)

    def test_protocol_ranges_are_not_interchangeable(self):
        self.assertEqual(keycodes.parse("MO(1)", protocol=10), 0x5101)
        self.assertEqual(keycodes.parse("MO(1)", protocol=12), 0x5221)
        self.assertEqual(keycodes.parse("M(0)", protocol=10), 0x5F12)
        self.assertEqual(keycodes.parse("M(0)", protocol=11), 0x7702)
        self.assertEqual(keycodes.parse("M(0)", protocol=12), 0x7700)
        self.assertEqual(keycodes.parse("UG_TOGG", protocol=13), 0x7820)
        with self.assertRaises(ValueError):
            keycodes.parse("RGB_TOG", protocol=13)

    def test_modifiers_layers_and_taps(self):
        cases = {"LGUI(KC_E)": 0x0808, "LCTL(LSFT(KC_A))": 0x0304, "LT(3,KC_SPACE)": 0x432C, "MT(MOD_LCTL|MOD_LSFT,KC_ESC)": 0x2329, "RALT_T(KC_ENT)": 0x3428, "OSM(MOD_LGUI)": 0x52A8, "LM(2,MOD_LCTL)": 0x5041}
        for expression, expected in cases.items():
            self.assertEqual(keycodes.parse(expression), expected)

    def test_reject_invalid_expressions_without_evaluation(self):
        for expression in ["__import__('os')", "LCTL(RSFT(KC_A))", "MT(MOD_LCTL|MOD_RALT,KC_A)", "LT(16,KC_A)", "MO(32)", "M(128)", "LCTL(MO(1))", "0x10000", "65536", True, -1, "LCTL(KC_A"]:
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                keycodes.parse(expression)

    def test_unknown_numeric_codes_roundtrip(self):
        for protocol in range(9, 14):
            for code in (0xFFFF, 0xAA55, 0x2345, 0x0002, 0x1000, 0x1004):
                self.assertEqual(keycodes.parse(keycodes.name(code, protocol=protocol), protocol=protocol), code)

    def test_snapshot_protocol_is_not_qmk_keycodes_version(self):
        self.assertEqual(keycodes.keycode_protocol({"via_protocol": 13, "keycodes_version": 8}), 13)
        self.assertEqual(keycodes.keycode_protocol({"via_protocol": 9, "vial_protocol": 6}), "vial6")
        self.assertEqual(keycodes.keycode_protocol({"via_protocol": 9, "vial_protocol": 5}), "vial5")
        with self.assertRaises(ValueError):
            keycodes.keycode_protocol({"via_protocol": 13, "keycodes_version": 10})

    def test_reference_syntax_fixtures(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/reference-keycodes.json").read_text())
        for protocol, expression, expected in fixture["via"]:
            with self.subTest(protocol=protocol, expression=expression):
                self.assertEqual(keycodes.parse(expression, protocol=protocol), expected)
                self.assertEqual(keycodes.via_name(expected, protocol=protocol), expression)
        for vial, expression, expected in fixture["vial"]:
            with self.subTest(vial=vial, expression=expression):
                self.assertEqual(keycodes.parse(expression, protocol="vial6" if vial >= 6 else "vial5"), expected)

    def test_all_vial_literal_names_match_reference_wire_values(self):
        catalog = json.loads((Path(__file__).parent.parent / "resources/vial-keycodes.json").read_text())["protocols"]
        for version, table in catalog.items():
            for name, expected in table.items():
                with self.subTest(version=version, name=name):
                    self.assertEqual(keycodes.parse(name, protocol="vial" + version), expected)
        self.assertEqual(keycodes.parse("MI_A", protocol="vial6"), 0x710C)
        self.assertEqual(keycodes.parse("MI_A", protocol=12), 0x7119)
        self.assertEqual(keycodes.parse("PDF(2)", protocol="vial6"), 0x52E2)

    def test_via_refuses_unrepresentable_numeric_values(self):
        for value in (0xFFFF, 0xAA55, 0x1000, 0x4002):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Omakeeb"):
                keycodes.via_name(value)

    def test_vial_extended_advertised_macro_slot(self):
        snapshot = {"via_protocol": 9, "vial_protocol": 5, "macro_count": 32}
        self.assertEqual(keycodes.macro_code(20, snapshot), 0x5F26)
        with self.assertRaises(ValueError):
            keycodes.macro_code(32, snapshot)
        with self.assertRaises(ValueError):
            keycodes.parse("M128", protocol=12)

    def test_search_human_label_and_category(self):
        self.assertTrue(any(item.name == "KC_LGUI" for item in keycodes.search("super")))
        self.assertTrue(all(item.category == "Lighting" for item in keycodes.search(category="Lighting")))
        self.assertEqual(keycodes.label(0x4F), "→")
        self.assertEqual(keycodes.parse("KC_RIGHT"), 0x4F)


if __name__ == "__main__":
    unittest.main()
