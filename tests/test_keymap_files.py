import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from omakeeb.keymap_files import (KeymapFileError, export_keymap, import_keymap,
                                  load_profile, load_profile_bundle, validate_snapshot)
from omakeeb.macros import encode, decode


def snapshot(vial=False):
    return {'schema_version':1, 'identity': {'path':'/dev/hidraw-test','vendor_id':0x8968,'product_id':0x4e54,
            'serial':'first-device'}, 'definition_sha256':'a'*64, 'via_protocol':12,
            'vial_protocol':6 if vial else None, 'keyboard_id':'1122334455667788' if vial else None,
            'matrix':{'rows':2,'cols':2}, 'layers': [[[4,5],[6,7]], [[8,9],[10,11]]],
            'macro_count':2, 'macro_buffer': bytes(64).hex(),
            'encoders':[[[4,5]],[[6,7]]], 'lighting':{'brightness':[128]},
            'layout_options':0, 'dynamic':{}, 'settings':{}, 'future_metadata':{'preserved': True}}


class KeymapFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'layout.json'

    def write(self, data):
        self.path.write_text(json.dumps(data))

    def test_native_roundtrip_is_lossless_and_private(self):
        source = snapshot()
        export_keymap(self.path, source)
        self.assertEqual(load_profile(self.path), source)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(import_keymap(self.path, source), source)

    def test_import_stages_only_and_keeps_live_device_metadata(self):
        source, baseline = snapshot(), snapshot()
        source['layers'][0][0][0] = 42
        baseline['identity']['path'] = '/dev/hidraw-new'
        baseline['identity']['serial'] = 'another-device'
        baseline['future_metadata'] = {'live':True}
        original = copy.deepcopy(baseline)
        export_keymap(self.path, source)
        imported = import_keymap(self.path, baseline)
        self.assertEqual(baseline, original)
        self.assertEqual(imported['layers'][0][0][0], 42)
        self.assertEqual(imported['identity'], baseline['identity'])
        self.assertEqual(imported['future_metadata'], baseline['future_metadata'])

    def test_checksum_detects_modification(self):
        export_keymap(self.path, snapshot())
        data = json.loads(self.path.read_text())
        data['snapshot']['layers'][0][0][0] = 99
        self.write(data)
        with self.assertRaisesRegex(KeymapFileError, 'checksum'):
            load_profile(self.path)

    def test_duplicate_json_fields_rejected(self):
        self.path.write_text('{"schema_version":1,"schema_version":2}')
        with self.assertRaisesRegex(KeymapFileError,'Duplicate'):
            load_profile(self.path)

    def test_self_contained_native_profile_validates_geometry_and_hash(self):
        from omakeeb.protocol import definition_hash
        source = snapshot()
        definition = {"name":"Test keyboard", "vendorId":0x8968,"productId":0x4e54,
                      "matrix":{"rows":2,"cols":2},"layouts":{"keymap":[["0,0","0,1"],["1,0","1,1"]]}}
        source['definition_sha256'] = definition_hash(definition)
        export_keymap(self.path,source,definition=definition)
        loaded, embedded = load_profile_bundle(self.path)
        self.assertEqual(loaded,source)
        self.assertEqual(embedded,definition)
        tampered=json.loads(self.path.read_text())
        tampered['definition']['name']='Different board'
        self.write(tampered)
        with self.assertRaisesRegex(KeymapFileError,'definition'):
            load_profile_bundle(self.path)
        wrong=copy.deepcopy(definition)
        wrong['matrix']['cols']=3
        source['definition_sha256']=definition_hash(wrong)
        with self.assertRaisesRegex(KeymapFileError,'dimensions'):
            export_keymap(self.path,source,definition=wrong)

    def test_external_encoder_fixtures_preserve_ccw_cw_order(self):
        # This fixture follows VIA save-load.tsx: encoder-major, then layer,
        # then getEncoderValue(false), getEncoderValue(true). Distinct values
        # prove directions against the external format, not just a roundtrip.
        source=snapshot(vial=True)
        via={"name":"External VIA export", "vendorProductId":0x89684e54,
             "layers":[['KC_A','KC_B','KC_C','KC_D'],['KC_E','KC_F','KC_G','KC_H']],
             "encoders":[[['KC_VOLD','KC_VOLU'],['KC_PGDN','KC_PGUP']]]}
        self.write(via)
        imported=import_keymap(self.path,source)
        self.assertEqual(imported['encoders'],[[[170,169]],[[78,75]]])
        # Vial save_layout serializes direction0 then1 despite naming its
        # local variables cw/ccw. EncoderWidget documents direction0 as CCW.
        vial={"version":1,"uid":int(source['keyboard_id'],16),
              "layout":source['layers'],"encoder_layout":[[[170,169]],[[78,75]]]}
        self.write(vial)
        self.assertEqual(import_keymap(self.path,source)['encoders'],imported['encoders'])
        export_keymap(self.path,imported,format='via')
        self.assertEqual(json.loads(self.path.read_text())['encoders'],via['encoders'])

    def test_native_identity_definition_protocol_and_sizes_must_match(self):
        for mutation in ('identity','definition_sha256','via_protocol','matrix','macro_count','macro_buffer'):
            with self.subTest(field=mutation):
                source = snapshot()
                if mutation == 'identity': source['identity']['vendor_id'] = 123
                elif mutation == 'definition_sha256': source[mutation] = 'b'*64
                elif mutation == 'via_protocol': source[mutation] = 13
                elif mutation == 'matrix': source['matrix']['cols'] = 3
                elif mutation == 'macro_count': source[mutation] = 3
                else: source[mutation] += '00'
                self.write(source)
                with self.assertRaises(KeymapFileError):
                    import_keymap(self.path, snapshot())

    def test_valid_via_roundtrip_transposes_encoders(self):
        source = snapshot()
        source['macro_buffer'] = encode(['abc{KC_ENT}', '{KC_LCTL,KC_C}{250}'], size=64).hex()
        export_keymap(self.path, source, format='via')
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw['encoders'], [[['KC_A','KC_B'],['KC_C','KC_D']]])
        result = import_keymap(self.path, source)
        for field in ('layers','encoders','macro_buffer'):
            self.assertEqual(result[field], source[field])

    def test_via_rejects_wrong_dimensions_unknown_codes_macro_overflow(self):
        source = snapshot()
        export_keymap(self.path, source, format='via')
        valid = json.loads(self.path.read_text())
        for kind in ('keys','layers','macro','code','model','encoder'):
            data = copy.deepcopy(valid)
            if kind == 'keys': data['layers'][0].pop()
            elif kind == 'layers': data['layers'].pop()
            elif kind == 'macro': data['macros'][0] = 'a'*100
            elif kind == 'code': data['layers'][0][0] = 'UNKNOWN_GUESSED_CODE'
            elif kind == 'model': data['vendorProductId'] += 1
            else: data['encoders'][0].pop()
            self.write(data)
            with self.subTest(kind=kind), self.assertRaises(KeymapFileError):
                import_keymap(self.path, source)

    def test_vial_roundtrip_includes_dynamic_entries_and_binary_macros(self):
        source = snapshot(vial=True)
        source['dynamic'] = {'tap_dance':[[4,5,6,7,200]], 'combo':[[4,5,0,0,6]],
                             'key_override':[[4,5,65535,1,2,3,128]], 'alt_repeat_key':[[4,5,3,8]]}
        source['macro_buffer'] = encode(['hello{250}{LCTL(KC_A)}', '{KC_LCTL,KC_C}'], vial_protocol=6, size=64).hex()
        export_keymap(self.path, source, format='vial')
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw['uid'], int(source['keyboard_id'],16))
        self.assertEqual(raw['macro'][0][1], ['delay',250])
        result = import_keymap(self.path, source)
        for field in ('layers','encoders','macro_buffer','dynamic'):
            self.assertEqual(result[field], source[field])

    def test_vial_unknown_matrix_cells_preserve_baseline(self):
        source = snapshot(vial=True)
        export_keymap(self.path, source, format='vial')
        raw = json.loads(self.path.read_text())
        raw['layout'][0][0][0] = -1
        self.write(raw)
        self.assertEqual(import_keymap(self.path, source)['layers'], source['layers'])

    def test_vial_uid_mismatch_and_unbalanced_macros_rejected(self):
        source = snapshot(vial=True)
        export_keymap(self.path, source, format='vial')
        valid = json.loads(self.path.read_text())
        for change in ('uid','hold','feature'):
            raw = copy.deepcopy(valid)
            if change == 'uid': raw['uid'] += 1
            elif change == 'hold': raw['macro'][0] = [['down','KC_LCTL']]
            else: raw['combo'] = [[4,5,0,0,6]]
            self.write(raw)
            with self.subTest(change=change), self.assertRaises(KeymapFileError):
                import_keymap(self.path, source)

    def test_via_on_vial_preserves_encoder_direction_and_converts_macro_dialect(self):
        source = snapshot(vial=True)
        source['macro_buffer'] = encode(['{250}', 'hello'], vial_protocol=6,size=64).hex()
        export_keymap(self.path, source,format='via')
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw['encoders'][0][0], ['KC_A','KC_B'])
        imported = import_keymap(self.path,source)
        self.assertEqual(imported['encoders'],source['encoders'])
        self.assertEqual(imported['macro_buffer'],source['macro_buffer'])

    def test_raw_unknown_macro_bytes_can_be_preserved_in_native_export(self):
        source = snapshot()
        source['macro_buffer'] = (b'\x01\x7f' + bytes(62)).hex()
        export_keymap(self.path, source)
        self.assertEqual(load_profile(self.path),source)
        with self.assertRaises(ValueError):
            export_keymap(self.path, source,format='via')

    def test_automatic_backup_import_verifies_embedded_definition(self):
        from omakeeb.safety import BackupStore
        from omakeeb.protocol import definition_hash
        definition = {"name":"test", "matrix":{"rows":2,"cols":2}, "layouts":{"keymap":[["0,0","0,1"],["1,0","1,1"]]}}
        source = snapshot()
        source["definition_sha256"] = definition_hash(definition)
        path = BackupStore(Path(self.temp.name) / 'backups').save(source,definition)
        self.assertEqual(import_keymap(path,source),source)
        raw = json.loads(path.read_text())
        raw['definition']['name'] = 'altered'
        path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(KeymapFileError,'checksum'):
            import_keymap(path,source)

    def test_vial_settings_preserve_exact_width(self):
        source = snapshot(vial=True)
        source['settings'] = {'7':'c800','21':'01000000'}
        export_keymap(self.path,source,format='vial')
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw['settings'],{'7':200,'21':1})
        self.assertEqual(import_keymap(self.path,source)['settings'],source['settings'])

    def test_external_vial_aliases_and_via_custom_macro_import(self):
        source = snapshot(vial=True)
        vial = {"version": 1, "uid": int(source["keyboard_id"], 16),
                "layout": [[["M0", "KC_KP_ENTER"], ["LT2(KC_SPACE)", "LCTL_T(KC_A)"]], source["layers"][1]]}
        self.write(vial)
        self.assertEqual(import_keymap(self.path, source)["layers"][0], [[0x7700, 0x58], [0x422C, 0x2104]])
        via = {"name": "NK Classic TKL", "vendorProductId": 0x89684E54,
               "layers": [["CUSTOM(0)", "MACRO(0)", "MT(MOD_LCTL,KC_A)", "OSM(MOD_LCTL|MOD_LSFT)"], ["KC_E", "KC_F", "KC_G", "KC_H"]]}
        self.write(via)
        self.assertEqual(import_keymap(self.path, source)["layers"][0], [[0x7E00, 0x7700], [0x2104, 0x52A3]])

    def test_via_exports_reference_accepted_macro_custom_and_compounds(self):
        source = snapshot()
        source["layers"][0] = [[0x7700, 0x7E00], [0x2104, 0x52A3]]
        export_keymap(self.path, source, format="via")
        self.assertEqual(json.loads(self.path.read_text())["layers"][0],
                         ["MACRO(0)", "CUSTOM(0)", "MT(MOD_LCTL,KC_A)", "OSM(MOD_LCTL|MOD_LSFT)"])

    def test_via_unknown_hex_does_not_replace_existing_export(self):
        source = snapshot()
        source["layers"][0][0][0] = 0xAA55
        self.path.write_text("existing file")
        with self.assertRaisesRegex(ValueError, "VIA cannot preserve"):
            export_keymap(self.path, source, format="via")
        self.assertEqual(self.path.read_text(), "existing file")

    def test_vial_hex_exports_are_accepted_python_integer_literals(self):
        import ast
        source = snapshot(vial=True)
        source["layers"][0] = [[0x7700, 0x2104], [0x1004, 0xAA55]]
        export_keymap(self.path, source, format="vial")
        raw = json.loads(self.path.read_text())
        # Vial AnyKeycode uses simpleeval's Python AST constant handling;
        # integer literals need no custom names or functions to be resolved.
        decoded = [[ast.literal_eval(code) for code in row] for row in raw["layout"][0]]
        self.assertEqual(decoded, source["layers"][0])
        self.assertEqual(raw["layout"][0][0][0], "0x7700")

    def test_via_dictionary_on_modern_vial_preserves_wire_values(self):
        source = snapshot(vial=True)
        source["via_protocol"] = 9
        source["layers"][0] = [[0x5201, 0x7700], [0x2104, 0x422C]]
        #0x2104 is not representable by VIA9; do not silently export it as zero.
        with self.assertRaisesRegex(ValueError, "VIA cannot preserve"):
            export_keymap(self.path, source, format="via")
        source["layers"][0][1][0] = 4
        export_keymap(self.path, source, format="via")
        self.assertEqual(json.loads(self.path.read_text())["layers"][0][:2], ["DF(1)", "MT(MOD_RCTL|MOD_RSFT|MOD_RALT,KC_NO)"])
        self.assertEqual(import_keymap(self.path, source)["layers"], source["layers"])

    def test_via_macro_escape_semantics_match_reference(self):
        source = snapshot()
        via = {"name": "Test", "vendorProductId": 0x89684E54,
               "layers": [["KC_A", "KC_B", "KC_C", "KC_D"], ["KC_E", "KC_F", "KC_G", "KC_H"]],
               "macros": [r"\\server\share", r"literal \{x} and C:\tmp\}"]}
        self.write(via)
        imported = import_keymap(self.path, source)
        expected = (r"\\server\share" + "\0" + r"literal {x} and C:\tmp\}" + "\0").encode()
        self.assertTrue(bytes.fromhex(imported["macro_buffer"]).startswith(expected))
        export_keymap(self.path, imported, format="via")
        self.assertEqual(json.loads(self.path.read_text())["macros"], via["macros"])

    def test_via_rejects_backslash_before_macro_action(self):
        # VIA has no backslash escape: "\{KC_ENT}" would import as literal text.
        self.path.write_text("existing file")
        for expression in (r"\\{KC_ENT}", r"a\\{100}b", r"\\{+KC_LSFT}x{-KC_LSFT}"):
            source = snapshot()
            source["macro_buffer"] = encode([expression, ""], size=64).hex()
            with self.assertRaisesRegex(ValueError, "VIA cannot preserve macro 0.*Omakeeb or Vial"):
                export_keymap(self.path, source, format="via")
            self.assertEqual(self.path.read_text(), "existing file")
        source["macro_buffer"] = encode([r"\\\{{KC_ENT}", r"\\x{100}"], size=64).hex()
        export_keymap(self.path, source, format="via")
        self.assertEqual(json.loads(self.path.read_text())["macros"], ["\\\\{{KC_ENT}", "\\x{100}"])
        self.assertEqual(import_keymap(self.path, source)["macro_buffer"], source["macro_buffer"])

    def test_via_rejects_extended_vial_macro_actions(self):
        source = snapshot(vial=True)
        source["macro_buffer"] = encode(["{LGUI(KC_E)}", ""], vial_protocol=6, size=64).hex()
        with self.assertRaisesRegex(ValueError, "VIA cannot preserve these macros"):
            export_keymap(self.path, source, format="via")

    def test_atomic_replace_failure_preserves_previous_file(self):
        self.path.write_text('original')
        with patch('os.replace',side_effect=OSError('disk failure')):
            with self.assertRaises(KeymapFileError):
                export_keymap(self.path,snapshot())
        self.assertEqual(self.path.read_text(),'original')
        self.assertEqual(list(self.path.parent.iterdir()),[self.path])

    def test_boolean_keycodes_and_oversized_dynamic_fields_rejected(self):
        for kind in ('keycode','dynamic','lighting'):
            source = snapshot()
            if kind == 'keycode': source['layers'][0][0][0] = True
            elif kind == 'lighting': source['lighting']['brightness']=[256]
            else: source['dynamic']={'key_override':[[4,5,65535,256,0,0,0]]}
            with self.subTest(kind=kind),self.assertRaises(KeymapFileError):
                validate_snapshot(source)


if __name__ == '__main__':
    unittest.main()
