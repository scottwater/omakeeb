import unittest
from omakeeb import macros


class MacrosTest(unittest.TestCase):
    def test_via_exact_wire_formats(self):
        self.assertEqual(macros.encode(["{KC_A}"], protocol=10), b"\x01\x04\x00")
        self.assertEqual(macros.encode(["{KC_A}{250}"], protocol=11), b"\x01\x01\x04\x01\x04250|\x00")
        self.assertEqual(macros.encode(["{KC_LCTL,KC_C}"]), bytes([1, 2, 0xE0, 1, 2, 6, 1, 3, 6, 1, 3, 0xE0, 0]))

    def test_vial_binary_delay_and_extended_code(self):
        self.assertEqual(macros.encode(["{255}"], vial_protocol=6), bytes([1, 4, 1, 2, 0]))
        self.assertEqual(macros.encode(["{LGUI(KC_E)}"], vial_protocol=6), bytes([1, 5, 8, 8, 0]))
        # Zero low byte must be escaped so it cannot terminate the macro.
        encoded = macros.encode(["{0x5200}"], vial_protocol=6)
        self.assertEqual(encoded, bytes([1, 5, 0x52, 0xFF, 0]))
        self.assertEqual(macros.encode(macros.decode(encoded, 1, vial_protocol=6), vial_protocol=6), encoded)

    def test_roundtrip_across_all_dialects(self):
        for protocol, vial in [(9, None), (10, None), (11, None), (12, None), (13, None), (9, 1), (9, 2), (9, 6)]:
            expression = "Hello \\{brace\\} \\\\ {KC_LCTL,KC_C}{KC_ENT}"
            if protocol >= 11 or vial and vial >= 2:
                expression += "{9999}"
            encoded = macros.encode([expression, ""], protocol=protocol, size=100, vial_protocol=vial)
            decoded = macros.decode(encoded, 2, protocol=protocol, vial_protocol=vial)
            self.assertEqual(macros.encode(decoded, protocol=protocol, size=100, vial_protocol=vial), encoded)

    def test_overflow_and_unsafe_or_invalid_macro(self):
        for expression in ["{", "{}", "{KC_BOGUS}", "{+KC_LCTL}", "{-KC_LCTL}", "{+KC_A}{+KC_A}{-KC_A}", "{+KC_A,KC_B}", "{10000}", "nul\0", "snowman ☃", "{MO(1)}", "{KC_NO}"]:
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                macros.encode([expression])
        with self.assertRaises(ValueError):
            macros.encode(["longer than buffer"], size=4)
        with self.assertRaises(ValueError):
            macros.encode(["{10}"], protocol=10)

    def test_bad_wire_data_never_silently_truncated(self):
        for data in [b"\x01", b"\x01\x01", b"\x01\x01\0", b"\x01\x04x|\0", b"\x01\x08\0", b"not terminated", b"\x01\x0410000|\0"]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                macros.decode(data, 1)
        with self.assertRaises(ValueError):
            macros.decode(b"\x01\x04\x00\x01\0", 1, vial_protocol=6)


if __name__ == "__main__":
    unittest.main()
