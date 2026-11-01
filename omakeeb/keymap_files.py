"""Validated, atomic keymap files. Import stages data; it never touches a device."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

MAX_FILE_BYTES = 8 * 1024 * 1024
EDITABLE_FIELDS = ("layers", "macro_buffer", "encoders", "lighting", "layout_options", "dynamic", "settings")


class KeymapFileError(ValueError):
    pass


def _integer(value, low=0, high=65535, label="value"):
    if type(value) is not int or not low <= value <= high:
        raise KeymapFileError(f"{label} must be an integer from {low} to {high}.")
    return value


def _hex(value, label="hex data"):
    if not isinstance(value, str) or len(value) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", value):
        raise KeymapFileError(f"Invalid {label}.")
    return bytes.fromhex(value)


def _list(value, label, max_length=65536):
    if not isinstance(value, list) or len(value) > max_length:
        raise KeymapFileError(f"Invalid {label}.")
    return value


def _same_shape(value, original, label, high=65535):
    if isinstance(original, list):
        if not isinstance(value, list) or len(value) != len(original):
            raise KeymapFileError(f"{label} has a different size from this keyboard.")
        for child, expected in zip(value, original):
            _same_shape(child, expected, label, high)
    else:
        _integer(value, high=high, label=label)


def validate_snapshot(snapshot: dict) -> None:
    """Bound the complete native schema before allocating any device work."""
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise KeymapFileError("Unsupported Omakeeb snapshot version.")
    identity = snapshot.get("identity")
    if not isinstance(identity, dict):
        raise KeymapFileError("Missing keyboard identity.")
    for key in ("vendor_id", "product_id"):
        _integer(identity.get(key), label=key)
    matrix = snapshot.get("matrix")
    if not isinstance(matrix, dict):
        raise KeymapFileError("Missing matrix dimensions.")
    rows = _integer(matrix.get("rows"), 1, 255, "rows")
    cols = _integer(matrix.get("cols"), 1, 255, "columns")
    layers = _list(snapshot.get("layers"), "layers", 32)
    if not layers or rows * cols * len(layers) > 32768:
        raise KeymapFileError("Invalid keymap dimensions.")
    for layer in layers:
        if len(_list(layer, "matrix rows", 255)) != rows:
            raise KeymapFileError("Incorrect matrix row count.")
        for row in layer:
            if len(_list(row, "matrix columns", 255)) != cols:
                raise KeymapFileError("Incorrect matrix column count.")
            for value in row:
                _integer(value, label="keycode")
    _integer(snapshot.get("via_protocol"), 1, 65535, "VIA protocol")
    if snapshot.get("vial_protocol") is not None:
        _integer(snapshot["vial_protocol"], 0, 65535, "Vial protocol")
    macro_count = _integer(snapshot.get("macro_count", 0), 0, 255, "macro count")
    macro_bytes = _hex(snapshot.get("macro_buffer", ""), "macro buffer")
    if len(macro_bytes) > 65535 or (macro_count and len(macro_bytes) < macro_count):
        raise KeymapFileError("Invalid macro buffer size.")
    encoders = _list(snapshot.get("encoders", []), "encoders", 32)
    if encoders and len(encoders) != len(layers):
        raise KeymapFileError("Encoder layer count does not match keymap.")
    count = None
    for layer in encoders:
        _list(layer, "encoder layer", 128)
        if count is not None and len(layer) != count:
            raise KeymapFileError("Encoder count differs between layers.")
        count = len(layer)
        for pair in layer:
            if len(_list(pair, "encoder directions", 2)) != 2:
                raise KeymapFileError("Each encoder needs two directions.")
            for code in pair:
                _integer(code, label="encoder keycode")
    lighting = snapshot.get("lighting", {})
    if not isinstance(lighting, dict) or len(lighting) > 128:
        raise KeymapFileError("Invalid lighting values.")
    for key, values in lighting.items():
        if not isinstance(key, str):
            raise KeymapFileError("Invalid lighting name.")
        for value in _list(values, "lighting bytes", 32):
            _integer(value, 0, 255, "lighting byte")
    if snapshot.get("layout_options") is not None:
        _integer(snapshot["layout_options"], 0, 0xffffffff, "layout options")
    dynamic = snapshot.get("dynamic", {})
    if not isinstance(dynamic, dict) or len(dynamic) > 16:
        raise KeymapFileError("Invalid dynamic entries.")
    widths = {"tap_dance": (65535,)*5, "combo": (65535,)*5,
              "key_override": (65535,)*3 + (255,)*4, "alt_repeat_key": (65535,)*2 + (255,)*2}
    for name, entries in dynamic.items():
        if name not in widths:
            raise KeymapFileError(f"Unknown dynamic feature: {name}.")
        for entry in _list(entries, name, 255):
            if len(_list(entry, name, 7)) != len(widths[name]):
                raise KeymapFileError(f"Incorrect {name} entry length.")
            for value, maximum in zip(entry, widths[name]):
                _integer(value, 0, maximum, name)
    settings = snapshot.get("settings", {})
    if not isinstance(settings, dict) or len(settings) > 4096:
        raise KeymapFileError("Invalid QMK settings.")
    for key, value in settings.items():
        if not isinstance(key, str) or not key.isdecimal() or not 0 <= int(key) <= 65535:
            raise KeymapFileError("Invalid QMK setting identifier.")
        if not 1 <= len(_hex(value, "QMK setting")) <= 31:
            raise KeymapFileError("Invalid QMK setting size.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise KeymapFileError(f"Duplicate JSON field: {key}.")
        result[key] = value
    return result


def _read(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise KeymapFileError("Keymap file is too large (8 MiB maximum).")
        data = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(data, dict):
            raise KeymapFileError("A keymap must be a JSON object.")
        return data
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise KeymapFileError(f"Could not read keymap: {exc}") from exc


def _canonical(snapshot):
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _validate_definition(definition, snapshot):
    from .protocol import definition_hash
    from .definitions import load_definition
    if not isinstance(definition, dict) or definition_hash(definition) != snapshot.get("definition_sha256"):
        raise KeymapFileError("Embedded definition does not match the snapshot checksum.")
    try:
        parsed = load_definition(definition, vial=snapshot.get("vial_protocol") is not None)
    except ValueError as exc:
        raise KeymapFileError(f"Invalid embedded keyboard definition: {exc}") from exc
    if {"rows":parsed.rows,"cols":parsed.cols} != snapshot["matrix"]:
        raise KeymapFileError("Embedded definition dimensions do not match the snapshot.")
    for key, value in (("vendorId",parsed.vendor_id),("productId",parsed.product_id)):
        expected = snapshot["identity"]["vendor_id" if key == "vendorId" else "product_id"]
        if key in definition and value != expected:
            raise KeymapFileError("Embedded definition USB identity does not match the snapshot.")


def _profile_data(data) -> dict:
    if data.get("format") == "omakeeb":
        if data.get("version") != 1 or not isinstance(data.get("snapshot"), dict):
            raise KeymapFileError("Unsupported Omakeeb profile version.")
        snapshot = data["snapshot"]
        digest = hashlib.sha256(_canonical(snapshot)).hexdigest()
        if data.get("sha256") != digest:
            raise KeymapFileError("Keymap checksum does not match; the file may be incomplete or edited.")
    elif data.get("format") == "omakeeb-backup":
        if data.get("version") != 1 or not isinstance(data.get("snapshot"), dict):
            raise KeymapFileError("Unsupported backup version.")
        content = {key: value for key, value in data.items() if key != "sha256"}
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
        if hashlib.sha256(encoded).hexdigest() != data.get("sha256"):
            raise KeymapFileError("Backup checksum does not match.")
        from .protocol import definition_hash
        snapshot = data["snapshot"]
        if not isinstance(data.get("definition"), dict) or definition_hash(data["definition"]) != snapshot.get("definition_sha256"):
            raise KeymapFileError("Backup definition does not match its snapshot.")
    elif data.get("schema_version") == 1:
        snapshot = data
    else:
        raise KeymapFileError("This file is not an Omakeeb profile.")
    validate_snapshot(snapshot)
    if "definition" in data:
        _validate_definition(data["definition"], snapshot)
    return snapshot


def load_profile(path) -> dict:
    """Read a native profile, verified automatic backup, or raw snapshot."""
    return _profile_data(_read(path))


def load_profile_bundle(path) -> tuple[dict, dict | None]:
    """Load a verified native profile with optional self-contained geometry."""
    data = _read(path)
    return _profile_data(data), data.get("definition")


def _keycode(value, snapshot):
    from . import keycodes
    if type(value) is int:
        return _integer(value, label="keycode")
    if not isinstance(value, str) or len(value) > 200:
        raise KeymapFileError("Invalid keycode.")
    try:
        if snapshot.get("vial_protocol") is not None and re.fullmatch(r"M[0-9]+", value.strip(), re.I):
            return keycodes.macro_code(int(value.strip()[1:]), snapshot)
        return keycodes.parse(value, protocol=keycodes.keycode_protocol(snapshot))
    except ValueError as exc:
        raise KeymapFileError(f"Unsupported keycode {value!r}: {exc}") from exc


def _name(code, snapshot, format="via"):
    from . import keycodes
    if format == "vial":
        # Vial accepts hexadecimal through AnyKeycode, while its aliases and
        # generated macro names differ from VIA. Exact hex avoids reinterpretation.
        return f"0x{code:04x}"
    return keycodes.via_name(code, protocol=snapshot["via_protocol"])


def _native_import(source, baseline):
    validate_snapshot(source)
    for field in ("vendor_id", "product_id"):
        if source["identity"][field] != baseline["identity"][field]:
            raise KeymapFileError("This profile belongs to a different keyboard model.")
    for field in ("definition_sha256", "via_protocol", "vial_protocol", "keyboard_id", "keycodes_version", "matrix", "macro_count"):
        if source.get(field) != baseline.get(field):
            raise KeymapFileError(f"Profile {field} does not match this keyboard.")
    staged = copy.deepcopy(baseline)
    for field in EDITABLE_FIELDS:
        if field in source:
            staged[field] = copy.deepcopy(source[field])
    return staged


def _via_import(source, baseline):
    identity = baseline["identity"]
    expected = (identity["vendor_id"] << 16) | identity["product_id"]
    supplied = source.get("vendorProductId")
    if type(supplied) is not int or not -(1 << 31) <= supplied <= 0xffffffff or supplied & 0xffffffff != expected:
        raise KeymapFileError("This VIA file belongs to a different keyboard model.")
    staged = copy.deepcopy(baseline)
    # VIA JSON has no Vial dictionary marker. Match VIA's own protocol-specific
    # decoder even on a Vial board, preserving the exact wire keycode values.
    via_dictionary = {**baseline, "vial_protocol": None}
    rows, cols = baseline["matrix"]["rows"], baseline["matrix"]["cols"]
    layers = _list(source.get("layers"), "VIA layers", 32)
    if len(layers) != len(baseline["layers"]):
        raise KeymapFileError("VIA layer count does not match this keyboard.")
    staged["layers"] = []
    for layer in layers:
        if len(_list(layer, "VIA layer")) != rows * cols:
            raise KeymapFileError("VIA matrix size does not match this keyboard.")
        staged["layers"].append([[_keycode(code, via_dictionary) for code in layer[r*cols:(r+1)*cols]] for r in range(rows)])
    if "encoders" in source:
        encoders = _list(source["encoders"], "VIA encoders", 128)
        expected_encoders = len(baseline.get("encoders", [[]])[0]) if baseline.get("encoders") else 0
        if len(encoders) != expected_encoders:
            raise KeymapFileError("VIA encoder count does not match this keyboard.")
        if encoders:
            native = [[[] for _ in encoders] for _ in layers]
            for encoder, values in enumerate(encoders):
                if len(_list(values, "encoder layers", 32)) != len(layers):
                    raise KeymapFileError("VIA encoder layer count does not match.")
                for layer, pair in enumerate(values):
                    if len(_list(pair, "encoder directions", 2)) != 2:
                        raise KeymapFileError("VIA encoder needs two directions.")
                    converted = [_keycode(code, via_dictionary) for code in pair]
                    # Both protocols use direction 0=CCW, 1=CW. Vial's
                    # save_layout local variable names misleadingly say cw first.
                    native[layer][encoder] = converted
            staged["encoders"] = native
    if "macros" in source:
        expressions = _list(source["macros"], "VIA macros", 255)
        if len(expressions) != baseline["macro_count"] or any(not isinstance(x, str) for x in expressions):
            raise KeymapFileError("VIA macro count does not match this keyboard.")
        from .macros import encode
        staged["macro_buffer"] = encode([_from_via_expression(value) for value in expressions], protocol=baseline["via_protocol"],
            vial_protocol=baseline.get("vial_protocol"), size=len(bytes.fromhex(baseline["macro_buffer"]))).hex()
    return staged


def _vial_import(source, baseline):
    if source.get("version") != 1 or baseline.get("vial_protocol") is None:
        raise KeymapFileError("Vial files require a matching Vial keyboard and file version 1.")
    expected_uid = int(baseline["keyboard_id"], 16) if baseline.get("keyboard_id") else None
    if type(source.get("uid")) is not int or source["uid"] != expected_uid:
        raise KeymapFileError("This Vial file belongs to a different keyboard definition.")
    for field in ("via_protocol", "vial_protocol"):
        if field in source and source[field] != baseline[field]:
            raise KeymapFileError(f"Vial file {field} does not match this firmware.")
    staged = copy.deepcopy(baseline)
    layers = _list(source.get("layout"), "Vial layers", 32)
    staged["layers"] = [[[_keycode(code, baseline) if code != -1 else baseline["layers"][l][r][c]
                          for c, code in enumerate(_list(row, "Vial row", 255))]
                         for r, row in enumerate(_list(layer, "Vial layer", 255))] for l, layer in enumerate(layers)]
    if "encoder_layout" in source:
        staged["encoders"] = [[[_keycode(code, baseline) for code in _list(pair, "encoder directions", 2)]
                               for pair in _list(layer, "encoders", 128)]
                              for layer in _list(source["encoder_layout"], "encoder layers", 32)]
    if "layout_options" in source and source["layout_options"] != -1:
        staged["layout_options"] = source["layout_options"]
    if "macro" in source:
        staged["macro_buffer"] = _encode_vial_actions(source["macro"], baseline).hex()
    if "dynamic" not in staged:
        staged["dynamic"] = {}
    fields = {"key_override": ("trigger", "replacement", "layers", "trigger_mods", "negative_mod_mask", "suppressed_mods", "options"),
              "alt_repeat_key": ("keycode", "alt_keycode", "allowed_mods", "options")}
    for name in ("tap_dance", "combo", "key_override", "alt_repeat_key"):
        if name not in source:
            continue
        entries = []
        for entry in _list(source[name], name, 255):
            if name in fields:
                if not isinstance(entry, dict) or set(entry) != set(fields[name]):
                    raise KeymapFileError(f"Invalid Vial {name} entry.")
                values = [entry[key] for key in fields[name]]
                key_count = 2
            else:
                values = list(_list(entry, name, 5))
                key_count = 4 if name == "tap_dance" else 5
            entries.append([_keycode(value, baseline) if i < key_count else value for i, value in enumerate(values)])
        # Older files include empty arrays for unsupported features.
        if entries or name in staged["dynamic"]:
            staged["dynamic"][name] = entries
    if source.get("settings"):
        from .protocol import setting_width
        if not isinstance(source["settings"], dict):
            raise KeymapFileError("Invalid Vial settings.")
        for key, value in source["settings"].items():
            if not key.isdecimal() or key not in baseline.get("settings", {}):
                raise KeymapFileError(f"Unsupported Vial setting {key}.")
            width = setting_width(int(key))
            if not width:
                raise KeymapFileError(f"Unknown Vial setting {key}; use the native format to preserve it.")
            _integer(value, 0, (1 << (width*8))-1, "Vial setting")
            staged["settings"][key] = value.to_bytes(width, "little").hex()
    return staged


def _validate_staged(staged, baseline):
    validate_snapshot(staged)
    for field in ("layers", "encoders"):
        _same_shape(staged.get(field, []), baseline.get(field, []), field)
    if len(_hex(staged.get("macro_buffer", ""))) != len(_hex(baseline.get("macro_buffer", ""))):
        raise KeymapFileError("Macro buffer capacity does not match this keyboard.")
    for field in ("lighting", "dynamic", "settings"):
        if set(staged.get(field, {})) != set(baseline.get(field, {})):
            raise KeymapFileError(f"The {field} capabilities do not match this keyboard.")
        for key, value in staged.get(field, {}).items():
            original = baseline[field][key]
            if field == "settings":
                if len(_hex(value)) != len(_hex(original)):
                    raise KeymapFileError("QMK setting size does not match.")
            else:
                _same_shape(value, original, field, 255 if field == "lighting" else 65535)
    if baseline.get("layout_options") is None and staged.get("layout_options") is not None:
        raise KeymapFileError("This keyboard does not support layout options.")


def import_keymap(path, baseline: dict) -> dict:
    """Return a staged copy, retaining all live identity/capability metadata."""
    validate_snapshot(baseline)
    data = _read(path)
    try:
        if data.get("format") in ("omakeeb", "omakeeb-backup") or data.get("schema_version") == 1:
            staged = _native_import(_profile_data(data), baseline)
        elif "vendorProductId" in data:
            staged = _via_import(data, baseline)
        elif "layout" in data and "uid" in data:
            staged = _vial_import(data, baseline)
        else:
            raise KeymapFileError("Unrecognized keymap format. Use Omakeeb, VIA JSON, or Vial .vil.")
        _validate_staged(staged, baseline)
        return staged
    except (IndexError, KeyError, TypeError, OverflowError) as exc:
        raise KeymapFileError("Keymap fields do not match this keyboard's capabilities.") from exc
    except ValueError as exc:
        if isinstance(exc, KeymapFileError):
            raise
        raise KeymapFileError(str(exc)) from exc


def _encode_vial_actions(macros, snapshot):
    from .macros import encode
    macros = _list(macros, "Vial macros", 255)
    if len(macros) != snapshot["macro_count"]:
        raise KeymapFileError("Vial macro count does not match.")
    expressions = []
    for actions in macros:
        expression = ""
        for action in _list(actions, "macro actions", 65535):
            if not isinstance(action, list) or not action:
                raise KeymapFileError("Invalid Vial macro action.")
            tag = action[0]
            if tag == "text":
                if len(action) != 2 or not isinstance(action[1], str):
                    raise KeymapFileError("Invalid Vial macro text.")
                expression += "".join("\\" + c if c in "{}\\" else c for c in action[1])
            elif tag == "delay":
                if len(action) != 2:
                    raise KeymapFileError("Invalid Vial delay action.")
                expression += "{" + str(_integer(action[1], 0, 65024, "macro delay")) + "}"
            elif tag in ("tap", "down", "up"):
                if len(action) < 2:
                    raise KeymapFileError("Empty Vial key action.")
                for value in action[1:]:
                    code = _keycode(value, snapshot)
                    expression += "{" + {"tap": "", "down": "+", "up": "-"}[tag] + hex(code) + "}"
            else:
                raise KeymapFileError(f"Unknown Vial macro action: {tag}.")
        expressions.append(expression)
    return encode(expressions, protocol=snapshot["via_protocol"],
                  vial_protocol=snapshot["vial_protocol"], size=len(_hex(snapshot["macro_buffer"])))


def _macro_expressions(snapshot):
    from .macros import decode
    return decode(_hex(snapshot["macro_buffer"]), snapshot["macro_count"],
                  protocol=snapshot["via_protocol"], vial_protocol=snapshot.get("vial_protocol"))



def _from_via_expression(expression):
    """Translate VIA's only escape (backslash-open-brace) to our codec grammar."""
    output = []
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "\\" and index + 1 < len(expression) and expression[index + 1] == "{":
            output.append("\\{")
            index += 2
            continue
        if char == "{" and (index == 0 or expression[index - 1] != "\\"):
            end = expression.find("}", index + 1)
            if end < 0:
                raise KeymapFileError("Unclosed VIA macro action")
            output.append(expression[index:end + 1])
            index = end + 1
            continue
        output.append("\\" + char if char in "{}\\" else char)
        index += 1
    return "".join(output)


