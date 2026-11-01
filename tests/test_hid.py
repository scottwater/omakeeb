import os
import tempfile
import unittest

from omakeeb.hid import DeviceInfo, lacks_access, udev_rule


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


if __name__ == "__main__":
    unittest.main()
