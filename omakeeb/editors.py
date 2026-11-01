"""Native controls for optional firmware capabilities."""
from copy import deepcopy
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
from . import keycodes

DYNAMIC_FIELDS = {
    "tap_dance": [("Tap", True, 65535), ("Hold", True, 65535), ("Double tap", True, 65535), ("Tap then hold", True, 65535), ("Tapping term (ms)", False, 65535)],
    "combo": [("First key", True, 65535), ("Second key", True, 65535), ("Third key (optional)", True, 65535), ("Fourth key (optional)", True, 65535), ("Output", True, 65535)],
    "key_override": [("Trigger key", True, 65535), ("Replacement", True, 65535), ("Layer bitmask", False, 65535), ("Required modifiers", False, 255), ("Forbidden modifiers", False, 255), ("Suppressed modifiers", False, 255), ("Options bitmask", False, 255)],
    "alt_repeat_key": [("Key", True, 65535), ("Alternate key", True, 65535), ("Allowed modifiers", False, 255), ("Options bitmask", False, 255)],
}
DYNAMIC_HELP = {
    "tap_dance": "Choose what this key does when tapped, held, or tapped twice. Assign TD(slot) in the keymap to use a tap dance.",
    "combo": "Press up to four keys together to send the output. Use KC_NO for unused input slots.",
    "key_override": "Replace a trigger key when the required modifiers are held. Layer and modifier masks use QMK's bit positions.",
    "alt_repeat_key": "Choose the alternate action for QMK's alternate repeat key. Modifier and options masks use the firmware's QMK definitions.",
}


def dynamic_editor(window, kind):
    from .app import box, label, button
    entries = window.draft.current["dynamic"][kind]
    dialog, content = window.dialog(kind.replace("_", " ").title(), 660, 560)
    content.append(label(DYNAMIC_HELP[kind], "muted", True))
    selector = Gtk.DropDown.new_from_strings([f"Slot {i}" for i in range(len(entries))])
    content.append(selector)
    form = box(True, 10)
    content.append(form)
    inputs = []
    for title, code, maximum in DYNAMIC_FIELDS[kind]:
        row = box()
        name = label(title); name.set_hexpand(True); row.append(name)
        entry = Gtk.Entry() if code else Gtk.SpinButton.new_with_range(0, maximum, 1)
        entry.set_size_request(230, -1)
        row.append(entry); form.append(row); inputs.append(entry)
    def populate(*_):
        for widget, field, value in zip(inputs, DYNAMIC_FIELDS[kind], window.draft.current["dynamic"][kind][selector.get_selected()]):
            if field[1]: widget.set_text(keycodes.name(value, protocol=window.code_version))
            else: widget.set_value(value)
    selector.connect("notify::selected", populate); populate()
    def stage():
        try:
            values = [keycodes.parse(widget.get_text(), protocol=window.code_version) if field[1] else widget.get_value_as_int() for widget, field in zip(inputs, DYNAMIC_FIELDS[kind])]
            proposed = deepcopy(window.draft.current)
            proposed["dynamic"][kind][selector.get_selected()] = values
            from .safety import validate_staged
            validate_staged(window.draft.baseline, proposed, window.session.protocol.lighting_specs if window.session else window.demo_lighting_specs())
            window.draft.replace(proposed); window.update_footer(); dialog.close()
        except (ValueError, RuntimeError) as error:
            window.alert("Check this entry", str(error))
    actions = box(); actions.append(button("Cancel", dialog.close)); actions.append(button("Stage entry", stage, "primary")); content.append(actions)
    dialog.present()


def settings_editor(window, parent):
    from .app import box, label
    from .protocol import setting_specs
    settings = window.draft.current.get("settings", {})
    specs = setting_specs()
    last_group = None
    for sid, raw in settings.items():
        spec = specs.get(int(sid))
        if spec is None:
            continue
        if spec["group"] != last_group:
            parent.append(label(spec["group"], "title"))
            last_group = spec["group"]
        number = int.from_bytes(bytes.fromhex(raw), "little")
        for field in spec["fields"]:
            row = box(css="card")
            title = label(field["title"], wrap=True); title.set_hexpand(True); row.append(title)
            if field["type"] == "boolean":
                control = Gtk.Switch()
                control.set_valign(Gtk.Align.CENTER)
                bit = field.get("bit", 0)
                control.set_active(bool(number & (1 << bit)))
                def changed(widget, _param, qsid=sid, b=bit, width=spec["width"]):
                    current = int.from_bytes(bytes.fromhex(window.draft.current["settings"][qsid]), "little")
                    value = (current | (1 << b)) if widget.get_active() else (current & ~(1 << b))
                    window.draft.edit(lambda d:d["settings"].__setitem__(qsid,value.to_bytes(width,"little").hex()))
                    window.update_footer()
                control.connect("notify::active", changed)
            else:
                control = Gtk.SpinButton.new_with_range(spec["min"], spec["max"], 1)
                control.set_value(number)
                def changed(widget, qsid=sid, width=spec["width"]):
                    window.draft.edit(lambda d:d["settings"].__setitem__(qsid,widget.get_value_as_int().to_bytes(width,"little").hex()))
                    window.update_footer()
                control.connect("value-changed", changed)
            row.append(control); parent.append(row)


