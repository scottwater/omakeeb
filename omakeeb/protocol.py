"""Bounded VIA 9–13 / Vial 0–6 configuration protocol.

Packet layouts are interoperable with the public QMK VIA protocol and Vial.
This module deliberately has no flash, reset, erase, or bootloader operation.
All writes go through a validated, backup-first SafetySession.
"""

from __future__ import annotations

from functools import lru_cache, wraps
import hashlib
import json
import lzma
from pathlib import Path
import struct
import threading


class ProtocolError(RuntimeError):
    pass


class UnsupportedProtocol(ProtocolError):
    pass


class UnsupportedControl(ProtocolError):
    """An optional GET was explicitly rejected with VIA's unhandled marker."""


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapped


def definition_hash(definition: dict) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


@lru_cache(maxsize=1)
def setting_specs() -> dict[int, dict]:
    """Public Vial setting schemas, including exact widths and allowed ranges."""
    data = json.loads((Path(__file__).parent / "data" / "qmk_settings.json").read_text())
    specs = {}
    for tab in data["tabs"]:
        for field in tab["fields"]:
            qsid = field["qsid"]
            spec = specs.setdefault(qsid, {
                "title": field["title"], "group": tab["name"],
                "width": field.get("width", 1), "type": field["type"], "fields": [],
                "min": field.get("min", 0), "max": field.get("max", 0),
            })
            spec["fields"].append(field)
            if field["type"] == "boolean":
                spec["max"] |= 1 << field.get("bit", 0)
    return specs


def setting_width(qsid: int) -> int | None:
    spec = setting_specs().get(qsid)
    return spec["width"] if spec else None


