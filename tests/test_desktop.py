import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from omakeeb.desktop import (load_theme, theme_css, shortcuts_from_binds,
                             shortcut_keycode, discover_shortcuts, default_shortcuts)


class ThemeTests(unittest.TestCase):
    def test_current_state_path_and_safe_colors(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            current = home / '.local/state/omarchy/current'
            (current / 'theme').mkdir(parents=True)
            (current / 'theme/colors.toml').write_text('background="#ffffff"\nforeground="#111111"\naccent="red; * { opacity:0; }"\nmode="light"\n')
            (current / 'theme.name').write_text('paper\n')
            theme = load_theme(home)
            self.assertEqual(theme.name, 'paper')
            self.assertEqual(theme.mode, 'light')
            self.assertEqual(theme.colors['background'], '#ffffff')
            self.assertNotIn('opacity', theme_css(theme))
            self.assertTrue(int(theme.colors['lighter_background'][1:3],16) > 200)

    def test_legacy_path_aliases_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            theme = home / '.config/omarchy/current/theme'
            theme.mkdir(parents=True)
            source = theme / 'colors.toml'
            source.write_text('bg="#112233"\nfg="#ccddee"\ncolor4="#abcdef"\n')
            self.assertEqual(load_theme(home).colors['accent'], '#abcdef')
            source.write_text('background="#ffffff"\n')
            self.assertEqual(load_theme(home).colors['background'], '#ffffff')

    def test_invalid_theme_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            theme = home / '.local/state/omarchy/current/theme'
            theme.mkdir(parents=True)
            (theme / 'colors.toml').write_text('this is invalid TOML')
            self.assertIsNone(load_theme(home).source)


class ShortcutTests(unittest.TestCase):
    def test_qmk_modifiers_and_extended_function_keys(self):
        self.assertEqual(shortcut_keycode(('SUPER','SHIFT'), 'B'), 0x0a05)
        self.assertEqual(shortcut_keycode((), 'XF86Tools'), 104)
        self.assertEqual(shortcut_keycode((), 'F24'), 115)
        self.assertEqual(shortcut_keycode(('SUPER',), 'XF86AudioRaiseVolume'), 0x08a9)
        self.assertIsNone(shortcut_keycode((), 'XF86AudioMicMute'))
        self.assertEqual(shortcut_keycode((), 'XF86Calculator'), 0xb2)
        self.assertEqual(shortcut_keycode(('SUPER',), '', 10), 0x081e)
        self.assertEqual(shortcut_keycode(('SUPER','SHIFT'), '', 201), 0x0a72)

    def test_live_bindings_do_not_guess_unsupported_modifiers_or_submaps(self):
        binds = [
            {'description':'Custom terminal','key':'RETURN','modmask':64},
            {'description':'Mouse','key':'mouse:272','modmask':64,'mouse':True},
            {'description':'Submap','key':'X','modmask':0,'submap':'resize'},
            {'description':'Hyper','key':'X','modmask':128},
            {'description':'Long press','key':'X','modmask':64,'longPress':True},
            {'description':'Unknown keysym','key':'XF86Imaginary','modmask':0},
            {'description':'Physical workspace','key':'','keycode':19,'modmask':64},
            {'key':'X','modmask':0,'arg':'private shell command'},
        ]
        results = {item.label:item for item in shortcuts_from_binds(binds)}
        self.assertEqual(set(results), {'Custom terminal','Hyper','Long press','Unknown keysym','Physical workspace'})
        self.assertEqual(results['Custom terminal'].keycode, 0x0828)
        self.assertEqual(results['Physical workspace'].keycode, 0x0827)
        self.assertIsNone(results['Hyper'].keycode)
        self.assertIsNone(results['Long press'].keycode)
        self.assertIsNone(results['Unknown keysym'].keycode)

    def test_no_compositor_uses_defaults_without_process(self):
        with patch.dict('os.environ', {}, clear=True), patch('subprocess.run') as run:
            self.assertEqual(discover_shortcuts(), default_shortcuts())
            run.assert_not_called()

    def test_empty_live_binding_set_stays_empty(self):
        with patch.dict('os.environ', {'HYPRLAND_INSTANCE_SIGNATURE':'test'}), patch('subprocess.run') as run:
            run.return_value.stdout = '[]'
            self.assertEqual(discover_shortcuts(), [])
            self.assertEqual(run.call_args.args[0], ['hyprctl','-j','binds'])


if __name__ == '__main__':
    unittest.main()
