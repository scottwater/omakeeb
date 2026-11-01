"""Small Linux HIDAPI binding. Opening a device never changes its configuration.

Only QMK's vendor-defined raw-HID collection is eligible. No Python HID package,
WebHID permissions, daemon, root process, or firmware flasher is required.
"""

from __future__ import annotations

import ctypes as C
import ctypes.util
from dataclasses import asdict, dataclass
import threading


class HidError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    vendor_id: int
    product_id: int
    serial: str = ""
    manufacturer: str = ""
    product: str = "QMK keyboard"
    interface_number: int = -1
    release_number: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def label(self) -> str:
        return f"{self.product or 'QMK keyboard'} · {self.vendor_id:04X}:{self.product_id:04X}"


class _Device(C.Structure):
    pass


_Device._fields_ = [
    ("path", C.c_char_p), ("vendor_id", C.c_ushort), ("product_id", C.c_ushort),
    ("serial_number", C.c_wchar_p), ("release_number", C.c_ushort),
    ("manufacturer_string", C.c_wchar_p), ("product_string", C.c_wchar_p),
    ("usage_page", C.c_ushort), ("usage", C.c_ushort),
    ("interface_number", C.c_int), ("next", C.POINTER(_Device)),
]

_library = None
_library_lock = threading.Lock()


def _api():
    global _library
    with _library_lock:
        if _library is None:
            path = ctypes.util.find_library("hidapi-hidraw")
            if not path:
                raise HidError("Install the system hidapi package to connect a keyboard.")
            lib = C.CDLL(path)
            signatures = {
                "hid_init": ([], C.c_int),
                "hid_enumerate": ([C.c_ushort, C.c_ushort], C.POINTER(_Device)),
                "hid_free_enumeration": ([C.POINTER(_Device)], None),
                "hid_open_path": ([C.c_char_p], C.c_void_p),
                "hid_close": ([C.c_void_p], None),
                "hid_write": ([C.c_void_p, C.c_void_p, C.c_size_t], C.c_int),
                "hid_read_timeout": ([C.c_void_p, C.c_void_p, C.c_size_t, C.c_int], C.c_int),
                "hid_error": ([C.c_void_p], C.c_wchar_p),
            }
            for name, (args, result) in signatures.items():
                fn = getattr(lib, name)
                fn.argtypes, fn.restype = args, result
            if lib.hid_init() != 0:
                raise HidError("Could not initialize the system HID library.")
            _library = lib
    return _library


def enumerate_devices() -> list[DeviceInfo]:
    """Enumerate descriptors only; no command is sent to any keyboard."""
    lib = _api()
    head = lib.hid_enumerate(0, 0)
    devices = []
    try:
        node = head
        while node:
            item = node.contents
            if item.usage_page == 0xFF60 and item.usage == 0x61 and item.path:
                devices.append(DeviceInfo(
                    path=item.path.decode("utf-8", "surrogateescape"),
                    vendor_id=item.vendor_id, product_id=item.product_id,
                    serial=item.serial_number or "", manufacturer=item.manufacturer_string or "",
                    product=item.product_string or "QMK keyboard", interface_number=item.interface_number,
                    release_number=item.release_number,
                ))
            node = item.next
    finally:
        lib.hid_free_enumeration(head)
    return sorted(devices, key=lambda d: (d.product, d.path))


class HidTransport:
    """One request/response at a time, with no automatic retries.

    A timeout poisons the handle: a late response must never be mistaken for the
    next command's reply. Reconnection requires a fresh session and fresh backup.
    """

    def __init__(self, info: DeviceInfo, timeout_ms: int = 1200):
        if info not in enumerate_devices():
            raise HidError("The selected raw-HID interface changed. Scan and reconnect.")
        self.info = info
        self._lib = _api()
        self._lock = threading.RLock()
        self._failed = False
        self.timeout_ms = timeout_ms
        self._handle = self._lib.hid_open_path(info.path.encode("utf-8", "surrogateescape"))
        if not self._handle:
            raise HidError(
                "Cannot open this keyboard's raw-HID interface. Grant access with a "
                "device-specific udev rule, reconnect the keyboard, then scan again. "
                "Do not run Omakeeb as root."
            )

    def exchange(self, request: bytes) -> bytes:
        if not 1 <= len(request) <= 32:
            raise ValueError("Raw-HID requests must contain 1–32 bytes.")
        # Defense in depth: no direct reset/erase operations exist. Vial's
        # physical unlock handshake is allowed only through explicit UI actions.
        # The protocol layer narrows this
        # further to capability-checked writes performed by SafetySession.
        if request[0] not in {1, 2, 3, 4, 5, 7, 8, 9, 12, 13, 14, 15, 17, 18, 20, 21, 254}:
            raise HidError("Command is outside Omakeeb's safe configuration protocol.")
        if request[0] == 254 and (len(request) < 2 or request[1] not in {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13}):
            raise HidError("Unsupported Vial command.")
        with self._lock:
            if not self._handle or self._failed:
                raise HidError("This connection is closed or uncertain. Reconnect before continuing.")
            packet = C.create_string_buffer(b"\0" + request.ljust(32, b"\0"), 33)
            response = C.create_string_buffer(32)
            written = self._lib.hid_write(self._handle, packet, 33)
            if written != 33:
                self._failed = True
                raise HidError("HID request failed; its outcome may be unknown. Reconnect to inspect the keyboard.")
            received = self._lib.hid_read_timeout(self._handle, response, 32, self.timeout_ms)
            if received != 32:
                self._failed = True
                raise HidError("Keyboard disconnected, timed out, or returned a short reply. Reconnect to inspect it.")
            return response.raw

    def close(self):
        with self._lock:
            if self._handle:
                self._lib.hid_close(self._handle)
                self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def udev_rule(info: DeviceInfo) -> str:
    """Suggested per-model rule; deliberately never installs it itself."""
    return (f'SUBSYSTEM=="hidraw", ATTRS{{idVendor}}=="{info.vendor_id:04x}", '
            f'ATTRS{{idProduct}}=="{info.product_id:04x}", TAG+="uaccess"')
