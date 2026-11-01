import json
import struct
import unittest

from omakeeb.protocol import Protocol, ProtocolError, UnsupportedProtocol, setting_width
from tests.fake_keyboard import FakeKeyboard


class ProtocolTests(unittest.TestCase):
    def connect(self, **kwargs):
        fake = FakeKeyboard(**kwargs)
        return fake, Protocol(fake, fake.identity)

    def test_snapshot_reads_entire_matrix_and_macro_capacity_without_writes(self):
        fake, protocol = self.connect(encoder=True, lighting=True)
        snapshot = protocol.read_snapshot(fake.definition)
        self.assertEqual(snapshot["layers"], fake.keys)
        self.assertEqual(snapshot["macro_buffer"], bytes(64).hex())
        self.assertEqual(snapshot["encoders"], fake.encoders)
        self.assertEqual(snapshot["lighting"]["rgb_matrix_color"], [12, 255])
        self.assertEqual(fake.writes, [])
        self.assertEqual([p[3] for p in fake.sent if p[0] == 14], [28, 28, 8])

    def test_vial_snapshot_preserves_advanced_features(self):
        fake, protocol = self.connect(via=9, vial=6, encoder=True, lighting=True)
        snapshot = protocol.read_snapshot(fake.definition)
        self.assertEqual(snapshot["keyboard_id"], "1122334455667788")
        self.assertEqual(snapshot["dynamic"], fake.dynamic)
        self.assertEqual(snapshot["settings"], {"7": "c800", "22": "00"})
        self.assertEqual(snapshot["encoders"], fake.encoders)
        self.assertEqual(fake.writes, [])

    def test_protocol13_keycode_version_is_four_bytes(self):
        fake, protocol = self.connect(via=13)
        self.assertEqual(protocol.probe()["keycodes_version"], 9)

    def test_unknown_protocol_refuses_commands(self):
        fake, protocol = self.connect(via=14)
        with self.assertRaises(UnsupportedProtocol):
            protocol.read_snapshot(fake.definition)
        self.assertEqual(fake.sent, [b"\x01"])
        self.assertFalse(fake.writes)

    def test_definition_identity_mismatch_is_rejected(self):
        fake, protocol = self.connect()
        fake.definition["productId"] = "0x0001"
        with self.assertRaises(ProtocolError):
            protocol.read_snapshot(fake.definition)
        self.assertFalse(fake.writes)

    def test_corrupted_response_poisoning_prevents_late_packet_reuse(self):
        fake, protocol = self.connect()
        fake.exchange = lambda _: bytes(32)
        with self.assertRaises(ProtocolError):
            protocol.probe()
        fake.exchange = lambda _: self.fail("A poisoned protocol must never send another packet")
        with self.assertRaises(ProtocolError):
            protocol.probe()

    def test_vial_definition_decompression_is_bounded(self):
        fake, protocol = self.connect(via=9, vial=6)
        protocol.probe()
        original = fake.exchange
        fake.exchange = lambda request: (struct.pack("<I", 300000) + bytes(28)) if request == b"\xfe\x01" else original(request)
        with self.assertRaises(ProtocolError):
            protocol.read_vial_definition()

    def test_matrix_read_and_vial_lock_do_not_write(self):
        fake, protocol = self.connect(via=9, vial=6)
        self.assertEqual(protocol.read_matrix_state(fake.definition), {(0, 0), (1, 2)})
        fake.unlocked = False
        with self.assertRaises(ProtocolError):
            protocol.read_matrix_state(fake.definition)
        self.assertFalse(fake.writes)

    def test_physical_unlock_is_explicit_and_relock_verified(self):
        fake, protocol = self.connect(via=9, vial=6)
        fake.unlocked = False
        self.assertFalse(protocol.read_unlock_status()["unlocked"])
        self.assertFalse(any(p[:2] == b"\xfe\x06" for p in fake.sent))
        self.assertEqual(protocol.begin_unlock()["keys"], [(0, 0), (1, 2)])
        with self.assertRaises(ProtocolError):
            protocol.lock_keyboard()
        for _ in range(3):
            result = protocol.poll_unlock()
        self.assertTrue(result["unlocked"])
        self.assertFalse(protocol.lock_keyboard()["unlocked"])

    def test_setting_widths_from_schema(self):
        self.assertEqual(setting_width(7), 2)
        self.assertEqual(setting_width(21), 4)
        self.assertIsNone(setting_width(60000))

    def test_definitively_unsupported_optional_lighting_does_not_block_snapshot(self):
        fake, protocol = self.connect(via=9, lighting=True)
        fake.unsupported_lighting = set(fake.light)
        snapshot = protocol.read_snapshot(fake.definition)
        self.assertEqual(snapshot["lighting"], {})
        self.assertEqual(protocol.lighting_specs, {})
        self.assertEqual(snapshot["unsupported_controls"], [
            "underglow_brightness", "underglow_color", "underglow_effect", "underglow_speed"])
        self.assertEqual(snapshot["layers"], fake.keys)
        self.assertFalse(protocol._poisoned)
        self.assertFalse(fake.writes)

    def test_missing_optional_speed_keeps_other_supported_controls(self):
        fake, protocol = self.connect(via=9, lighting=True)
        fake.unsupported_lighting = {(0x82,)}
        snapshot = protocol.read_snapshot(fake.definition)
        self.assertEqual(snapshot["unsupported_controls"], ["underglow_speed"])
        self.assertEqual(set(snapshot["lighting"]), {"underglow_brightness", "underglow_effect", "underglow_color"})

    def test_optional_lighting_wrong_echo_still_poisoned(self):
        fake, protocol = self.connect(via=9, lighting=True)
        original = fake.exchange
        fake.exchange = lambda request: b"\xff\x81" + bytes(30) if request == b"\x08\x80" else original(request)
        with self.assertRaises(ProtocolError):
            protocol.read_snapshot(fake.definition)
        self.assertTrue(protocol._poisoned)
        self.assertFalse(fake.writes)


if __name__ == "__main__":
    unittest.main()
