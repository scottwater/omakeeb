import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('omakeeb_installer',Path(__file__).resolve().parent.parent/'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        for folder in ('omakeeb','resources','scripts'):
            (self.source/folder).mkdir(parents=True)
        (self.source/'omakeeb/__init__.py').write_text('')
        (self.source/'omakeeb/__main__.py').write_text('from pathlib import Path\nprint((Path(__file__).resolve().parent.parent/"resources/value.txt").read_text())\n')
        (self.source/'resources/value.txt').write_text('first version')
        (self.source/f'resources/{installer.APP_ID}.svg').write_text('<svg><!-- Omakeeb application icon --></svg>')
        for file in ('README.md','LICENSE','THIRD_PARTY.md'):
            (self.source/file).write_text(file)
        self.data = self.root/'data with spaces'
        self.bin = self.root/'bin with spaces'

    def install(self):
        return installer.install(self.source,self.data,self.bin,dependencies=False)

    def test_installed_launcher_survives_moving_source(self):
        paths=self.install()
        shutil.rmtree(self.source)
        result=subprocess.run([str(paths['launcher'])],capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'first version')
        self.assertEqual(paths['launcher'].stat().st_mode & 0o777,0o755)
        self.assertIn(f'Exec="{paths["launcher"]}"',paths['desktop'].read_text())
        self.assertTrue((paths['app']/'LICENSE').is_file())

    def test_upgrade_and_uninstall_preserve_user_data(self):
        self.install()
        private=self.data/'omakeeb/backups'
        private.mkdir()
        (private/'keep.json').write_text('backup')
        definitions=self.data/'omakeeb/definitions'
        definitions.mkdir()
        (definitions/'custom.json').write_text('definition')
        (self.source/'resources/value.txt').write_text('second version')
        paths=self.install()
        result=subprocess.run([str(paths['launcher'])],capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'second version')
        installer.uninstall(self.data,self.bin)
        self.assertTrue((private/'keep.json').is_file())
        self.assertTrue((definitions/'custom.json').is_file())
        self.assertTrue(all(not path.exists() for path in paths.values()))
        installer.uninstall(self.data,self.bin)

    def test_unmanaged_files_and_symlinks_are_not_replaced(self):
        self.bin.mkdir()
        target=self.bin/'omakeeb'
        target.write_text('unrelated')
        with self.assertRaisesRegex(RuntimeError,'unmanaged'):
            self.install()
        self.assertEqual(target.read_text(),'unrelated')
        target.unlink()
        target.symlink_to(self.source/'README.md')
        with self.assertRaisesRegex(RuntimeError,'unmanaged'):
            self.install()
        self.assertTrue(target.is_symlink())

    def test_failed_upgrade_restores_previous_application_and_launcher(self):
        paths=self.install()
        original_launcher=paths['launcher'].read_text()
        (self.source/'resources/value.txt').write_text('second version')
        write=installer._write_atomic
        failed=False
        def fail_once(path,*args):
            nonlocal failed
            if path == paths['desktop'] and not failed:
                failed=True
                raise OSError('simulated write failure')
            return write(path,*args)
        with patch.object(installer,'_write_atomic',side_effect=fail_once):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual((paths['app']/'resources/value.txt').read_text(),'first version')
        self.assertEqual(paths['launcher'].read_text(),original_launcher)
        self.assertFalse(list(paths['app'].parent.glob('.app-*')))

    def test_desktop_exec_escapes_reserved_characters_and_field_codes(self):
        escaped=installer._desktop_exec(Path('/tmp/a $b%\\"/omakeeb'))
        self.assertIn('%%',escaped)
        self.assertIn('\\\\$',escaped)
        self.assertIn('\\\\"',escaped)
        self.assertTrue(escaped.startswith('"') and escaped.endswith('"'))


if __name__ == '__main__':
    unittest.main()