def _via_macro_expressions(snapshot):
    from .macros import encode, expression_actions
    from .keycodes import via_name
    expressions = _macro_expressions(snapshot)
    # Confirm that delays and key actions fit the target VIA dialect. Vial
    # extended key actions and >9999ms delays cannot be silently converted.
    try:
        encode(expressions, protocol=snapshot["via_protocol"])
    except ValueError as exc:
        raise KeymapFileError(f"VIA cannot preserve these macros: {exc}. Export Omakeeb or Vial format.") from exc
    result = []
    for index, expression in enumerate(expressions):
        parts = []
        for action, value in expression_actions(expression):
            if action != "text" and parts and parts[-1] == "\\":
                # VIA reads any brace after a backslash as literal text and
                # has no backslash escape, so this sequence has no VIA form.
                raise KeymapFileError(f"VIA cannot preserve macro {index}: a backslash directly before a key "
                                      "action or delay would become literal text. Export Omakeeb or Vial format.")
            if action == "text":
                parts.append("\\{" if value == "{" else value)
            elif action == "delay":
                parts.append("{" + str(value) + "}")
            else:
                code = _keycode(value, snapshot)
                direction = {"tap": "", "down": "+", "up": "-"}[action]
                parts.append("{" + direction + via_name(code, protocol=snapshot["via_protocol"]) + "}")
        result.append("".join(parts))
    return result