def _backlight_quantization_matches(requested: int, actual: int) -> bool:
    """QMK maps 0–255 to a compiled integer BACKLIGHT_LEVELS and back.

    Firmware does not expose that constant. Accept only a value produced by
    its documented pair of integer divisions for some valid level count;
    this rule does not apply to RGB, effect, speed, or arbitrary controls.
    """
    return 0 <= actual <= requested <= 255 and any(
        ((requested * levels // 255) * 255 // levels) == actual
        for levels in range(1, 256)
    )


# entry getter, setter, wire layout, positions containing QMK keycodes
DYNAMIC_SPECS = {
    "tap_dance": (1, 2, "<HHHHH", (0, 1, 2, 3)),
    "combo": (3, 4, "<HHHHH", (0, 1, 2, 3, 4)),
    "key_override": (5, 6, "<HHHBBBB", (0, 1)),
    "alt_repeat_key": (7, 8, "<HHBB", (0, 1)),
}


class Protocol:
    def __init__(self, transport, identity: dict | None = None):
        self.transport = transport
        if identity is None:
            identity = transport.info.as_dict()
        self.identity = dict(identity)
        self.lock = threading.RLock()
        self.via_protocol = None
        self.vial_protocol = None
        self.keyboard_id = None
        self.keycodes_version = None
        self.lighting_specs = {}
        self.unsupported_controls = []
        self._poisoned = False

    def _exchange(self, payload: bytes, echo: int | None = None, *, optional=False) -> bytes:
        if self._poisoned:
            raise ProtocolError("This connection is uncertain. Disconnect and reconnect before continuing.")
        try:
            reply = bytes(self.transport.exchange(payload))
        except Exception:
            self._poisoned = True
            raise
        if len(reply) != 32:
            self._poisoned = True
            raise ProtocolError("Keyboard returned an incomplete raw-HID packet.")
        if optional and reply == b"\xff" + payload[1:].ljust(31, b"\0"):
            # QMK's explicit id_unhandled reply preserves the request payload.
            # Unlike a timeout or mismatched reply, this fully consumes the
            # request and leaves the stream synchronized. Only optional reads
            # can use this exception; writes always fail closed.
            raise UnsupportedControl("Firmware explicitly reports this optional control as unsupported.")
        if echo is not None and reply[:echo] != payload[:echo]:
            # Unsupported commands have id=0xff. Other mismatches can be stale
            # responses from competing software. Either way fail closed.
            self._poisoned = True
            raise ProtocolError(f"Keyboard rejected or mismatched command 0x{payload[0]:02x}; reconnect before continuing.")
        return reply

    @synchronized
    def probe(self) -> dict:
        reply = self._exchange(b"\x01", 1)
        via = int.from_bytes(reply[1:3], "big")
        if via not in range(9, 14):
            raise UnsupportedProtocol(f"VIA protocol {via} is unsupported; no configuration changes are allowed.")
        vial_reply = self._exchange(b"\xfe\x00")
        vial, uid = None, None
        if vial_reply != b"\xff" + bytes(31):
            candidate = int.from_bytes(vial_reply[:4], "little")
            if candidate not in range(7):
                raise UnsupportedProtocol(f"Unknown Vial protocol {candidate}; reconnect with supported firmware.")
            vial = candidate
            uid = f"{int.from_bytes(vial_reply[4:12], 'little'):016x}"
            if uid == "0000000000000000":
                raise ProtocolError("Vial returned an empty keyboard identity.")
        keycodes_version = None
        if via >= 13 and vial is None:
            response = self._exchange(b"\x02\x06", 2)
            keycodes_version = int.from_bytes(response[2:6], "big")
            if keycodes_version not in (8, 9):
                raise UnsupportedProtocol(f"QMK keycode version {keycodes_version} is unsupported.")
        self.via_protocol, self.vial_protocol = via, vial
        self.keyboard_id, self.keycodes_version = uid, keycodes_version
        return {"via_protocol": via, "vial_protocol": vial, "keyboard_id": uid,
                "keycodes_version": keycodes_version}

    @synchronized
    def read_vial_definition(self) -> dict | None:
        if self.via_protocol is None:
            self.probe()
        if self.vial_protocol is None:
            return None
        size = int.from_bytes(self._exchange(b"\xfe\x01")[:4], "little")
        if not 1 <= size <= 256 * 1024:
            raise ProtocolError("Vial definition size is invalid or exceeds the safety limit.")
        compressed = bytearray()
        for offset in range(0, size, 32):
            data = self._exchange(struct.pack("<BBI", 254, 2, offset // 32))
            compressed.extend(data[:min(size - offset, 32)])
        try:
            decoder = lzma.LZMADecompressor(memlimit=16 * 1024 * 1024)
            decoded = decoder.decompress(bytes(compressed), max_length=2 * 1024 * 1024 + 1)
            if len(decoded) > 2 * 1024 * 1024 or not decoder.eof or decoder.unused_data:
                raise ValueError("Definition decompression limit or trailing data")
            definition = json.loads(decoded)
            if not isinstance(definition, dict):
                raise ValueError("Definition is not an object")
        except (ValueError, lzma.LZMAError, UnicodeError) as exc:
            raise ProtocolError(f"Invalid Vial definition: {exc}") from exc
        return definition

    def _read_buffer(self, command: int, size: int) -> bytes:
        if not 0 <= size <= 65535:
            raise ProtocolError("Invalid buffer size.")
        data = bytearray()
        for offset in range(0, size, 28):
            length = min(28, size - offset)
            reply = self._exchange(struct.pack(">BHB", command, offset, length), 4)
            data.extend(reply[4:4 + length])
        return bytes(data)

    def _definition_dimensions(self, definition):
        try:
            rows, cols = definition["matrix"]["rows"], definition["matrix"]["cols"]
            if type(rows) is not int or type(cols) is not int or not 1 <= rows <= 32 or not 1 <= cols <= 32:
                raise ValueError("matrix dimensions must be 1–32")
            for name, attr in (("vendorId", "vendor_id"), ("productId", "product_id")):
                if name in definition:
                    value = definition[name]
                    value = int(value, 0) if isinstance(value, str) else value
                    if value != self.identity[attr]:
                        raise ValueError("definition VID/PID differs from the selected device")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError(f"Invalid keyboard definition: {exc}") from exc
        return rows, cols

    def _encoder_count(self, definition) -> int:
        from .definitions import load_definition
        try:
            parsed = load_definition(definition, vial=self.vial_protocol is not None)
        except ValueError as exc:
            raise ProtocolError(f"Invalid physical layout: {exc}") from exc
        return parsed.encoder_count

    def _lighting_config(self, definition):
        specs = {}
        self.unsupported_controls = []

        def add(name, address, length=1, maximum=255, **extra):
            specs[name] = {"address": list(address), "length": length, "min": 0,
                           "max": maximum, "title": name.replace("_", " ").title(), **extra}

        if self.vial_protocol is not None or self.via_protocol <= 10:
            kind = definition.get("lighting")
            if isinstance(kind, dict):
                kind = kind.get("extends")
            if kind in ("qmk_backlight", "qmk_backlight_rgblight"):
                add("backlight_brightness", [9])
                add("backlight_effect", [10], maximum=1)
            if kind in ("qmk_rgblight", "qmk_backlight_rgblight"):
                for name, code in (("brightness", 0x80), ("effect", 0x81), ("speed", 0x82)):
                    add("underglow_" + name, [code])
                add("underglow_color", [0x83], length=2, components=["Hue", "Saturation"])
            if kind == "vialrgb" and self.vial_protocol is not None:
                try:
                    info = self._exchange(b"\x08\x40", 2, optional=True)
                except UnsupportedControl:
                    self.unsupported_controls.append("vialrgb_mode")
                    self.lighting_specs = specs
                    return specs
                if int.from_bytes(info[2:4], "little") != 1:
                    raise UnsupportedProtocol("Unsupported Vial RGB protocol.")
                effects, cursor = {0}, 0
                for _ in range(256):
                    reply = self._exchange(struct.pack("<BBH", 8, 0x42, cursor), 2)
                    values = [int.from_bytes(reply[i:i + 2], "little") for i in range(2, 32, 2)]
                    effects.update(v for v in values if v != 65535)
                    if 65535 in values:
                        break
                    if max(values) <= cursor:
                        raise ProtocolError("Vial RGB effect enumeration did not advance.")
                    cursor = max(values)
                else:
                    raise ProtocolError("Vial RGB effect enumeration exceeded the limit.")
                add("vialrgb_mode", [0x41], length=6, effects=sorted(effects), brightness_max=info[4],
                    components=["Effect low byte", "Effect high byte", "Speed", "Hue", "Saturation", "Brightness"])
        else:
            menus = definition.get("menus", [])
            for kind, channel, prefix in (("qmk_backlight", 1, "backlight"),
                                          ("qmk_rgblight", 2, "underglow"),
                                          ("qmk_rgb_matrix", 3, "rgb_matrix"),
                                          ("qmk_led_matrix", 5, "led_matrix")):
                if kind not in menus:
                    continue
                add(prefix + "_brightness", [channel, 1])
                add(prefix + "_effect", [channel, 2], maximum=1 if channel == 1 else 255)
                if channel != 1:
                    add(prefix + "_speed", [channel, 3])
                if channel in (2, 3):
                    add(prefix + "_color", [channel, 4], length=2, components=["Hue", "Saturation"])
        self.lighting_specs = specs
        return specs

    def _read_lighting(self):
        result = {}
        for name, spec in list(self.lighting_specs.items()):
            request = bytes([8, *spec["address"]])
            try:
                reply = self._exchange(request, len(request), optional=True)
            except UnsupportedControl:
                self.unsupported_controls.append(name)
                del self.lighting_specs[name]
                continue
            result[name] = list(reply[len(request):len(request) + spec["length"]])
        return result

    def _read_dynamic(self):
        entries = {name: [] for name in DYNAMIC_SPECS}
        if self.vial_protocol is None or self.vial_protocol < 4:
            return entries
        counts = self._exchange(b"\xfe\x0d\x00")[:4]
        for (name, (getter, _, fmt, _)), count in zip(DYNAMIC_SPECS.items(), counts):
            if name == "key_override" and self.vial_protocol < 5:
                count = 0
            if name == "alt_repeat_key" and self.vial_protocol < 6:
                count = 0
            if count > 128:
                raise ProtocolError("Dynamic feature count exceeds the safety limit.")
            for index in range(count):
                reply = self._exchange(bytes([254, 13, getter, index]))
                if reply[0] != 0:
                    raise ProtocolError(f"Cannot read {name} entry {index}.")
                entries[name].append(list(struct.unpack(fmt, reply[1:1 + struct.calcsize(fmt)])))
        return entries

    def _read_settings(self):
        settings = {}
        if self.vial_protocol is None or self.vial_protocol < 4:
            return settings
        ids, cursor = set(), 0
        for _ in range(256):
            reply = self._exchange(struct.pack("<BBH", 254, 9, cursor))
            values = [int.from_bytes(reply[i:i + 2], "little") for i in range(0, 32, 2)]
            ids.update(value for value in values if value != 65535)
            if 65535 in values:
                break
            if max(values) <= cursor:
                raise ProtocolError("QMK setting enumeration did not advance.")
            cursor = max(values)
        else:
            raise ProtocolError("QMK setting enumeration exceeded the safety limit.")
        for qsid in sorted(ids):
            reply = self._exchange(struct.pack("<BBH", 254, 10, qsid))
            if reply[0] != 0:
                raise ProtocolError(f"Cannot read QMK setting {qsid}.")
            settings[str(qsid)] = reply[1:1 + (setting_width(qsid) or 31)].hex()
        return settings

    @synchronized
    def read_snapshot(self, definition: dict) -> dict:
        self.probe()
        rows, cols = self._definition_dimensions(definition)
        if self.vial_protocol is not None:
            embedded = self.read_vial_definition()
            if definition_hash(embedded) != definition_hash(definition):
                raise ProtocolError("Vial's embedded definition changed. Reconnect to inspect it.")
        count = self._exchange(b"\x11", 1)[1]
        if not 1 <= count <= 32 or rows * cols * count * 2 > 65535:
            raise ProtocolError("Invalid layer count or keymap capacity.")
        raw = self._read_buffer(0x12, rows * cols * count * 2)
        words = list(struct.unpack(">" + "H" * (len(raw) // 2), raw))
        layers = [[words[(l * rows + r) * cols:(l * rows + r + 1) * cols]
                   for r in range(rows)] for l in range(count)]
        macro_count = self._exchange(b"\x0c", 1)[1]
        macro_size = int.from_bytes(self._exchange(b"\x0d", 1)[1:3], "big")
        if macro_count > 128 or macro_size > 32768 or macro_count > macro_size:
            raise ProtocolError("Invalid macro capacity.")
        macros = self._read_buffer(14, macro_size)
        encoder_count = self._encoder_count(definition)
        if encoder_count and self.vial_protocol is None and self.via_protocol < 10:
            raise UnsupportedProtocol("This VIA version does not provide a safe encoder mapping protocol.")
        encoders = []
        if encoder_count:
            for layer in range(count):
                values = []
                for index in range(encoder_count):
                    if self.vial_protocol is not None:
                        reply = self._exchange(bytes([254, 3, layer, index]))
                        values.append(list(struct.unpack(">HH", reply[:4])))
                    else:
                        pair = []
                        for direction in range(2):
                            reply = self._exchange(bytes([20, layer, index, direction]), 4)
                            pair.append(int.from_bytes(reply[4:6], "big"))
                        values.append(pair)
                encoders.append(values)
        layout_options = None
        if definition.get("layouts", {}).get("labels"):
            layout_options = int.from_bytes(self._exchange(b"\x02\x02", 2)[2:6], "big")
        self._lighting_config(definition)
        lighting = self._read_lighting()
        return {
            "schema_version": 1, "identity": dict(self.identity),
            "definition_sha256": definition_hash(definition),
            "via_protocol": self.via_protocol, "vial_protocol": self.vial_protocol,
            "keyboard_id": self.keyboard_id, "keycodes_version": self.keycodes_version,
            "matrix": {"rows": rows, "cols": cols}, "layers": layers,
            "macro_count": macro_count, "macro_buffer": macros.hex(),
            "encoders": encoders, "layout_options": layout_options,
            "lighting": lighting, "unsupported_controls": sorted(self.unsupported_controls),
            "dynamic": self._read_dynamic(),
            "settings": self._read_settings(),
        }

    @synchronized
    def read_unlock_status(self) -> dict:
        if self.via_protocol is None:
            self.probe()
        if self.vial_protocol is None:
            return {"unlocked": True, "in_progress": False, "keys": []}
        data = self._exchange(b"\xfe\x05")
        if data[0] not in (0, 1):
            raise ProtocolError("Unrecognized Vial lock state.")
        return {"unlocked": bool(data[0]), "in_progress": bool(data[1]), "keys": [
            (data[i], data[i + 1]) for i in range(2, 32, 2)
            if data[i] != 255 and data[i + 1] != 255
        ]}

    @synchronized
    def begin_unlock(self) -> dict:
        """Start Vial's physical-key handshake only on explicit user action.

        Firmware decides whether the keys are physically held. There is no
        software bypass. A user aborting midway must reconnect the keyboard.
        """
        status = self.read_unlock_status()
        if self.vial_protocol is None or status["unlocked"]:
            return status
        if not status["keys"]:
            raise ProtocolError("Firmware did not provide a physical unlock combination.")
        self._exchange(b"\xfe\x06")
        return {**status, "in_progress": True}

    @synchronized
    def poll_unlock(self) -> dict:
        """Poll at approximately 200 ms while the user holds the displayed keys."""
        if self.vial_protocol is None:
            return {"unlocked": True, "in_progress": False, "remaining": 0}
        reply = self._exchange(b"\xfe\x07")
        if reply[0] not in (0, 1) or reply[1] not in (0, 1):
            raise ProtocolError("Invalid Vial unlock response.")
        return {"unlocked": bool(reply[0]), "in_progress": bool(reply[1]), "remaining": reply[2]}

    @synchronized
    def lock_keyboard(self) -> dict:
        status = self.read_unlock_status()
        if self.vial_protocol is None:
            return status
        if status["in_progress"]:
            raise ProtocolError("Reconnect the keyboard to cancel an unfinished physical unlock.")
        self._exchange(b"\xfe\x08")
        status = self.read_unlock_status()
        if status["unlocked"]:
            raise ProtocolError("Firmware did not lock; it may have been compiled with VIAL_INSECURE.")
        return status

    @synchronized
    def read_matrix_state(self, definition: dict) -> set[tuple[int, int]]:
        if self.via_protocol is None:
            self.probe()
        if self.vial_protocol is not None:
            if self.vial_protocol < 3 or not self.read_unlock_status()["unlocked"]:
                raise ProtocolError("Matrix testing requires a supported, physically unlocked Vial keyboard.")
        rows, cols = self._definition_dimensions(definition)
        bytes_per_row = (cols + 7) // 8
        if rows * bytes_per_row > 28:
            raise UnsupportedProtocol("This matrix is too large for a single safe test report.")
        data = self._exchange(b"\x02\x03", 2)
        return {(row, col) for row in range(rows) for col in range(cols)
                if int.from_bytes(data[2 + row * bytes_per_row:2 + (row + 1) * bytes_per_row], "big") & (1 << col)}

    def _write_key(self, layer, row, col, value):
        self._exchange(struct.pack(">BBBBH", 5, layer, row, col, value), 6)
        reply = self._exchange(bytes([4, layer, row, col]), 4)
        if int.from_bytes(reply[4:6], "big") != value:
            raise ProtocolError("Key mapping readback did not match the requested value.")

    def _write_encoder(self, layer, index, direction, value):
        if self.vial_protocol is not None:
            self._exchange(struct.pack(">BBBBBH", 254, 4, layer, index, direction, value))
            reply = self._exchange(bytes([254, 3, layer, index]))
            actual = int.from_bytes(reply[direction * 2:direction * 2 + 2], "big")
        else:
            self._exchange(struct.pack(">BBBBH", 21, layer, index, direction, value), 6)
            reply = self._exchange(bytes([20, layer, index, direction]), 4)
            actual = int.from_bytes(reply[4:6], "big")
        if actual != value:
            raise ProtocolError("Encoder readback did not match the requested value.")

    def _write_macro_buffer(self, old: bytes, new: bytes):
        # VIA uses the last buffer byte as a write-in-progress flag. Never
        # reset/erase macros. Keep the flag set on failure so partial macros
        # cannot execute; reconnect and restore the durable backup explicitly.
        if not new or new[-1] != 0:
            raise ProtocolError("Macro buffers must reserve a final zero terminator.")
        marker = struct.pack(">BHB", 15, len(new) - 1, 1) + b"\xff"
        self._exchange(marker, len(marker))
        marked = self._exchange(struct.pack(">BHB", 14, len(new) - 1, 1), 4)
        if marked[4] != 0xFF:
            # Vial can echo a SET it silently ignored after being relocked.
            # Never rewrite macro payload while its execution guard is absent.
            raise ProtocolError("Macro write-in-progress marker was not accepted; no macro data was written.")
        for offset in range(0, len(new) - 1, 28):
            length = min(28, len(new) - 1 - offset)
            if old[offset:offset + length] == new[offset:offset + length]:
                continue
            chunk = new[offset:offset + length]
            packet = struct.pack(">BHB", 15, offset, length) + chunk
            self._exchange(packet, len(packet))
            reply = self._exchange(struct.pack(">BHB", 14, offset, length), 4)
            if reply[4:4 + length] != chunk:
                raise ProtocolError("Macro readback failed; macros remain disabled pending recovery.")
        finish = struct.pack(">BHB", 15, len(new) - 1, 1) + b"\0"
        self._exchange(finish, len(finish))
        if self._read_buffer(14, len(new)) != new:
            raise ProtocolError("Macro buffer verification failed.")

    def _write_changes(self, baseline: dict, staged: dict):
        """Private: SafetySession validates and backs up before calling this."""
        normalizations = []
        if not self.read_unlock_status()["unlocked"]:
            raise ProtocolError("This Vial keyboard is locked. Physically unlock it before applying changes.")
        for l, layer in enumerate(staged["layers"]):
            for r, row in enumerate(layer):
                for c, code in enumerate(row):
                    if code != baseline["layers"][l][r][c]:
                        self._write_key(l, r, c, code)
        for l, layer in enumerate(staged["encoders"]):
            for index, pair in enumerate(layer):
                for direction, code in enumerate(pair):
                    if code != baseline["encoders"][l][index][direction]:
                        self._write_encoder(l, index, direction, code)
        if staged["macro_buffer"] != baseline["macro_buffer"]:
            self._write_macro_buffer(bytes.fromhex(baseline["macro_buffer"]), bytes.fromhex(staged["macro_buffer"]))
        if staged["layout_options"] != baseline["layout_options"]:
            packet = struct.pack(">BBI", 3, 2, staged["layout_options"])
            self._exchange(packet, len(packet))
            actual = int.from_bytes(self._exchange(b"\x02\x02", 2)[2:6], "big")
            if actual != staged["layout_options"]:
                raise ProtocolError("Layout option readback failed.")
        channels = set()
        for name, value in staged["lighting"].items():
            if value != baseline["lighting"][name]:
                spec = self.lighting_specs[name]
                prefix = bytes([7, *spec["address"]])
                packet = prefix + bytes(value)
                self._exchange(packet, len(prefix))
                get = bytes([8, *spec["address"]])
                reply = self._exchange(get, len(get))
                actual = list(reply[len(get):len(get) + len(value)])
                if actual != value:
                    if (name == "backlight_brightness" and spec["address"] in ([9], [1, 1])
                            and len(value) == 1 and _backlight_quantization_matches(value[0], actual[0])):
                        normalizations.append(
                            f"Backlight brightness: requested {value[0]}, firmware applied {actual[0]} "
                            "using its supported brightness levels."
                        )
                        staged["lighting"][name] = actual
                    else:
                        raise ProtocolError(f"Lighting readback failed for {name}.")
                channels.add(spec["address"][0] if self.vial_protocol is None and self.via_protocol >= 11 else None)
        for channel in sorted(channels, key=lambda value: -1 if value is None else value):
            request = b"\x09" if channel is None else bytes([9, channel])
            self._exchange(request, len(request))
        for name, entries in staged["dynamic"].items():
            getter, setter, fmt, _ = DYNAMIC_SPECS[name]
            for index, entry in enumerate(entries):
                if entry == baseline["dynamic"][name][index]:
                    continue
                self._exchange(bytes([254, 13, setter, index]) + struct.pack(fmt, *entry))
                reply = self._exchange(bytes([254, 13, getter, index]))
                if reply[0] != 0 or list(struct.unpack(fmt, reply[1:1 + struct.calcsize(fmt)])) != entry:
                    raise ProtocolError(f"Readback failed for {name} entry {index}.")
        for qsid, value in staged["settings"].items():
            if value == baseline["settings"][qsid]:
                continue
            payload = bytes.fromhex(value)
            reply = self._exchange(struct.pack("<BBH", 254, 11, int(qsid)) + payload)
            if reply[0] != 0:
                raise ProtocolError(f"QMK setting {qsid} was rejected.")
            reply = self._exchange(struct.pack("<BBH", 254, 10, int(qsid)))
            if reply[0] != 0 or reply[1:1 + len(payload)] != payload:
                raise ProtocolError(f"Readback failed for QMK setting {qsid}.")
        return normalizations

    def close(self):
        self.transport.close()
