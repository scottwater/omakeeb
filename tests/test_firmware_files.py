import hashlib
import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from omakeeb.firmware_files import archive_firmware, verify_archive, FirmwareArchiveError


class FirmwareFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'vendor board.bin'
        self.data = b'test firmware\x00\x01\x02' * 100
        self.source.write_bytes(self.data)
        self.archive = self.root / 'archive'

    def test_archives_exact_bytes_with_provenance_and_permissions(self):
        manifest_path = archive_firmware(self.source,self.archive,{'vendor_id':123,'product':'Example','path':'private path'})
        manifest = verify_archive(manifest_path)
        self.assertFalse(manifest['read_from_keyboard'])
        self.assertFalse(manifest['compatibility_verified'])
        self.assertEqual(manifest['sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(manifest['source_name'],'vendor board.bin')
        self.assertNotIn('path',manifest['identity'])
        self.assertEqual((self.archive / manifest['filename']).read_bytes(),self.data)
        self.assertEqual(self.source.read_bytes(),self.data)
        self.assertEqual(self.archive.stat().st_mode & 0o777,0o700)
        self.assertEqual(manifest_path.stat().st_mode & 0o777,0o600)
        self.assertEqual((self.archive/manifest['filename']).stat().st_mode & 0o777,0o600)

    def test_tampering_is_detected(self):
        manifest_path = archive_firmware(self.source,self.archive)
        manifest = json.loads(manifest_path.read_text())
        (self.archive/manifest['filename']).write_bytes(b'changed')
        with self.assertRaisesRegex(FirmwareArchiveError,'checksum'):
            verify_archive(manifest_path)

    def test_manifest_cannot_escape_archive_or_follow_symlink(self):
        manifest_path = archive_firmware(self.source,self.archive)
        original = json.loads(manifest_path.read_text())
        for name in ('../vendor board.bin',str(self.source)):
            manifest = dict(original,filename=name)
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(FirmwareArchiveError,'filename'):
                verify_archive(manifest_path)
        manifest_path.write_text(json.dumps(original))
        archived = self.archive/original['filename']
        archived.unlink()
        archived.symlink_to(self.source)
        with self.assertRaises(FirmwareArchiveError):
            verify_archive(manifest_path)

    def test_rejects_empty_large_or_non_regular_sources(self):
        self.source.write_bytes(b'')
        with self.assertRaisesRegex(FirmwareArchiveError,'empty'):
            archive_firmware(self.source,self.archive)
        self.source.write_bytes(self.data)
        with patch('omakeeb.firmware_files.MAX_FIRMWARE_BYTES',4):
            with self.assertRaisesRegex(FirmwareArchiveError,'limit'):
                archive_firmware(self.source,self.archive)
        self.source.unlink()
        os.mkfifo(self.source)
        with self.assertRaisesRegex(FirmwareArchiveError,'regular'):
            archive_firmware(self.source,self.archive)
        self.assertEqual(list(self.archive.iterdir()),[])

    def test_rejects_public_destination_and_does_not_chmod_it(self):
        self.archive.mkdir(mode=0o755)
        with self.assertRaisesRegex(FirmwareArchiveError,'private'):
            archive_firmware(self.source,self.archive)
        self.assertEqual(self.archive.stat().st_mode & 0o777,0o755)


if __name__ == '__main__':
    unittest.main()
