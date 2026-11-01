import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from omakeeb.hid import DeviceInfo, grant_command, lacks_access, udev_rule


def device(path):
    return DeviceInfo(path=path, vendor_id=0x8968, product_id=0x4E54, serial="", manufacturer="",
                      product="NK", interface_number=1, release_number=1)


class HidAccessTests(unittest.TestCase):
    def test_rule_names_the_model(self):
        self.assertIn('ATTRS{idVendor}=="8968", ATTRS{idProduct}=="4e54"', udev_rule(device("/dev/hidraw0")))

    def test_only_unopenable_dev_nodes_need_a_rule(self):
        self.assertTrue(lacks_access(device("/dev/omakeeb-missing")))
        self.assertFalse(lacks_access(device("1-2:1.0")))
        self.assertFalse(lacks_access(device(os.devnull)))

    def test_grant_appends_the_rule_once_then_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp, "calls")
            fake = Path(tmp, "udevadm")
            fake.write_text(f'#!/bin/sh\necho "$*" >> {calls}\n')
            fake.chmod(0o755)
            rules = Path(tmp, "59-omakeeb.rules")
            command = grant_command(device("/dev/hidraw0"))[1:-1] + [str(rules)]
            env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"}
            for _ in range(2):
                subprocess.run(command, check=True, env=env)
            self.assertEqual(rules.read_text(), udev_rule(device("/dev/hidraw0")) + "\n")
            self.assertIn("trigger --subsystem-match=hidraw --action=change", calls.read_text())


if __name__ == "__main__":
    unittest.main()
