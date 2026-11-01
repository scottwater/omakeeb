"""Byte-level fake keyboard to exercise the actual production protocol."""
from copy import deepcopy
import json
import lzma
import struct

from omakeeb.protocol import DYNAMIC_SPECS


class FakeKeyboard:
    identity = {"path": "fake://keyboard", "vendor_id": 0x1234, "product_id": 0x5678,
                "serial": "offline-test", "product": "Fake keyboard", "manufacturer": "Tests",
                "interface_number": 1, "release_number": 1}

    def __init__(self, via=12, vial=None, encoder=False, lighting=False):
        self.via, self.vial = via, vial
        self.keys = [[[4, 5, 6], [7, 8, 9]], [[1, 1, 1], [1, 1, 1]]]
        self.macros = bytearray(64)
        self.macro_count = 2
        self.encoders = [[[0x80, 0x81]], [[0x80, 0x81]]]
        self.definition = {"name": "Fake keyboard", "vendorId": "0x1234", "productId": "0x5678",
                           "matrix": {"rows": 2, "cols": 3},
                           "layouts": {"keymap": [["0,0", "0,1", "0,2"], ["1,0", "1,1", "1,2"]],
                                       "labels": ["Split space"]}}
        if encoder:
            self.definition["layouts"]["keymap"][0][0] += "\n\n\n\ne" + ("" if vial is not None else "0")
        self.light = {}
        if lighting:
            if vial is not None or via <= 10:
                self.definition["lighting"] = "qmk_rgblight"
                self.light = {(0x80,): [120], (0x81,): [1], (0x82,): [100], (0x83,): [12, 255]}
            else:
                self.definition["menus"] = ["qmk_rgb_matrix"]
                self.light = {(3, 1): [120], (3, 2): [1], (3, 3): [100], (3, 4): [12, 255]}
        self.layout_options = 0
        self.dynamic = {"tap_dance": [[4, 5, 0, 0, 200]], "combo": [[4, 5, 0, 0, 6]],
                        "key_override": [[4, 5, 0xFFFF, 0, 0, 0, 0]], "alt_repeat_key": [[4, 5, 0, 0]]}
        self.settings = {7: b"\xc8\0", 22: b"\0"}
        self.unlocked = True
        self.in_progress = False
        self.unlock_remaining = 3
        self.matrix_bits = [0b001, 0b100]
        self.sent = []
        self.writes = []
        self.fail_write_at = None
        self.drop_key_write = False
        self.unsupported_lighting = set()
        self.backlight_levels = None
        self.before_write = None
        self.closed = False

    @property
    def compressed(self):
        return lzma.compress(json.dumps(self.definition).encode())

    def exchange(self, request):
        request = bytes(request)
        self.sent.append(request)
        packet = bytearray(request.ljust(32, b"\0"))
        cmd = packet[0]
        is_write = cmd in (3, 5, 7, 9, 15, 21) or (cmd == 254 and packet[1] in (4, 11))
        is_write |= cmd == 254 and packet[1] == 13 and packet[2] in (2, 4, 6, 8)
        if is_write:
            if self.before_write:
                self.before_write(request)
            self.writes.append(request)
            if self.fail_write_at == len(self.writes):
                raise OSError("simulated disconnect during write")
        if cmd == 1:
            packet[1:3] = self.via.to_bytes(2, "big")
        elif cmd == 2:
            if packet[1] == 2:
                packet[2:6] = self.layout_options.to_bytes(4, "big")
            elif packet[1] == 3:
                packet[2:4] = bytes(self.matrix_bits)
            elif packet[1] == 6:
                packet[2:6] = (9).to_bytes(4, "big")
            else:
                raise AssertionError(request)
        elif cmd == 3:
            self.layout_options = int.from_bytes(packet[2:6], "big")
        elif cmd in (4, 5):
            l, r, c = packet[1:4]
            if cmd == 5 and not self.drop_key_write:
                self.keys[l][r][c] = int.from_bytes(packet[4:6], "big")
            if cmd == 4:
                packet[4:6] = self.keys[l][r][c].to_bytes(2, "big")
        elif cmd == 12:
            packet[1] = self.macro_count
        elif cmd == 13:
            packet[1:3] = len(self.macros).to_bytes(2, "big")
        elif cmd in (14, 15, 18):
            offset, length = int.from_bytes(packet[1:3], "big"), packet[3]
            assert length <= 28
            if cmd == 15:
                self.macros[offset:offset + length] = packet[4:4 + length]
            else:
                source = self.macros if cmd == 14 else b"".join(code.to_bytes(2, "big") for layer in self.keys for row in layer for code in row)
                packet[4:4 + length] = source[offset:offset + length]
        elif cmd == 17:
            packet[1] = len(self.keys)
        elif cmd in (20, 21):
            layer, index, direction = packet[1:4]
            if cmd == 21:
                self.encoders[layer][index][direction] = int.from_bytes(packet[4:6], "big")
            else:
                packet[4:6] = self.encoders[layer][index][direction].to_bytes(2, "big")
        elif cmd in (7, 8):
            address_size = 1 if self.vial is not None or self.via <= 10 else 2
            address = tuple(packet[1:1 + address_size])
            if cmd == 8 and address in self.unsupported_lighting:
                packet[0] = 255
                return bytes(packet)
            value = self.light[address]
            if cmd == 7:
                self.light[address] = list(packet[1 + address_size:1 + address_size + len(value)])
                if self.backlight_levels is not None and address in ((9,), (1, 1)):
                    request = self.light[address][0]
                    level = request * self.backlight_levels // 255
                    self.light[address] = [level * 255 // self.backlight_levels]
            else:
                packet[1 + address_size:1 + address_size + len(value)] = bytes(value)
        elif cmd == 9:
            pass
        elif cmd == 254:
            sub = packet[1]
            if self.vial is None:
                packet[0] = 255
            elif sub == 0:
                packet = bytearray(32)
                packet[:4] = self.vial.to_bytes(4, "little")
                packet[4:12] = (0x1122334455667788).to_bytes(8, "little")
            elif sub == 1:
                packet[:4] = len(self.compressed).to_bytes(4, "little")
            elif sub == 2:
                offset = int.from_bytes(packet[2:6], "little") * 32
                packet = bytearray(self.compressed[offset:offset + 32].ljust(32, b"\0"))
            elif sub == 3:
                layer, index = packet[2:4]
                packet[:4] = struct.pack(">HH", *self.encoders[layer][index])
            elif sub == 4:
                layer, index, direction = packet[2:5]
                self.encoders[layer][index][direction] = int.from_bytes(packet[5:7], "big")
            elif sub == 5:
                packet = bytearray([255] * 32)
                packet[:6] = bytes([self.unlocked, self.in_progress, 0, 0, 1, 2])
            elif sub == 6:
                self.in_progress = True
            elif sub == 7:
                self.unlock_remaining -= 1
                if self.unlock_remaining == 0:
                    self.unlocked, self.in_progress = True, False
                packet[:3] = bytes([self.unlocked, self.in_progress, self.unlock_remaining])
            elif sub == 8:
                self.unlocked = False
            elif sub == 9:
                packet = bytearray(b"\xff" * 32)
                packet[:4] = struct.pack("<HH", 7, 22)
            elif sub == 10:
                qsid = int.from_bytes(packet[2:4], "little")
                value = self.settings[qsid]
                packet[0] = 0
                packet[1:1 + len(value)] = value
            elif sub == 11:
                qsid = int.from_bytes(packet[2:4], "little")
                self.settings[qsid] = bytes(packet[4:4 + len(self.settings[qsid])])
                packet[0] = 0
            elif sub == 13:
                operation, index = packet[2:4]
                if operation == 0:
                    packet = bytearray(32)
                    packet[:4] = bytes(len(self.dynamic[name]) for name in DYNAMIC_SPECS)
                else:
                    for name, (getter, setter, fmt, _) in DYNAMIC_SPECS.items():
                        if operation == setter:
                            self.dynamic[name][index] = list(struct.unpack(fmt, packet[4:4 + struct.calcsize(fmt)]))
                            packet[0] = 0
                            break
                        if operation == getter:
                            value = struct.pack(fmt, *self.dynamic[name][index])
                            packet[0] = 0
                            packet[1:1 + len(value)] = value
                            break
            else:
                raise AssertionError(request)
        else:
            raise AssertionError(f"Unexpected command: {request!r}")
        assert len(packet) == 32
        return bytes(packet)

    def close(self):
        self.closed = True
