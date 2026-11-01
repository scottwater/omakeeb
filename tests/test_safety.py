from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omakeeb.macros import encode
from omakeeb.protocol import Protocol
from omakeeb.safety import (BackupStore, SafetyError, SafetySession, StaleSnapshot,
                           dangerous_keycodes, validate_new_keycode, validate_staged)
from tests.fake_keyboard import FakeKeyboard


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name) / "backups"
        self.fake = FakeKeyboard(encoder=True, lighting=True)
        self.protocol = Protocol(self.fake, self.fake.identity)
        self.session = SafetySession(self.protocol, self.fake.definition, self.directory)

    def tearDown(self):
        self.tmp.cleanup()

    def test_connect_backups_are_private_and_integrity_checked(self):
        self.assertEqual(self.session.backup_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        loaded = self.session.backups.load(self.session.backup_path)
        self.assertEqual(loaded["snapshot"], self.session.baseline)
        self.assertFalse(loaded["firmware_backup"])
        loaded["snapshot"]["layers"][0][0][0] = 7
        self.session.backup_path.write_text(json.dumps(loaded))
        with self.assertRaises(SafetyError):
            self.session.backups.load(self.session.backup_path)
        self.assertFalse(self.fake.writes)

    def test_changed_key_is_backed_up_before_first_write_and_verified(self):
        self.session.staged["layers"][0][0][0] = 30
        self.fake.before_write = lambda _: self.assertEqual(len(self.session.backups.list()), 2)
        self.session.apply()
        self.assertEqual([packet[0] for packet in self.fake.writes], [5])
        self.assertEqual(self.fake.keys[0][0][0], 30)
        self.assertFalse(self.session.dirty)

    def test_backup_failure_prevents_any_write(self):
        self.session.staged["layers"][0][0][0] = 30
        with patch.object(self.session.backups, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.session.apply()
        self.assertFalse(self.fake.writes)

    def test_fsync_failure_prevents_any_write(self):
        self.session.staged["layers"][0][0][0] = 30
        with patch("omakeeb.safety.os.fsync", side_effect=OSError("I/O error")):
            with self.assertRaises(OSError):
                self.session.apply()
        self.assertFalse(self.fake.writes)

    def test_concurrent_external_change_requires_reconnect(self):
        self.session.staged["layers"][0][0][0] = 30
        self.fake.keys[1][0][0] = 44
        with self.assertRaises(StaleSnapshot):
            self.session.apply()
        self.assertFalse(self.fake.writes)

    def test_invalid_import_is_rejected_before_any_packet(self):
        cases = []
        bad = deepcopy(self.session.baseline)
        bad["identity"]["product_id"] = 1
        cases.append(bad)
        bad = deepcopy(self.session.baseline)
        bad["layers"][0][0].pop()
        cases.append(bad)
        bad = deepcopy(self.session.baseline)
        bad["layers"][0][0][0] = True
        cases.append(bad)
        bad = deepcopy(self.session.baseline)
        bad["layers"][0][0][0] = 0x7C00
        cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad):
                sent_count = len(self.fake.sent)
                with self.assertRaises(SafetyError):
                    self.session.apply(bad)
                self.assertEqual(len(self.fake.sent), sent_count)

    def test_failed_readback_stops_and_never_retries_or_rolls_back(self):
        self.session.staged["layers"][0][0][0] = 30
        self.session.staged["layers"][0][0][1] = 31
        self.fake.drop_key_write = True
        with self.assertRaisesRegex(SafetyError, "Backup:"):
            self.session.apply()
        self.assertTrue(self.session.failed)
        self.assertEqual(len(self.fake.writes), 1)
        count = len(self.fake.sent)
        with self.assertRaises(SafetyError):
            self.session.apply()
        self.assertEqual(len(self.fake.sent), count)

    def test_disconnect_can_leave_partial_changes_but_preserves_backup(self):
        self.session.staged["layers"][0][0][0] = 30
        self.session.staged["layers"][0][0][1] = 31
        self.fake.fail_write_at = 2
        with self.assertRaises(SafetyError):
            self.session.apply()
        self.assertEqual(self.fake.keys[0][0], [30, 5, 6])
        original = self.session.backups.load(self.session.backup_path)["snapshot"]
        self.assertEqual(original["layers"][0][0], [4, 5, 6])

    def test_macro_transaction_uses_marker_no_reset_and_readback(self):
        self.session.staged["macro_buffer"] = encode(["Hi", "{KC_LCTL,KC_C}"], size=64).hex()
        self.session.apply()
        self.assertEqual(self.fake.writes[0], b"\x0f\x00\x3f\x01\xff")
        self.assertEqual(self.fake.writes[-1], b"\x0f\x00\x3f\x01\x00")
        self.assertTrue(all(packet[0] == 15 for packet in self.fake.writes))
        self.assertEqual(self.fake.macros[-1], 0)

    def test_macro_failure_keeps_execution_disabled(self):
        self.session.staged["macro_buffer"] = encode(["Changed", ""], size=64).hex()
        self.fake.fail_write_at = 2
        with self.assertRaises(SafetyError):
            self.session.apply()
        self.assertEqual(self.fake.macros[-1], 255)
        self.assertEqual(len(self.fake.writes), 2)

    def test_echoed_but_ignored_macro_marker_stops_before_payload_write(self):
        self.session.staged["macro_buffer"] = encode(["Changed", ""], size=64).hex()
        original = self.fake.exchange

        def relocked_firmware(packet):
            # Actual Vial via.c jumps to its final raw_hid_send while locked:
            # the SET packet is echoed without modifying the macro EEPROM.
            if packet == b"\x0f\x00\x3f\x01\xff":
                self.fake.sent.append(packet)
                self.fake.writes.append(packet)
                return packet.ljust(32, b"\0")
            return original(packet)

        self.fake.exchange = relocked_firmware
        with self.assertRaisesRegex(SafetyError, "marker was not accepted"):
            self.session.apply()
        self.assertEqual(self.fake.writes, [b"\x0f\x00\x3f\x01\xff"])
        self.assertEqual(self.fake.macros, bytes(64))

    def test_locked_vial_backup_preserves_nonempty_macro_and_settings_data(self):
        fake = FakeKeyboard(via=9, vial=6)
        fake.macros[:] = encode(["private macro", "{KC_LCTL,KC_C}"], protocol=9, vial_protocol=6, size=64)
        fake.unlocked = False
        session = SafetySession(Protocol(fake, fake.identity), fake.definition, self.directory)
        self.assertEqual(session.baseline["macro_buffer"], fake.macros.hex())
        self.assertEqual(session.baseline["settings"], {str(k): v.hex() for k, v in fake.settings.items()})
        self.assertEqual(session.baseline["dynamic"], fake.dynamic)
        self.assertFalse(fake.writes)

    def test_legacy_rgb_encoder_and_layout_options(self):
        fake = FakeKeyboard(via=10, encoder=True, lighting=True)
        session = SafetySession(Protocol(fake, fake.identity), fake.definition, self.directory)
        session.staged["encoders"][1][0][1] = 44
        session.staged["lighting"]["underglow_brightness"] = [90]
        session.staged["layout_options"] = 1
        session.apply()
        self.assertEqual(fake.encoders[1][0][1], 44)
        self.assertEqual(fake.light[(0x80,)], [90])
        self.assertEqual(fake.writes[-1], b"\x09")

    def backlight_session(self, via):
        fake = FakeKeyboard(via=via)
        if via < 11:
            fake.definition["lighting"] = "qmk_backlight"
            fake.light = {(9,): [0], (10,): [0]}
        else:
            fake.definition["menus"] = ["qmk_backlight"]
            fake.light = {(1, 1): [0], (1, 2): [0]}
        fake.backlight_levels = 3
        return fake, SafetySession(Protocol(fake, fake.identity), fake.definition, self.directory)

    def test_qmk_backlight_quantization_reports_effective_value_without_retry(self):
        for via in (9, 12):
            with self.subTest(via=via):
                fake, session = self.backlight_session(via)
                session.staged["lighting"]["backlight_brightness"] = [130]
                session.apply()
                self.assertEqual(session.baseline["lighting"]["backlight_brightness"], [85])
                self.assertEqual(session.staged, session.baseline)
                self.assertEqual(len(session.normalizations), 1)
                self.assertIn("requested 130, firmware applied 85", session.normalizations[0])
                self.assertEqual([packet[0] for packet in fake.writes], [7, 9])

    def test_invalid_backlight_readback_still_aborts_before_save(self):
        fake, session = self.backlight_session(12)
        session.staged["lighting"]["backlight_brightness"] = [130]
        original = fake.exchange

        def bad_firmware(packet):
            response = original(packet)
            if packet[:3] == b"\x07\x01\x01":
                fake.light[(1, 1)] = [100]  # No BACKLIGHT_LEVELS 1–255 yields 100 from 130.
            return response

        fake.exchange = bad_firmware
        with self.assertRaisesRegex(SafetyError, "Lighting readback failed"):
            session.apply()
        self.assertEqual([packet[0] for packet in fake.writes], [7])
        self.assertEqual(session.normalizations, [])

    def test_rgb_brightness_is_never_treated_as_backlight_quantization(self):
        self.session.staged["lighting"]["rgb_matrix_brightness"] = [130]
        original = self.fake.exchange

        def rgb_mismatch(packet):
            response = original(packet)
            if packet[:3] == b"\x07\x03\x01":
                self.fake.light[(3, 1)] = [85]
            return response

        self.fake.exchange = rgb_mismatch
        with self.assertRaisesRegex(SafetyError, "Lighting readback failed"):
            self.session.apply()
        self.assertEqual([packet[0] for packet in self.fake.writes], [7])
        self.assertEqual(self.session.normalizations, [])

    def test_vial_advanced_entries_settings_and_encoders(self):
        fake = FakeKeyboard(via=9, vial=6, encoder=True)
        session = SafetySession(Protocol(fake, fake.identity), fake.definition, self.directory)
        session.staged["dynamic"]["tap_dance"][0] = [5, 6, 7, 8, 250]
        session.staged["dynamic"]["combo"][0] = [4, 6, 0, 0, 7]
        session.staged["dynamic"]["key_override"][0][1] = 8
        session.staged["dynamic"]["alt_repeat_key"][0][1] = 9
        session.staged["settings"]["7"] = "fa00"
        session.staged["encoders"][0][0][0] = 44
        session.apply()
        self.assertEqual(fake.dynamic["tap_dance"][0][-1], 250)
        self.assertEqual(fake.settings[7], b"\xfa\0")
        self.assertEqual(fake.encoders[0][0][0], 44)

    def test_locked_vial_never_writes_or_automatically_unlocks(self):
        fake = FakeKeyboard(via=9, vial=6)
        fake.unlocked = False
        session = SafetySession(Protocol(fake, fake.identity), fake.definition, self.directory)
        session.staged["layers"][0][0][0] = 30
        with self.assertRaises(SafetyError):
            session.apply()
        self.assertFalse(fake.writes)
        self.assertFalse(any(packet[:2] == b"\xfe\x06" for packet in fake.sent))

    def test_vial_dangerous_macro_is_rejected(self):
        fake = FakeKeyboard(via=9, vial=6)
        session = SafetySession(Protocol(fake, fake.identity), fake.definition, self.directory)
        # Extended tap of QK_BOOT (encoded 0xff7c to avoid an embedded NUL).
        session.staged["macro_buffer"] = (b"\x01\x05\x7c\xff\0\0" + bytes(58)).hex()
        with self.assertRaises(SafetyError):
            session.apply()
        self.assertFalse(fake.writes)

    def test_dangerous_keycodes_are_protocol_specific_and_exclude_fake_alias(self):
        vial5 = {"via_protocol": 9, "vial_protocol": 5}
        vial6 = {"via_protocol": 9, "vial_protocol": 6}
        via12 = {"via_protocol": 12, "vial_protocol": None}
        self.assertEqual(dangerous_keycodes(vial5), {0x5C00, 0x5CDF})
        self.assertEqual(dangerous_keycodes(vial6), {0x7C00, 0x7C01, 0x7C03})
        self.assertEqual(dangerous_keycodes(via12), dangerous_keycodes(vial6))
        for snapshot in (vial5, vial6, via12):
            self.assertNotIn(0x999D, dangerous_keycodes(snapshot))
        validate_new_keycode(0x5C02, 0, vial5)  # modifier swap, not EEPROM reset
        validate_new_keycode(0x7C02, 0, vial6)  # debug, not EEPROM reset
        validate_new_keycode(0x5CDF, 0, vial6)  # different namespace
        validate_new_keycode(0x5CDF, 0x5CDF, vial5)  # existing assignment preserved
        with self.assertRaises(SafetyError):
            validate_new_keycode(0x5CDF, 0, vial5)

    def test_legacy_via_known_eeprom_and_reboot_variants_are_blocked(self):
        snapshot = {"via_protocol": 9, "vial_protocol": None}
        for value in (0x5C00, 0x5CDE, 0x5CDF, 0x5DA7, 0x5DA8):
            with self.subTest(value=hex(value)), self.assertRaises(SafetyError):
                validate_new_keycode(value, 0, snapshot)

    def test_vial5_eeprom_numeric_assignment_blocked_in_every_write_surface(self):
        fake = FakeKeyboard(via=9, vial=5, encoder=True)
        session = SafetySession(Protocol(fake, fake.identity), fake.definition, self.directory)
        variants = []
        value = 0x5CDF
        staged = deepcopy(session.baseline)
        staged["layers"][0][0][0] = value
        variants.append(("layer", staged))
        staged = deepcopy(session.baseline)
        staged["encoders"][0][0][0] = value
        variants.append(("encoder", staged))
        for capability, field in (("tap_dance", 0), ("combo", 4), ("key_override", 1)):
            staged = deepcopy(session.baseline)
            staged["dynamic"][capability][0][field] = value
            variants.append((capability, staged))
        staged = deepcopy(session.baseline)
        staged["macro_buffer"] = encode(["{0x5CDF}", ""], protocol=9, vial_protocol=5, size=64).hex()
        variants.append(("macro", staged))
        for label, staged in variants:
            with self.subTest(surface=label):
                before = len(fake.sent)
                with self.assertRaises(SafetyError):
                    session.apply(staged)
                self.assertEqual(len(fake.sent), before)
        self.assertFalse(fake.writes)

    def test_existing_legacy_dangerous_macro_is_preserved_when_another_macro_changes(self):
        fake = FakeKeyboard(via=9, vial=5)
        fake.macros[:] = encode(["{0x5CDF}", "before"], protocol=9, vial_protocol=5, size=64)
        session = SafetySession(Protocol(fake, fake.identity), fake.definition, self.directory)
        session.staged["macro_buffer"] = encode(["{0x5CDF}", "after"], protocol=9, vial_protocol=5, size=64).hex()
        session.apply()
        self.assertEqual(bytes(fake.macros), bytes.fromhex(session.staged["macro_buffer"]))

    def test_macro_with_unreleased_modifier_is_rejected(self):
        self.session.staged["macro_buffer"] = (b"\x01\x02\xe1\0\0" + bytes(59)).hex()
        with self.assertRaises(SafetyError):
            self.session.apply()
        self.assertFalse(self.fake.writes)

    def test_optional_lighting_absence_preserved_in_backup_and_does_not_block_key_edit(self):
        fake = FakeKeyboard(via=9, lighting=True)
        fake.unsupported_lighting = set(fake.light)
        session = SafetySession(Protocol(fake, fake.identity), fake.definition, self.directory)
        backup = session.backups.load(session.backup_path)
        self.assertEqual(len(backup["snapshot"]["unsupported_controls"]), 4)
        session.staged["layers"][0][0][0] = 30
        session.apply()
        self.assertEqual([packet[0] for packet in fake.writes], [5])

    def test_restore_is_staged_only_and_target_checked(self):
        path = self.session.backup_path
        self.session.staged["layers"][0][0][0] = 30
        self.session.apply()
        count = len(self.fake.writes)
        self.session.stage_backup(path)
        self.assertTrue(self.session.dirty)
        self.assertEqual(len(self.fake.writes), count)
        self.session.apply()
        self.assertEqual(self.fake.keys[0][0][0], 4)


if __name__ == "__main__":
    unittest.main()
