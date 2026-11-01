"""Local editing state. Editing this model never sends a device command."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json


def definition_hash(raw: dict) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def demo_snapshot(definition) -> dict:
    from .keycodes import parse
    rows, cols = definition.rows, definition.cols
    layout = [[0] * cols for _ in range(rows)]
    sequences = [
        "KC_ESC KC_1 KC_2 KC_3 KC_4 KC_5 KC_6 KC_7 KC_8 KC_9 KC_0 KC_MINS KC_EQL KC_BSPC KC_DEL",
        "KC_TAB KC_Q KC_W KC_E KC_R KC_T KC_Y KC_U KC_I KC_O KC_P KC_LBRC KC_RBRC KC_BSLS KC_PGUP",
        "KC_CAPS KC_A KC_S KC_D KC_F KC_G KC_H KC_J KC_K KC_L KC_SCLN KC_QUOT KC_ENT KC_NO KC_PGDN",
        "KC_LSFT KC_Z KC_X KC_C KC_V KC_B KC_N KC_M KC_COMM KC_DOT KC_SLSH KC_RSFT KC_UP KC_NO KC_END",
        "KC_LCTL KC_LGUI KC_LALT KC_NO KC_NO KC_NO KC_SPC KC_NO KC_NO KC_RALT MO(1) KC_LEFT KC_DOWN KC_RGHT KC_NO",
    ]
    for r in range(min(rows, len(sequences))):
        for c, key in enumerate(sequences[r].split()[:cols]):
            try:
                layout[r][c] = parse(key)
            except ValueError:
                pass
    return {"schema_version": 1, "identity": {"vendor_id": definition.vendor_id, "product_id": definition.product_id, "product": definition.name, "serial": "demo", "path": "demo"},
            "definition_sha256": definition_hash(definition.raw), "via_protocol": 9, "vial_protocol": 6,
            "keyboard_id": "d3d0000000000001", "matrix": {"rows": rows, "cols": cols},
            "layers": [layout, [[1] * cols for _ in range(rows)], [[1] * cols for _ in range(rows)], [[1] * cols for _ in range(rows)]],
            "macro_buffer": bytes(512).hex(), "macro_count": 16,
            "encoders": [[[parse("KC_VOLD"),parse("KC_VOLU")]] for _ in range(4)],
            "lighting": {"underglow_brightness":[180],"underglow_effect":[1],"underglow_speed":[128],"underglow_color":[147,180]},
            "layout_options": 0, "dynamic": {"tap_dance":[[0,0,0,0,200] for _ in range(4)],"combo":[[0,0,0,0,0] for _ in range(4)],"key_override":[[0,0,65535,0,0,0,0] for _ in range(4)],"alt_repeat_key":[[0,0,0,0] for _ in range(4)]},
            "settings": {"7":"c800", "21":"00000000"}}


class Draft:
    def __init__(self, baseline: dict):
        self.baseline = deepcopy(baseline)
        self.current = deepcopy(baseline)
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []

    @property
    def dirty(self) -> bool:
        return self.current != self.baseline

    def edit(self, operation):
        previous = deepcopy(self.current)
        operation(self.current)
        if previous != self.current:
            self.undo_stack.append(previous)
            self.undo_stack = self.undo_stack[-100:]
            self.redo_stack.clear()

    def replace(self, snapshot):
        def update(current):
            current.clear()
            current.update(deepcopy(snapshot))
        self.edit(update)

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.current)
            self.current = self.undo_stack.pop()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.current)
            self.current = self.redo_stack.pop()

    def discard(self):
        self.replace(self.baseline)

    def changes(self) -> list[str]:
        from .keycodes import name, keycode_protocol
        changes = []
        version = keycode_protocol(self.current)
        for layer, matrix in enumerate(self.current["layers"]):
            for row, cells in enumerate(matrix):
                for col, value in enumerate(cells):
                    old = self.baseline["layers"][layer][row][col]
                    if old != value:
                        changes.append(f"Layer {layer} · row {row + 1}, key {col + 1}: {name(old, protocol=version)} → {name(value, protocol=version)}")
        for layer, encoders in enumerate(self.current.get("encoders",[])):
            for index,pair in enumerate(encoders):
                for direction,code in enumerate(pair):
                    old=self.baseline["encoders"][layer][index][direction]
                    if code!=old:
                        changes.append(f"Layer {layer} · encoder {index+1} {'clockwise' if direction else 'counterclockwise'}: {name(old,protocol=version)} → {name(code,protocol=version)}")
        if self.current.get("macro_buffer")!=self.baseline.get("macro_buffer"):
            from .macros import decode
            args={"protocol":self.current["via_protocol"],"vial_protocol":self.current.get("vial_protocol")}
            try:
                before=decode(bytes.fromhex(self.baseline["macro_buffer"]),self.current["macro_count"],**args)
                after=decode(bytes.fromhex(self.current["macro_buffer"]),self.current["macro_count"],**args)
                for index,(old,new) in enumerate(zip(before,after)):
                    if old!=new: changes.append(f"Macro {index}: {old[:100]!r} → {new[:100]!r}")
            except ValueError:
                changes.append("Macro buffer changed (binary format)")
        if self.current.get("layout_options")!=self.baseline.get("layout_options"):
            changes.append(f"Physical layout options: {self.baseline.get('layout_options')} → {self.current.get('layout_options')}")
        for field,title in [("lighting","Lighting"),("dynamic","Advanced mapping"),("settings","QMK setting")]:
            for key,value in self.current.get(field,{}).items():
                old=self.baseline.get(field,{}).get(key)
                if old!=value:
                    if field=="dynamic":
                        for index,(previous,entry) in enumerate(zip(old,value)):
                            if previous!=entry:changes.append(f"{key.replace('_',' ').title()} slot {index}: {previous} → {entry}")
                    else:
                        changes.append(f"{title} · {key.replace('_',' ')}: {old} → {value}")
        return changes