def _vial_actions(snapshot):
    from .macros import expression_actions
    result = []
    for expression in _macro_expressions(snapshot):
        actions = []
        for tag, value in expression_actions(expression):
            if tag == "text" and actions and actions[-1][0] == "text":
                actions[-1][1] += value
            elif tag in ("text", "delay"):
                actions.append([tag, value])
            else:
                actions.append([tag, _keycode(value, snapshot)])
        result.append(actions)
    return result


def export_keymap(path, snapshot: dict, format="omakeeb", name="Keyboard", definition=None) -> Path:
    """Durable replacement with private permissions. Native export is lossless.

    VIA files contain keymaps, macros and encoders only. Vial files additionally
    contain Vial dynamic/settings data; neither format carries lighting backups.
    """
    validate_snapshot(snapshot)
    if format == "omakeeb":
        data = {"format": "omakeeb", "version": 1, "name": str(name),
                "snapshot": snapshot, "sha256": hashlib.sha256(_canonical(snapshot)).hexdigest()}
        if definition is not None:
            _validate_definition(definition, snapshot)
            data["definition"] = definition
    elif format == "via":
        ident = snapshot["identity"]
        data = {"name": str(name), "vendorProductId": (ident["vendor_id"] << 16) | ident["product_id"],
                "layers": [[_name(code, snapshot) for row in layer for code in row] for layer in snapshot["layers"]],
                "macros": _via_macro_expressions(snapshot), "encoders": []}
        encoders = snapshot.get("encoders", [])
        if encoders:
            for index in range(len(encoders[0])):
                values = []
                for layer in encoders:
                    pair = layer[index]
                    values.append([_name(code, snapshot) for code in pair])
                data["encoders"].append(values)
    elif format == "vial":
        if snapshot.get("vial_protocol") is None or not snapshot.get("keyboard_id"):
            raise KeymapFileError("Vial export requires a Vial keyboard.")
        data = {"version": 1, "uid": int(snapshot["keyboard_id"], 16),
                "via_protocol": snapshot["via_protocol"], "vial_protocol": snapshot["vial_protocol"],
                "layout": [[[_name(code, snapshot, "vial") for code in row] for row in layer] for layer in snapshot["layers"]],
                "encoder_layout": [[[_name(code, snapshot, "vial") for code in pair] for pair in layer] for layer in snapshot.get("encoders", [])],
                "layout_options": snapshot.get("layout_options") if snapshot.get("layout_options") is not None else -1,
                "macro": _vial_actions(snapshot),
                "settings": {}}
        fields = {"key_override": ("trigger", "replacement", "layers", "trigger_mods", "negative_mod_mask", "suppressed_mods", "options"),
                  "alt_repeat_key": ("keycode", "alt_keycode", "allowed_mods", "options")}
        for feature in ("tap_dance", "combo", "key_override", "alt_repeat_key"):
            data[feature] = []
            for values in snapshot.get("dynamic", {}).get(feature, []):
                if feature in fields:
                    converted = [_name(value, snapshot, "vial") if i < 2 else value for i, value in enumerate(values)]
                    data[feature].append(dict(zip(fields[feature], converted)))
                else:
                    nkeys = 4 if feature == "tap_dance" else 5
                    data[feature].append([_name(value, snapshot, "vial") if i < nkeys else value for i, value in enumerate(values)])
        if snapshot.get("settings"):
            from .protocol import setting_width
            for key, value in snapshot["settings"].items():
                width = setting_width(int(key))
                if width is None or len(_hex(value)) != width:
                    raise KeymapFileError("Unknown QMK settings cannot be exported as Vial; use the native format.")
                data["settings"][key] = int.from_bytes(_hex(value), "little")
    else:
        raise KeymapFileError(f"Unknown export format: {format}.")
    path = Path(path)
    temporary = None
    try:
        payload = (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(payload) > MAX_FILE_BYTES:
            raise KeymapFileError("Export exceeds the 8 MiB profile limit.")
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return path
    except OSError as exc:
        raise KeymapFileError(f"Could not save keymap: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
