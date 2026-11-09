import tempfile
import unittest
from pathlib import Path
from omakeeb import compose, macros


class ComposeTests(unittest.TestCase):
    def test_parse_includes_overrides_and_macros(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / 'omarchy').write_text('include "%L"\n<Multi_key> <m> <s> : "😄" # smile\n'
                                          '<Multi_key> <m> <a> : "💪" # arm\n<Multi_key> <space> <space> : "—"\n')
            (home / '.XCompose').write_text('include "%H/omarchy"\ninclude "%H/.XCompose"\n'
                                            '<Multi_key> <m> <a> <e> : "a\\"b" # email\n'
                                            '<Multi_key> <braceleft> <x> : "x"\n<Multi_key> <Up> : "up"\n<dead_acute> <e> : "é"\n')
            entries = compose.load_entries(home, environ={})
        by_keys = {e.keys: e for e in entries}
        self.assertEqual(list(by_keys), [('m', 's'), ('space', 'space'), ('m', 'a', 'e'), ('braceleft', 'x'), ('Up',)])
        self.assertEqual(by_keys['m', 'a', 'e'].output, 'a"b')
        self.assertEqual(by_keys['m', 's'].comment, 'smile')
        self.assertEqual(by_keys['space', 'space'].sequence, 'Space Space')
        key = compose.compose_key(compose.OMARCHY_OPTIONS)
        expressions = [compose.macro_expression(e, key) for e in entries]
        self.assertEqual(expressions[:4], ['{KC_LSFT,KC_CAPS}ms', '{KC_LSFT,KC_CAPS}  ', '{KC_LSFT,KC_CAPS}mae', '{KC_LSFT,KC_CAPS}\\{x'])
        self.assertIsNone(expressions[4])
        for protocol, vial in ((12, None), (12, 6), (9, None)):
            data = macros.encode(expressions[:4], protocol=protocol, vial_protocol=vial)
            decoded = [e.replace('{KC_LSFT,KC_CAPS}', '{+KC_LSFT}{+KC_CAPS}{-KC_CAPS}{-KC_LSFT}') for e in expressions[:4]]
            self.assertEqual(macros.decode(data, 4, protocol=protocol, vial_protocol=vial), decoded)

    def test_compose_key_options(self):
        self.assertEqual(compose.compose_key('grp:alts_toggle,compose:ralt'), '{KC_RALT}')
        self.assertIsNone(compose.compose_key('grp:alts_toggle'))
        self.assertIsNone(compose.compose_key(None))
        self.assertIsNone(compose.macro_expression(compose.ComposeEntry(('a',), 'x'), None))


if __name__ == '__main__':
    unittest.main()
