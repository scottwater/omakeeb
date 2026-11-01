"""Versioned QMK keycodes and a small, non-evaluating expression parser.

Unknown numeric keycodes round-trip exactly. Never interpret a modern symbolic
name against legacy firmware: callers should pass its negotiated VIA protocol.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import json
import re

_TABLES = json.loads((Path(__file__).resolve().parent.parent / "resources/keycodes.json").read_text())["protocols"]
_VIAL_TABLES = json.loads((Path(__file__).resolve().parent.parent / "resources/vial-keycodes.json").read_text())["protocols"]
_VIAL_LOOKUPS = {version: {key.upper(): value for key, value in table.items()} for version, table in _VIAL_TABLES.items()}
# Vial and VIA share most QMK ranges, but their named MIDI tables differ.
# Retain a distinct dictionary identity rather than approximate Vial6 as VIA12.
_TABLES["vial5"] = {**_TABLES["10"], **_VIAL_TABLES["5"]}
_TABLES["vial6"] = {**_TABLES["12"], **_VIAL_TABLES["6"]}
_CASEFOLD_TABLES = {version: {key.upper(): value for key, value in table.items()} for version, table in _TABLES.items()}
_MODS = {"LCTL": 1, "LSFT": 2, "LALT": 4, "LGUI": 8, "RCTL": 17, "RSFT": 18, "RALT": 20, "RGUI": 24, "HYPR": 15, "MEH": 7, "LCAG": 13, "LCA": 5, "LCG": 9, "LSA": 6, "LAG": 12, "SGUI": 10, "C_S": 3}
_WRAPPERS = {**_MODS, "C": 1, "S": 2, "A": 4, "G": 8, "RCS": 19, "RCAG": 29, "RCG": 25}
_WRAPPERS.update({"RSA": 22, "LSG": 10, "LCMD": 8, "LWIN": 8, "LOPT": 4, "RCMD": 24, "RWIN": 24, "ROPT": 20, "ALGR": 20, "SCMD": 10, "SWIN": 10, "SAGR": 22, "ALL": 15, "CTL": 1, "SFT": 2, "ALT": 4, "OPT": 4, "GUI": 8, "CMD": 8, "WIN": 8})
_ALIASES = {"KC_ENTER": "KC_ENT", "KC_ESCAPE": "KC_ESC", "KC_BACKSPACE": "KC_BSPC", "KC_SPACE": "KC_SPC", "KC_DELETE": "KC_DEL", "KC_LCTRL": "KC_LCTL", "KC_RCTRL": "KC_RCTL", "KC_LSHIFT": "KC_LSFT", "KC_RSHIFT": "KC_RSFT", "KC_LSUPER": "KC_LGUI", "KC_RSUPER": "KC_RGUI", "KC_TRANSPARENT": "KC_TRNS", "_______": "KC_TRNS", "XXXXXXX": "KC_NO", "KC_PGDOWN": "KC_PGDN", "KC_PSCREEN": "KC_PSCR", "KC_CAPSLOCK": "KC_CAPS"}
_ALIASES.update({"KC_RIGHT": "KC_RGHT", "KC_INSERT": "KC_INS", "KC_PAUSE": "KC_PAUS", "KC_SCROLLLOCK": "KC_SLCK", "KC_NUMLOCK": "KC_NLCK", "KC_PGDOWN": "KC_PGDN", "KC_PRINT_SCREEN": "KC_PSCR", "KC_CAPS_LOCK": "KC_CAPS", "KC_APPLICATION": "KC_APP", "QK_BOOT": "RESET", "EE_CLR": "QK_CLEAR_EEPROM"})
_LABELS = {"KC_NO": "None", "KC_TRNS": "▽", "KC_ENT": "Enter", "KC_ESC": "Esc", "KC_BSPC": "Backspace", "KC_SPC": "Space", "KC_LCTL": "Ctrl", "KC_RCTL": "Ctrl", "KC_LGUI": "Super", "KC_RGUI": "Super", "KC_LSFT": "Shift", "KC_RSFT": "Shift", "KC_LALT": "Alt", "KC_RALT": "AltGr", "KC_TAB": "Tab", "KC_CAPS": "Caps", "KC_LEFT": "←", "KC_RIGHT": "→", "KC_RGHT": "→", "KC_UP": "↑", "KC_DOWN": "↓", "KC_MINS": "−", "KC_EQL": "=", "KC_LBRC": "[", "KC_RBRC": "]", "KC_BSLS": "\\", "KC_SCLN": ";", "KC_QUOT": "'", "KC_GRV": "`", "KC_COMM": ",", "KC_DOT": ".", "KC_SLSH": "/", "KC_PGUP": "PgUp", "KC_PGDN": "PgDn", "KC_DEL": "Del", "KC_INS": "Ins"}
_LAYER = {"TO": "_QK_TO", "MO": "_QK_MOMENTARY", "DF": "_QK_DEF_LAYER", "TG": "_QK_TOGGLE_LAYER", "OSL": "_QK_ONE_SHOT_LAYER", "TT": "_QK_LAYER_TAP_TOGGLE", "M": "_QK_MACRO", "MACRO": "_QK_MACRO", "CUSTOM": "_QK_KB"}


class KeycodeError(ValueError):
    pass


@dataclass(frozen=True)
class Keycode:
    code: int
    name: str
    label: str
    category: str

    @property
    def value(self):
        return self.code


def _table(modern=True, protocol=None):
    version = protocol if protocol is not None else (12 if modern else 10)
    if str(version) not in _TABLES:
        raise KeycodeError(f"Unsupported VIA keycode protocol {version}; use a numeric keycode to preserve unknown values")
    return _TABLES[str(version)]


def _u16(value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise KeycodeError("Keycode must be an integer from 0x0000 to 0xFFFF")
    return value


def _args(value):
    result, depth, start = [], 0, 0
    for pos, char in enumerate(value):
        if char == "(": depth += 1
        elif char == ")": depth -= 1
        elif char == "," and depth == 0:
            result.append(value[start:pos])
            start = pos + 1
        if depth < 0:
            raise KeycodeError("Unbalanced keycode expression")
    if depth:
        raise KeycodeError("Unbalanced keycode expression")
    return result + [value[start:]]


def _mods(text):
    result = 0
    sides = set()
    for token in text.split("|"):
        token = token.removeprefix("MOD_")
        if token not in _MODS:
            raise KeycodeError(f"Unknown modifier {token}")
        value = _MODS[token]
        sides.add(bool(value & 16))
        result |= value
    if len(sides) > 1:
        raise KeycodeError("QMK cannot encode left and right modifiers in one modifier keycode")
    return result


def parse(text, modern=True, *, protocol=None):
    if type(text) is int:
        return _u16(text)
    if not isinstance(text, str) or len(text) > 200:
        raise KeycodeError("Enter a QMK name or 16-bit hexadecimal keycode")
    text = re.sub(r"\s+", "", text.upper())
    if re.fullmatch(r"0X[0-9A-F]{1,4}|[0-9]{1,5}", text):
        return _u16(int(text, 16 if text.startswith("0X") else 10))
    table = _table(modern, protocol)
    lookup = _CASEFOLD_TABLES[str(protocol if protocol is not None else (12 if modern else 10))]
    text = _ALIASES.get(text, text)
    if text in lookup and not text.startswith("_"):
        return lookup[text]
    if "KC_" + text in lookup:
        return lookup["KC_" + text]
    version = protocol if protocol is not None else (12 if modern else 10)
    vial_aliases = _VIAL_LOOKUPS["5" if version in (9, 10, "vial5") else "6"]
    # VIA13 deliberately removed the old RGB keycode names. Do not revive them
    # with an alias table from Vial's older keycode generation.
    if text in vial_aliases and not (version == 13 and text.startswith("RGB_")):
        return vial_aliases[text]
    if re.fullmatch(r"M\d+", text):
        text = f"MACRO({int(text[1:])})"
    old_layer_tap = re.fullmatch(r"LT(\d+)\((.*)\)", text)
    if old_layer_tap:
        text = f"LT({old_layer_tap[1]},{old_layer_tap[2]})"
    match = re.fullmatch(r"([A-Z_]+)\((.*)\)", text)
    if not match:
        raise KeycodeError(f"Unknown keycode {text!r}")
    fn, inner = match.groups()
    args = _args(inner)
    if fn in _LAYER and len(args) == 1:
        if not args[0].isdigit():
            raise KeycodeError("Layer or macro index must be an integer")
        index = int(args[0])
        base = table[_LAYER[fn]]
        maximum = table[_LAYER[fn] + "_MAX"] - base
        if not 0 <= index <= maximum:
            raise KeycodeError(f"{fn} index must be between 0 and {maximum}")
        return base + index
    if fn == "PDF" and version == "vial6" and len(args) == 1 and args[0].isdigit() and 0 <= int(args[0]) < 32:
        return 0x52E0 + int(args[0])
    if fn == "OSM" and len(args) == 1:
        return table["_QK_ONE_SHOT_MOD"] | _mods(args[0])
    if fn == "LT" and len(args) == 2 and args[0].isdigit():
        layer = int(args[0])
        key = parse(args[1], modern, protocol=protocol)
        if not 0 <= layer < 16 or key > 255:
            raise KeycodeError("LT needs a layer 0–15 and a basic keycode")
        return table["_QK_LAYER_TAP"] | layer << 8 | key
    if fn == "MT" and len(args) == 2:
        key = parse(args[1], modern, protocol=protocol)
        if key > 255:
            raise KeycodeError("MT needs a basic keycode")
        return table["_QK_MOD_TAP"] | _mods(args[0]) << 8 | key
    if fn == "LM" and len(args) == 2 and args[0].isdigit():
        layer, mods = int(args[0]), _mods(args[1])
        mask = table["_QK_LAYER_MOD_MASK"]
        if not 0 <= layer < 16 or mods > mask:
            raise KeycodeError("LM layer or modifiers exceed this protocol's limits")
        return table["_QK_LAYER_MOD"] | layer * (mask + 1) | mods
    is_tap = fn.endswith("_T")
    wrapper = fn[:-2] if is_tap else fn
    if wrapper in _WRAPPERS and len(args) == 1:
        key = parse(args[0], modern, protocol=protocol)
        mods = _WRAPPERS[wrapper]
        if is_tap:
            if key > 255:
                raise KeycodeError("Modifier tap needs a basic keycode")
            return table["_QK_MOD_TAP"] | mods << 8 | key
        if key > 0x1fff:
            raise KeycodeError("Modifier wrappers need a basic or modifier keycode")
        existing = key >> 8
        if existing and bool(existing & 16) != bool(mods & 16):
            raise KeycodeError("Cannot mix left and right modifier wrappers")
        return key | mods << 8
    if fn == "TD" and len(args) == 1 and args[0].isdigit() and 0 <= int(args[0]) < 256:
        return 0x5700 | int(args[0])
    raise KeycodeError(f"Unsupported keycode expression {text!r}")


@lru_cache(maxsize=8)
def _reverse(version):
    result = {}
    for key, value in _TABLES[str(version)].items():
        if not key.startswith("_"):
            result.setdefault(value, key)
    return result


def name(code, modern=True, *, protocol=None):
    code = _u16(code)
    version = protocol if protocol is not None else (12 if modern else 10)
    table = _table(modern, protocol)
    direct = _reverse(version).get(code)
    if direct:
        return direct
    for fn, constant in _LAYER.items():
        if fn == "M": continue
        if table[constant] <= code <= table[constant + "_MAX"]:
            return f"{fn}({code - table[constant]})"
    if version == "vial6" and 0x52E0 <= code <= 0x52FF:
        return f"PDF({code - 0x52E0})"
    if table["_QK_LAYER_TAP"] <= code <= table["_QK_LAYER_TAP_MAX"]:
        return f"LT({(code >> 8) & 15},{name(code & 255, modern, protocol=protocol)})"
    if 0x100 <= code <= 0x1fff:
        mods = code >> 8
        if mods & 15 == 0:
            return f"0x{code:04X}"
        prefix = "R" if mods & 16 else "L"
        inner = name(code & 255, modern, protocol=protocol)
        for bit, suffix in ((1, "CTL"), (2, "SFT"), (4, "ALT"), (8, "GUI")):
            if mods & bit:
                inner = f"{prefix}{suffix}({inner})"
        return inner
    return f"0x{code:04X}"


def label(code, modern=True, *, protocol=None):
    text = name(code, modern, protocol=protocol)
    return _LABELS.get(text, text.removeprefix("KC_"))


def category(key):
    if key.startswith(("RGB_", "UG_", "RM_", "BL_")): return "Lighting"
    if key.startswith(("AU_", "MU_", "MI_")): return "Audio"
    if key.startswith(("MS_", "KC_MS", "KC_BTN", "KC_WH", "KC_ACL")): return "Mouse"
    if key.startswith(("KC_M", "KC_VOL", "KC_W", "KC_BRI")) and key not in ("KC_M", "KC_W", "KC_MINS"): return "Media"
    if key.startswith(("QK_", "EE_", "DB_", "RESET", "MAGIC_", "NK_")): return "System"
    if key.startswith(("MO(", "TO(", "TG(", "DF(", "TT(", "OSL(", "LT(")): return "Layers"
    if key.startswith(("M(", "MACRO(")): return "Macros"
    return "Keys"


def search(query="", modern=True, *, protocol=None, category=None):
    table = _table(modern, protocol)
    values = {key: value for key, value in table.items() if not key.startswith("_")}
    for fn in ("MO", "TG", "TO", "DF", "OSL", "TT", "M"):
        for index in range(min(16, table[_LAYER[fn] + "_MAX"] - table[_LAYER[fn]] + 1)):
            values[f"{fn}({index})"] = table[_LAYER[fn]] + index
    query = query.casefold().strip()
    result = []
    for key, code in values.items():
        text = _LABELS.get(key, key.removeprefix("KC_"))
        group = globals()["category"](key)
        if category and category != group:
            continue
        if query and query not in f"{key} {text} {group} {code:04x}".casefold():
            continue
        result.append(Keycode(code, key, text, group))
    group_order = {group: index for index, group in enumerate(("Keys", "Layers", "Macros", "Media", "Mouse", "Lighting", "Audio", "System"))}
    result.sort(key=lambda item: (0 if query and query in (item.name.casefold(), item.label.casefold()) else 1,
                                  group_order[item.category], item.code, item.name))
    return result


def keycode_protocol(snapshot):
    """Return the dictionary protocol; QMK keycodes_version is a different field."""
    if not isinstance(snapshot, dict):
        raise KeycodeError("Keyboard snapshot must be an object")
    vial = snapshot.get("vial_protocol")
    if vial is not None:
        if type(vial) is not int or not 0 <= vial <= 6:
            raise KeycodeError(f"Unsupported Vial keycode protocol {vial}")
        return "vial6" if vial >= 6 else "vial5"
    via = snapshot.get("via_protocol", 12)
    if type(via) is not int or not 9 <= via <= 13:
        raise KeycodeError(f"Unsupported VIA keycode protocol {via}")
    version = snapshot.get("keycodes_version")
    if via == 13 and version is not None and version not in (8, 9):
        raise KeycodeError(f"Unsupported QMK keycode version {version}")
    return via


def macro_code(index, snapshot):
    """Resolve an advertised macro slot, including extended legacy Vial slots."""
    count = snapshot.get("macro_count", 0)
    if type(index) is not int or type(count) is not int or not 0 <= index < count <= 255:
        raise KeycodeError("Macro slot is not advertised by this keyboard")
    protocol = keycode_protocol(snapshot)
    table = _table(protocol=protocol)
    if snapshot.get("vial_protocol") is not None:
        return table["_QK_MACRO"] + index
    return parse(f"MACRO({index})", protocol=protocol)


def _via_mod_names(mask):
    if not 1 <= mask <= 31 or mask & 15 == 0:
        raise KeycodeError("VIA cannot represent this modifier mask")
    side = "R" if mask & 16 else "L"
    return "|".join(f"MOD_{side}{suffix}" for bit, suffix in ((1, "CTL"), (2, "SFT"), (4, "ALT"), (8, "GUI")) if mask & bit)


def via_name(code, *, protocol=12):
    """Serialize only syntax accepted by VIA getByteForCode.

    VIA's importer silently converts unknown strings (including raw hexadecimal)
    to KC_NO. Reject unrepresentable codes instead of losing a user's mapping.
    """
    code = _u16(code)
    if protocol not in (9, 10, 11, 12, 13):
        raise KeycodeError("VIA export needs a VIA protocol dictionary")
    table = _table(protocol=protocol)
    reverse = _reverse(protocol)
    if code in reverse:
        return reverse[code]
    def basic(value):
        if value > 255 or value not in reverse:
            raise KeycodeError("VIA cannot represent this basic keycode")
        return reverse[value]
    try:
        for fn, constant in _LAYER.items():
            if fn == "M":
                continue
            if table[constant] <= code <= table[constant + "_MAX"]:
                return f"{fn}({code - table[constant]})"
        if table["_QK_MODS"] <= code <= table["_QK_MODS_MAX"]:
            mask = code >> 8
            _via_mod_names(mask)
            result = basic(code & 255)
            side = "R" if mask & 16 else "L"
            for bit, suffix in ((1, "CTL"), (2, "SFT"), (4, "ALT"), (8, "GUI")):
                if mask & bit:
                    result = f"{side}{suffix}({result})"
            return result
        if table["_QK_MOD_TAP"] <= code <= table["_QK_MOD_TAP_MAX"]:
            return f"MT({_via_mod_names((code >> 8) & 31)},{basic(code & 255)})"
        if table["_QK_LAYER_TAP"] <= code <= table["_QK_LAYER_TAP_MAX"]:
            return f"LT({(code >> 8) & 15},{basic(code & 255)})"
        if table["_QK_ONE_SHOT_MOD"] <= code <= table["_QK_ONE_SHOT_MOD_MAX"]:
            return f"OSM({_via_mod_names(code - table['_QK_ONE_SHOT_MOD'])})"
        if table["_QK_LAYER_MOD"] <= code <= table["_QK_LAYER_MOD_MAX"]:
            mask = table["_QK_LAYER_MOD_MASK"]
            remainder = code - table["_QK_LAYER_MOD"]
            return f"LM({remainder // (mask + 1)},{_via_mod_names(remainder & mask)})"
    except KeycodeError:
        pass
    raise KeycodeError(f"VIA cannot preserve keycode 0x{code:04X}. Export Omakeeb format to keep it exactly.")
