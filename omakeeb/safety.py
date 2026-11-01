"""Durable configuration backups and optimistic, verified device transactions.

USB EEPROM writes cannot be atomic. Failed writes are never automatically
retried or rolled back: retain the backup and require a fresh read/reconnection.
Configuration backup does not imply executable firmware can be read back.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import uuid

from .protocol import DYNAMIC_SPECS, ProtocolError, definition_hash, setting_specs, setting_width


class SafetyError(RuntimeError):
    pass


class StaleSnapshot(SafetyError):
    pass


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def default_backup_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "omakeeb" / "backups"


class BackupStore:
    def __init__(self, directory: Path | str | None = None):
        self.directory = Path(directory) if directory is not None else default_backup_dir()

    def save(self, snapshot: dict, definition: dict, reason: str = "before-edit") -> Path:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Refuse a world-readable existing destination: macros may contain
        # private text. Do not silently change permissions on user directories.
        if self.directory.stat().st_mode & 0o077:
            raise SafetyError(f"Backup directory must be private (chmod 700): {self.directory}")
        payload = {
            "format": "omakeeb-backup", "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(), "reason": reason,
            "firmware_backup": False, "snapshot": deepcopy(snapshot), "definition": deepcopy(definition),
        }
        payload["sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
        ident = snapshot.get("identity", {})
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        name = f"{timestamp}-{ident.get('vendor_id', 0):04x}-{ident.get('product_id', 0):04x}-{uuid.uuid4().hex[:8]}.json"
        destination = self.directory / name
        temporary = self.directory / ("." + name + ".tmp")
        fd = None
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                fd = None
                stream.write(json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False).encode() + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            if fd is not None:
                os.close(fd)
            temporary.unlink(missing_ok=True)
            raise
        return destination

    def list(self) -> list[Path]:
        return sorted(self.directory.glob("*.json"), reverse=True)

    def load(self, path: Path | str) -> dict:
        path = Path(path)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise SafetyError("Backup exceeds the size limit.")
        try:
            payload = json.loads(path.read_text())
            if payload.get("format") != "omakeeb-backup" or payload.get("version") != 1:
                raise ValueError("Unsupported backup format")
            digest = payload.pop("sha256")
            if hashlib.sha256(_canonical(payload)).hexdigest() != digest:
                raise ValueError("Backup checksum does not match")
            if definition_hash(payload["definition"]) != payload["snapshot"]["definition_sha256"]:
                raise ValueError("Backup definition does not match its snapshot")
            payload["sha256"] = digest
        except (KeyError, TypeError, ValueError) as exc:
            raise SafetyError(f"Cannot load backup: {exc}") from exc
        return payload


_EDITABLE = {"layers", "encoders", "macro_buffer", "lighting", "layout_options", "dynamic", "settings"}
# Block newly introduced firmware/EEPROM operations, even if imported as a raw
# keycode. Existing assignments are preserved in reads and backups.
def dangerous_keycodes(snapshot: dict) -> frozenset[int]:
    """Firmware/EEPROM actions for the negotiated keycode namespace.

    Vial's v5/v6 tables pin its layouts. Old VIA does not report a QMK keycode
    revision: include the verified legacy variants (QMK 0.17.9's VIA_ENABLE
    changes the MIDI enum and hence subsequent loose keycodes by one).

    0x999D is deliberately absent: Vial GUI's v5 table labels it a fake
    placeholder, not a real firmware reboot keycode. Likewise 0x5C02 is a
    legacy modifier swap, and modern 0x7C02 toggles debug logging.
    """
    vial = snapshot.get("vial_protocol")
    if vial is not None:
        if type(vial) is not int or not 0 <= vial <= 6:
            raise SafetyError("Cannot validate keycode actions for an unknown Vial version.")
        if vial < 6:
            return frozenset({0x5C00, 0x5CDF})
        return frozenset({0x7C00, 0x7C01, 0x7C03})
    via = snapshot.get("via_protocol")
    if type(via) is not int or not 9 <= via <= 13:
        raise SafetyError("Cannot validate keycode actions for an unknown VIA version.")
    if via < 11:
        return frozenset({0x5C00, 0x5CDE, 0x5CDF, 0x5DA7, 0x5DA8})
    return frozenset({0x7C00, 0x7C01, 0x7C03})


def _uint(value, maximum=65535):
    return type(value) is int and 0 <= value <= maximum


def validate_new_keycode(value: int, old: int | None, snapshot: dict):
    """Shared UI/backend guard; unchanged existing assignments are preserved."""
    if not _uint(value):
        raise SafetyError("Keycodes must be unsigned 16-bit integers.")
    if value != old and value in dangerous_keycodes(snapshot):
        raise SafetyError("Bootloader, reset, and EEPROM-clearing keycodes cannot be assigned by Omakeeb.")


def _hex(value, length):
    if not isinstance(value, str) or len(value) != length * 2 or not re.fullmatch(r"[0-9a-f]*", value):
        raise SafetyError("Binary settings must retain their exact length and use lowercase hexadecimal.")
    return bytes.fromhex(value)


def validate_staged(baseline: dict, staged: dict, lighting_specs: dict | None = None):
    """Reject malformed/imported data before sending any configuration write."""
    if not isinstance(staged, dict) or set(staged) != set(baseline):
        raise SafetyError("The staged keymap does not match this snapshot's schema.")
    for name in baseline.keys() - _EDITABLE:
        if staged[name] != baseline[name] or type(staged[name]) is not type(baseline[name]):
            raise SafetyError(f"Device metadata cannot be changed: {name}.")
    for name in ("layers", "encoders"):
        before, after = baseline[name], staged[name]
        if not isinstance(after, list) or len(after) != len(before):
            raise SafetyError(f"{name} layer count differs from this keyboard.")
        for l, (old_layer, layer) in enumerate(zip(before, after)):
            if not isinstance(layer, list) or len(layer) != len(old_layer):
                raise SafetyError(f"{name} row/encoder count differs on layer {l}.")
            for old_row, row in zip(old_layer, layer):
                if not isinstance(row, list) or len(row) != len(old_row):
                    raise SafetyError(f"{name} matrix dimensions differ.")
                for old, value in zip(old_row, row):
                    validate_new_keycode(value, old, baseline)
    if staged["macro_buffer"] != baseline["macro_buffer"]:
        raw = _hex(staged["macro_buffer"], len(baseline["macro_buffer"]) // 2)
        if not raw or raw[-1] != 0:
            raise SafetyError("Macros must reserve their final buffer byte as a zero terminator.")
        from .macros import decode, encode, expression_actions
        from .keycodes import parse, keycode_protocol
        try:
            expressions = decode(raw, baseline["macro_count"], protocol=baseline["via_protocol"], vial_protocol=baseline["vial_protocol"])
            # Re-encoding also validates balanced presses/releases; decoding
            # alone intentionally preserves old macros for export.
            encode(expressions, protocol=baseline["via_protocol"], size=len(raw), vial_protocol=baseline["vial_protocol"])
            vial = baseline["vial_protocol"]
            dialect = keycode_protocol(baseline)
            try:
                old_expressions = decode(bytes.fromhex(baseline["macro_buffer"]), baseline["macro_count"],
                                         protocol=baseline["via_protocol"], vial_protocol=vial)
            except (ValueError, TypeError):
                old_expressions = []
            for index, expression in enumerate(expressions):
                # Editing another macro must not silently alter an existing
                # dangerous binding. An unchanged macro is preserved verbatim.
                if index < len(old_expressions) and expression == old_expressions[index]:
                    continue
                for action, value in expression_actions(expression):
                    if action not in ("text", "delay"):
                        validate_new_keycode(parse(value, protocol=dialect), None, baseline)
        except (ValueError, TypeError) as exc:
            raise SafetyError(f"Invalid macro buffer: {exc}") from exc
    if staged["layout_options"] != baseline["layout_options"]:
        if baseline["layout_options"] is None or not _uint(staged["layout_options"], 0xFFFFFFFF):
            raise SafetyError("Unsupported or invalid layout options.")
    if not isinstance(staged["lighting"], dict) or set(staged["lighting"]) != set(baseline["lighting"]):
        raise SafetyError("Lighting capabilities must match this keyboard.")
    for name, value in staged["lighting"].items():
        if not isinstance(value, list) or len(value) != len(baseline["lighting"][name]) or not all(_uint(v, 255) for v in value):
            raise SafetyError(f"Invalid lighting payload: {name}.")
        if value != baseline["lighting"][name]:
            spec = (lighting_specs or {}).get(name)
            if spec is None:
                raise SafetyError(f"Lighting capability is unverified: {name}.")
            if name == "vialrgb_mode":
                if value[0] + (value[1] << 8) not in spec["effects"] or value[5] > spec["brightness_max"]:
                    raise SafetyError("Unsupported Vial RGB effect or brightness.")
            elif not all(spec["min"] <= v <= spec["max"] for v in value):
                raise SafetyError(f"Lighting value is outside the supported range: {name}.")
    if not isinstance(staged["dynamic"], dict) or set(staged["dynamic"]) != set(baseline["dynamic"]):
        raise SafetyError("Dynamic capabilities must match this keyboard.")
    for name, entries in staged["dynamic"].items():
        if name not in DYNAMIC_SPECS:
            raise SafetyError("Unknown dynamic capability.")
        old_entries = baseline["dynamic"][name]
        if not isinstance(entries, list) or len(entries) != len(old_entries):
            raise SafetyError(f"Entry count differs for {name}.")
        _, _, fmt, code_positions = DYNAMIC_SPECS[name]
        for old_entry, entry in zip(old_entries, entries):
            if not isinstance(entry, list) or len(entry) != len(old_entry) or not all(_uint(v) for v in entry):
                raise SafetyError(f"Invalid {name} entry.")
            try:
                struct.pack(fmt, *entry)
            except (struct.error, TypeError) as exc:
                raise SafetyError(f"Invalid {name} entry fields.") from exc
            for index in code_positions:
                validate_new_keycode(entry[index], old_entry[index], baseline)
    if not isinstance(staged["settings"], dict) or set(staged["settings"]) != set(baseline["settings"]):
        raise SafetyError("QMK settings capabilities must match this keyboard.")
    for qsid, value in staged["settings"].items():
        if value == baseline["settings"][qsid]:
            continue
        spec = setting_specs().get(int(qsid))
        if spec is None:
            raise SafetyError(f"QMK setting {qsid} has no verified schema and is read-only.")
        raw = _hex(value, spec["width"])
        number = int.from_bytes(raw, "little")
        if spec["type"] == "boolean":
            old = int.from_bytes(bytes.fromhex(baseline["settings"][qsid]), "little")
            if (number ^ old) & ~spec["max"]:
                raise SafetyError(f"Unknown bits cannot be changed in QMK setting {qsid}.")
        elif not spec["min"] <= number <= spec["max"]:
            raise SafetyError(f"QMK setting {qsid} is outside its supported range.")


def describe_changes(baseline: dict, staged: dict) -> list[str]:
    changes = []
    for l, layer in enumerate(staged["layers"]):
        for r, row in enumerate(layer):
            for c, value in enumerate(row):
                old = baseline["layers"][l][r][c]
                if old != value:
                    changes.append(f"Layer {l} · key {r},{c}: 0x{old:04X} → 0x{value:04X}")
    for l, layer in enumerate(staged["encoders"]):
        for index, pair in enumerate(layer):
            for direction, value in enumerate(pair):
                if value != baseline["encoders"][l][index][direction]:
                    changes.append(f"Layer {l} · encoder {index}, direction {direction}: 0x{value:04X}")
    for name in ("macro_buffer", "layout_options"):
        if baseline[name] != staged[name]:
            changes.append("Macros" if name == "macro_buffer" else "Physical layout options")
    for name in ("lighting", "dynamic", "settings"):
        for key, value in staged[name].items():
            if value != baseline[name][key]:
                changes.append(f"{name.replace('_', ' ').title()} · {key}")
    return changes


class SafetySession:
    def __init__(self, protocol, definition: dict, backup_dir: Path | str | None = None):
        self.protocol = protocol
        self.definition = deepcopy(definition)
        self.backups = BackupStore(backup_dir)
        self.failed = False
        self.normalizations: list[str] = []
        self.baseline = protocol.read_snapshot(self.definition)
        self.backup_path = self.backups.save(self.baseline, self.definition, "connected-before-edit")
        self.staged = deepcopy(self.baseline)

    @property
    def dirty(self) -> bool:
        return self.baseline != self.staged

    def changes(self) -> list[str]:
        validate_staged(self.baseline, self.staged, self.protocol.lighting_specs)
        return describe_changes(self.baseline, self.staged)

    def discard(self):
        self.staged = deepcopy(self.baseline)

    def apply(self, staged: dict | None = None) -> Path:
        if self.failed:
            raise SafetyError("A previous apply was interrupted. Reconnect and inspect the keyboard before restoring a backup.")
        self.normalizations = []
        proposed = deepcopy(staged if staged is not None else self.staged)
        validate_staged(self.baseline, proposed, self.protocol.lighting_specs)
        if proposed == self.baseline:
            return self.backup_path
        with self.protocol.lock:
            current = self.protocol.read_snapshot(self.definition)
            if current != self.baseline:
                raise StaleSnapshot("The keyboard changed since it was loaded. Reconnect and review the current keymap; nothing was written.")
            if not self.protocol.read_unlock_status()["unlocked"]:
                raise SafetyError("This Vial keyboard is locked. Physically unlock it before applying changes.")
            # A failed backup aborts BEFORE the first mutating USB packet.
            self.backup_path = self.backups.save(current, self.definition, "before-apply")
            try:
                self.normalizations = self.protocol._write_changes(current, proposed)
                verified = self.protocol.read_snapshot(self.definition)
                if verified != proposed:
                    raise ProtocolError("Final device readback did not match the staged configuration.")
            except Exception as exc:
                self.failed = True
                raise SafetyError(
                    f"Apply stopped: {exc} Some configuration may have changed. No automatic retry or rollback was attempted. "
                    f"Reconnect, inspect, then explicitly restore if needed. Backup: {self.backup_path}"
                ) from exc
            self.baseline = verified
            self.staged = deepcopy(verified)
            return self.backup_path

    def stage_backup(self, path: Path | str):
        payload = self.backups.load(path)
        restored = deepcopy(payload["snapshot"])
        # A device may reconnect on another hidraw node. Preserve this session's
        # path, but all physical identity/protocol/definition fields must match.
        restored["identity"]["path"] = self.baseline["identity"]["path"]
        validate_staged(self.baseline, restored, self.protocol.lighting_specs)
        self.staged = restored

    def close(self):
        self.protocol.close()