def lighting_editor(window, parent):
    from .app import box, label
    specs = window.session.protocol.lighting_specs if window.session else window.demo_lighting_specs()
    for field, values in window.draft.current["lighting"].items():
        spec = specs.get(field, {})
        card = box(True, 12, "card")
        card.append(label(spec.get("title", field.replace("_", " ").title()), "title"))
        if field == "vialrgb_mode" and "effects" not in spec:
            # Offline profiles lack the firmware's effect list and brightness
            # limit, so the saved bytes are shown and kept, never edited.
            if len(values) == 6:
                effect = values[0] + (values[1] << 8)
                summary = (f"{'Off' if effect == 0 else f'Effect {effect}'} · Speed {values[2]} · Hue {values[3]} · "
                           f"Saturation {values[4]} · Brightness {values[5]}")
            else:
                summary = "Saved bytes: " + (" ".join(map(str, values)) or "none")
            card.append(label(summary, "mono", True))
            card.append(label("Connect the keyboard to change Vial RGB. Its firmware reports the available effects and "
                              "brightness limit. Saved values are kept unchanged.", "muted", True))
            parent.append(card)
            continue
        if field.endswith("_color") and len(values)==2:
            import colorsys
            from gi.repository import Gdk
            color_dialog=Gtk.ColorDialog(title="Choose lighting color")
            chooser=Gtk.ColorDialogButton.new(color_dialog)
            rgba=Gdk.RGBA(); rgba.red,rgba.green,rgba.blue=colorsys.hsv_to_rgb(values[0]/255,values[1]/255,1); rgba.alpha=1
            chooser.set_rgba(rgba)
            def choose(widget,_param,f=field):
                color=widget.get_rgba()
                hue,saturation,_brightness=colorsys.rgb_to_hsv(color.red,color.green,color.blue)
                window.draft.edit(lambda d:d["lighting"].__setitem__(f,[round(hue*255),round(saturation*255)]))
                window.update_footer()
                window.render_page()
            chooser.connect("notify::rgba",choose)
            card.append(chooser)
        components = spec.get("components", ["Value"] * len(values))
        for index, value in enumerate(values):
            if field == "vialrgb_mode" and index == 1:
                continue
            row = box()
            if len(values) > 1:
                row.append(label("Effect" if field == "vialrgb_mode" and index == 0 else components[index]))
            if field == "vialrgb_mode" and index == 0:
                effects = spec["effects"]
                selector = Gtk.DropDown.new_from_strings(["Off" if e == 0 else f"Effect {e}" for e in effects])
                current = values[0] + (values[1] << 8)
                selector.set_selected(effects.index(current) if current in effects else Gtk.INVALID_LIST_POSITION)
                def change(widget, _param, f=field, allowed=effects):
                    pos = widget.get_selected()
                    if pos >= len(allowed): return
                    code = allowed[pos]
                    def edit(d): d["lighting"][f][:2] = [code & 255, code >> 8]
                    window.draft.edit(edit); window.update_footer()
                selector.connect("notify::selected", change)
                row.append(selector)
            else:
                maximum = spec.get("brightness_max", 255) if field == "vialrgb_mode" and index == 5 else spec.get("max", 255)
                control = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, spec.get("min", 0), max(1, maximum), 1)
                control.set_draw_value(True); control.set_value_pos(Gtk.PositionType.RIGHT)
                control.set_hexpand(True); control.set_value(value); control.set_digits(0)
                control.connect("value-changed", lambda s,f=field,i=index:window.stage_lighting(f,i,round(s.get_value())))
                row.append(control)
            card.append(row)
        parent.append(card)
