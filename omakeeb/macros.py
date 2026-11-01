"""Strict VIA and Vial dynamic macro codecs; no keyboard I/O.

Expressions use VIA notation: text, {KC_ENT}, {KC_LCTL,KC_C},
{+KC_LSFT}text{-KC_LSFT}, {250}. Escape braces/backslashes with a
backslash. Text uses QMK's ASCII send-string layout (normally US).

Vial >=2 uses binary base-255 delays and 16-bit extended key actions;
VIA >=11 uses ASCII decimal delays. These are deliberately separate.
"""
from . import keycodes


class MacroError(ValueError):
    pass


def _dialect(protocol, vial_protocol):
    if vial_protocol is not None:
        if type(vial_protocol) is not int or not 0 <= vial_protocol <= 6:
            raise MacroError("Unsupported Vial macro protocol")
        return "vial" if vial_protocol >= 2 else "legacy"
    if type(protocol) is not int or not 9 <= protocol <= 13:
        raise MacroError("Unsupported VIA macro protocol")
    return "via" if protocol >= 11 else "legacy"


def expression_actions(expression):
    if not isinstance(expression, str) or len(expression) > 65535:
        raise MacroError("Macro expression must be a string shorter than 64 KiB")
    i = 0
    while i < len(expression):
        char = expression[i]
        if char == "\\" and i + 1 < len(expression) and expression[i + 1] in "{}\\":
            i += 1
            yield "text", expression[i]
        elif char == "{":
            end = expression.find("}", i + 1)
            if end < 0:
                raise MacroError("Unclosed macro action; escape literal braces with a backslash")
            action = expression[i + 1:end].strip()
            if not action:
                raise MacroError("Empty macro action")
            if action.isdecimal():
                yield "delay", int(action)
            else:
                direction = "tap"
                if action[0] in "+-":
                    direction = "down" if action[0] == "+" else "up"
                    action = action[1:]
                names = [name.strip() for name in action.split(",")]
                if not all(names):
                    raise MacroError("Macro action contains an empty key")
                if direction != "tap" and len(names) != 1:
                    raise MacroError("Hold and release actions need exactly one key")
                if direction == "tap" and len(names) > 1:
                    for name in names:
                        yield "down", name
                    for name in reversed(names):
                        yield "up", name
                else:
                    yield direction, names[0]
            i = end
        else:
            yield "text", char
        i += 1


def encode(expressions, protocol=12, size=None, *, vial_protocol=None):
    dialect = _dialect(protocol, vial_protocol)
    if not isinstance(expressions, (list, tuple)) or len(expressions) > 255:
        raise MacroError("Expected at most 255 macro expressions")
    out = bytearray()
    key_protocol = keycodes.keycode_protocol({"via_protocol": protocol, "vial_protocol": vial_protocol})
    for index, expression in enumerate(expressions):
        held = set()
        for action, value in expression_actions(expression):
            if action == "text":
                number = ord(value)
                if not (32 <= number <= 126 or number in (9, 10, 13)):
                    raise MacroError("QMK text macros support ASCII; use explicit key actions for other keys")
                out.append(number)
            elif action == "delay":
                if dialect == "legacy":
                    raise MacroError("Delays are unavailable in this firmware's macro format")
                if dialect == "vial":
                    if not 0 <= value <= 65024:
                        raise MacroError("Vial delay must be between 0 and 65024 milliseconds")
                    out.extend((1, 4, value % 255 + 1, value // 255 + 1))
                else:
                    if not 0 <= value <= 9999:
                        raise MacroError("VIA delay must be between 0 and 9999 milliseconds")
                    out.extend(b"\x01\x04" + str(value).encode("ascii") + b"|")
            else:
                try:
                    code = keycodes.parse(value, protocol=key_protocol)
                except ValueError as exc:
                    raise MacroError(str(exc)) from exc
                if code in (0, 1) or code >= 0xFF00 or (dialect != "vial" and code > 255):
                    raise MacroError("This macro format requires a nonzero basic HID keycode")
                if action == "down":
                    if code in held:
                        raise MacroError(f"Macro {index} presses {value} twice without releasing it")
                    held.add(code)
                elif action == "up":
                    if code not in held:
                        raise MacroError(f"Macro {index} releases {value} without pressing it")
                    held.remove(code)
                action_code = {"tap": 1, "down": 2, "up": 3}[action]
                if dialect != "legacy":
                    out.append(1)
                if code > 255:
                    encoded = (0xFF00 | code >> 8) if code & 255 == 0 else code
                    out.extend((action_code + 4, encoded & 255, encoded >> 8))
                else:
                    out.extend((action_code, code))
        if held:
            raise MacroError(f"Macro {index} leaves keys held; add their release actions")
        out.append(0)
    if size is not None:
        if type(size) is not int or not 0 <= size <= 65535:
            raise MacroError("Invalid macro buffer size")
        if len(out) > size:
            raise MacroError(f"Macros require {len(out)} bytes; keyboard has {size}")
        out.extend(bytes(size - len(out)))
    return bytes(out)


def decode(data, count, protocol=12, *, vial_protocol=None):
    dialect = _dialect(protocol, vial_protocol)
    if not isinstance(data, (bytes, bytearray)) or len(data) > 65535:
        raise MacroError("Macro buffer must be bytes, at most 65535 bytes")
    if type(count) is not int or not 0 <= count <= 255:
        raise MacroError("Invalid macro count")
    key_protocol = keycodes.keycode_protocol({"via_protocol": protocol, "vial_protocol": vial_protocol})
    result = []
    current = []
    i = 0
    while i < len(data) and len(result) < count:
        value = data[i]
        i += 1
        if value == 0:
            result.append("".join(current))
            current = []
            continue
        is_action = value in (1, 2, 3) if dialect == "legacy" else value == 1
        if not is_action:
            if not (32 <= value <= 126 or value in (9, 10, 13)):
                raise MacroError("Macro contains unsupported text bytes; retain the original raw buffer")
            char = chr(value)
            current.append("\\" + char if char in "{}\\" else char)
            continue
        if dialect == "legacy":
            action = value
        else:
            if i >= len(data):
                raise MacroError("Truncated macro action")
            action = data[i]
            i += 1
        if action == 4 and dialect != "legacy":
            if dialect == "vial":
                if i + 2 > len(data) or 0 in data[i:i + 2]:
                    raise MacroError("Invalid Vial delay")
                delay = data[i] - 1 + (data[i + 1] - 1) * 255
                i += 2
            else:
                end = data.find(b"|", i, min(i + 5, len(data)))
                if end < 0 or not data[i:end] or not all(48 <= c <= 57 for c in data[i:end]):
                    raise MacroError("Invalid VIA delay")
                delay = int(data[i:end].decode("ascii"))
                i = end + 1
            current.append("{" + str(delay) + "}")
            continue
        if action not in ((1, 2, 3, 5, 6, 7) if dialect == "vial" else (1, 2, 3)):
            raise MacroError(f"Unknown macro action {action}; retain the original raw buffer")
        length = 2 if action > 4 else 1
        if i + length > len(data) or 0 in data[i:i + length]:
            raise MacroError("Truncated or invalid macro key action")
        code = int.from_bytes(data[i:i + length], "little")
        i += length
        if action > 4:
            action -= 4
            if code > 0xFF00:
                code = (code & 255) << 8
        direction = {1: "", 2: "+", 3: "-"}[action]
        current.append("{" + direction + keycodes.name(code, protocol=key_protocol) + "}")
    if len(result) != count:
        raise MacroError(f"Expected {count} terminated macros, found {len(result)}")
    return result
