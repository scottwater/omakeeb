"""Native GTK 4 interface. All edits remain local until a reviewed apply."""
from __future__ import annotations

import argparse
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from . import __version__, keycodes, macros
from .definitions import Catalog, demo_definition, load_definition, layout_choices, unpack_layout_options, pack_layout_options
from .geometry import place_keys, hit_test
from .desktop import load_theme, discover_shortcuts
from .model import Draft, demo_snapshot


def box(vertical=False, spacing=10, css=None):
    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if css:
        widget.add_css_class(css)
    return widget


def label(text, css=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_wrap(wrap)
    if css:
        widget.add_css_class(css)
    return widget


def button(text, callback, css=None, icon=None):
    widget = Gtk.Button(label=text)
    if icon:
        row = box(spacing=7)
        row.append(Gtk.Image.new_from_icon_name(icon))
        row.append(label(text))
        widget.set_child(row)
    if css:
        widget.add_css_class(css)
    widget.connect("clicked", lambda *_: callback())
    return widget


def clear(widget):
    while child := widget.get_first_child():
        widget.remove(child)


def scroll(child, horizontal=False):
    widget = Gtk.ScrolledWindow()
    widget.set_policy(Gtk.PolicyType.AUTOMATIC if horizontal else Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    widget.set_child(child)
    widget.set_vexpand(True)
    return widget


def padded(widget, value=24):
    widget.set_margin_top(value)
    widget.set_margin_bottom(value)
    widget.set_margin_start(value)
    widget.set_margin_end(value)
    return widget


class KeyboardCanvas(Gtk.DrawingArea):
    """KLE geometry with matrix-address selection and independent test marks."""
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.rects = []
        self.set_content_height(290)
        self.set_hexpand(True)
        self.set_focusable(True)
        self.set_draw_func(self.draw)
        click = Gtk.GestureClick()
        click.connect("pressed", self.clicked)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.navigate_key)
        self.add_controller(keys)
        self.set_tooltip_text("Select a key, then choose a mapping below. Arrow keys move the selection; Enter opens the keycode editor. Coordinates are the firmware’s matrix positions.")

    def clicked(self, _gesture, _count, x, y):
        key = hit_test(self.rects, x, y)
        if key:
            self.window.selected = (key.row, key.col)
            self.window.update_selection()
            self.grab_focus()
            self.queue_draw()

    def navigate_key(self,_controller,keyval,_code,_state):
        if self.window.page != "Keymap" or not self.rects:return False
        directions={Gdk.KEY_Left:(-1,0),Gdk.KEY_Right:(1,0),Gdk.KEY_Up:(0,-1),Gdk.KEY_Down:(0,1)}
        if keyval in (Gdk.KEY_Return,Gdk.KEY_KP_Enter):
            if hasattr(self.window,"code_entry"):self.window.code_entry.grab_focus()
            return True
        if keyval not in directions:return False
        selected=next((p for p in self.rects if (p.key.row,p.key.col)==self.window.selected),None)
        if selected:
            dx,dy=directions[keyval]
            candidates=[]
            for placed in self.rects:
                x,y=placed.center[0]-selected.center[0],placed.center[1]-selected.center[1]
                forward=x*dx+y*dy
                if forward>1: candidates.append((forward*forward+4*(x*dy-y*dx)**2,placed))
            target=min(candidates,key=lambda item:item[0])[1] if candidates else selected
        else:target=self.rects[0]
        self.window.selected=(target.key.row,target.key.col)
        self.window.update_selection();self.queue_draw()
        return True

    def draw(self, _area, cr, width, height):
        w = self.window
        if not w.definition or not w.draft:
            return
        keys = [k for k in w.definition.visible_keys(w.draft.current.get("layout_options") or 0) if k.row >= 0 and k.col >= 0]
        if not keys:
            return
        self.rects = place_keys(keys,width,height)
        colors = w.theme.colors
        def color(name, fallback):
            value = colors.get(name, fallback).lstrip("#")
            return tuple(int(value[i:i+2], 16) / 255 for i in (0, 2, 4))
        for placed in self.rects:
            key = placed.key
            cr.save()
            cr.translate(*placed.center)
            cr.rotate(placed.angle)
            kw, kh, unit = placed.width, placed.height, placed.unit
            x, y = -kw/2, -kh/2
            selected = w.selected == (key.row, key.col)
            try:
                source = w.draft.baseline if w.page == "Test keys" else w.draft.current
                code = source["layers"][w.layer][key.row][key.col]
                changed = code != w.draft.baseline["layers"][w.layer][key.row][key.col]
            except (IndexError, TypeError):
                cr.restore()
                continue
            tested = (code in w.tested_codes or (key.row,key.col) in w.tested_matrix) and w.page == "Test keys"
            cr.set_source_rgb(*color("accent" if selected else "green" if tested else "lighter_background", "#24283b"))
            radius = 5
            cr.new_sub_path()
            for cx, cy, a, b in [(x+kw-radius,y+radius,-math.pi/2,0), (x+kw-radius,y+kh-radius,0,math.pi/2), (x+radius,y+kh-radius,math.pi/2,math.pi),(x+radius,y+radius,math.pi,math.pi*1.5)]:
                cr.arc(cx,cy,radius,a,b)
            cr.close_path()
            cr.fill_preserve()
            cr.set_source_rgba(*color("accent" if selected else "muted", "#414868"), .8)
            cr.set_line_width(1)
            cr.stroke()
            cr.set_source_rgb(*color("background" if selected or tested else "foreground", "#a9b1d6"))
            cr.select_font_face("sans-serif", 0, 0)
            title = w.key_label(code)
            if len(title) > 11:
                title = title[:10] + "…"
            cr.set_font_size(min(13, unit * .24))
            ext = cr.text_extents(title)
            cr.move_to(x+(kw-ext.width)/2-ext.x_bearing, y+kh/2-ext.y_bearing/2)
            cr.show_text(title)
            if changed:
                cr.set_source_rgb(*color("background" if selected else "accent", "#7aa2f7"))
                cr.arc(x+kw-7,y+7,2.2,0,math.pi*2)
                cr.fill()
            cr.restore()


class Window(Gtk.ApplicationWindow):
    def __init__(self, app, demo=False):
        super().__init__(application=app, title="Omakeeb")
        self.set_default_size(1240, 850)
        self.set_size_request(860, 640)
        self.theme = load_theme()
        self.theme_fingerprint = None
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.refresh_theme()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omakeeb-device")
        self.catalog = Catalog()
        self.definition = None
        self.draft = None
        self.session = None
        self.transport = None
        self.busy = False
        self.demo = False
        self.offline = False
        self.layer = 0
        self.selected = None
        self.page = "Keymap"
        self.tested_codes = set()
        self.tested_matrix = set()
        self.matrix_testing = False
        self.matrix_inflight = False
        self.event_log = []
        self.pending_file = None
        self.macro_edits = {}
        self.macro_invalid = set()
        self.build_shell()
        self.connect("close-request", self.on_close)
        self.connect("notify::is-active", self.focus_changed)
        self.theme_timer = GLib.timeout_add_seconds(2, self.refresh_theme)
        self.render_page()
        if demo:
            self.open_demo()

    @property
    def code_version(self):
        if not self.draft:
            return 12
        data = self.draft.current
        return keycodes.keycode_protocol(data)

    def custom_codes(self):
        if not self.definition:return []
        result=[]
        for index,entry in enumerate(self.definition.raw.get("customKeycodes",[])):
            if not isinstance(entry,dict):continue
            try:code=keycodes.parse(f"CUSTOM({index})",protocol=self.code_version)
            except ValueError:continue
            title=str(entry.get("name",f"Custom {index}"))[:100]
            result.append((code,title,str(entry.get("shortName",title))[:20],str(entry.get("title",title))[:240]))
        return result

    def key_label(self,code):
        for value,_name,short,_tip in self.custom_codes():
            if value==code:return short
        return keycodes.label(code,protocol=self.code_version)

    def refresh_theme(self):
        theme = load_theme()
        fingerprint = repr(theme)
        if fingerprint == self.theme_fingerprint:
            return True
        self.theme = theme
        self.theme_fingerprint = fingerprint
        c = theme.colors
        bg = c.get("background", "#1a1b26")
        panel = c.get("dark_background", "#13141c")
        raised = c.get("lighter_background", "#24283b")
        fg = c.get("foreground", "#a9b1d6")
        accent = c.get("accent", "#7aa2f7")
        muted = c.get("muted", "#414868")
        css = f"""
        window {{ background: {bg}; color: {fg}; }}
        .sidebar {{ background: {panel}; border-right: 1px solid {muted}; }}
        .brand {{ font-size: 22px; font-weight: 700; letter-spacing: -1px; }}
        .eyebrow {{ color: {accent}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
        .heading {{ font-size: 27px; font-weight: 700; letter-spacing: -.5px; }}
        .title {{ font-size: 16px; font-weight: 700; }}
        .muted {{ opacity: .70; }}
        .small {{ font-size: 11px; }}
        .mono {{ font-family: monospace; }}
        .topbar {{ border-bottom: 1px solid {muted}; padding: 16px 24px; }}
        .footer {{ border-top: 1px solid {muted}; padding: 12px 24px; background: {panel}; }}
        .card {{ background: {panel}; border: 1px solid {muted}; border-radius: 10px; padding: 18px; }}
        button {{ background: {raised}; color: {fg}; border: 1px solid transparent; border-radius: 6px; padding: 9px 13px; box-shadow: none; }}
        button:hover {{ border-color: {accent}; }}
        button:disabled {{ opacity: .4; }}
        button.primary {{ background: {accent}; color: {bg}; font-weight: 700; }}
        button.nav {{ background: transparent; padding: 11px 13px; }}
        button.nav.active {{ background: {raised}; color: {accent}; }}
        button.key-option {{ padding: 7px 10px; }}
        button.selected {{ border-color: {accent}; color: {accent}; }}
        entry, textview, textview text, spinbutton {{ background: {raised}; color: {fg}; border-radius: 5px; }}
        entry {{ padding: 7px; }}
        list, listbox, row, viewport {{ background: transparent; color: {fg}; }}
        .status {{ color: {accent}; }}
        .warning {{ color: {c.get('yellow', '#e0af68')}; }}
        separator {{ background: {muted}; min-height: 1px; }}
        """
        self.provider.load_from_string(css)
        if hasattr(self, "canvas"):
            self.canvas.queue_draw()
        return True

    def build_shell(self):
        root = box()
        root.set_spacing(0)
        self.set_child(root)
        side = padded(box(True, 6, "sidebar"), 0)
        side.set_size_request(206, -1)
        brand = padded(box(True, 8), 24)
        brand.append(label("⌨  omakeeb", "brand"))
        brand.append(label("YOUR KEYBOARD, AT HOME", "eyebrow"))
        side.append(brand)
        self.nav_buttons = {}
        sections = [("Keymap","input-keyboard-symbolic"), ("Omarchy","starred-symbolic"), ("Macros","view-list-symbolic"), ("Lighting","display-brightness-symbolic"), ("Encoders","view-refresh-symbolic"), ("Advanced","preferences-system-symbolic"), ("Test keys","object-select-symbolic"), ("Backups","document-save-symbolic")]
        for name, icon in sections:
            b = button(name, lambda n=name: self.navigate(n), "nav", icon)
            b.set_margin_start(12)
            b.set_margin_end(12)
            self.nav_buttons[name] = b
            side.append(b)
        spacer = box()
        spacer.set_vexpand(True)
        side.append(spacer)
        bottom = padded(box(True, 9), 20)
        bottom.append(label("LOCAL FIRST", "eyebrow"))
        bottom.append(label("Changes stay in your draft\nuntil you review and apply.", "muted", True))
        bottom.append(button("Keyboard library", self.library_dialog, icon="folder-symbolic"))
        bottom.append(label(f"Omakeeb {__version__} · GTK 4", "small"))
        side.append(bottom)
        root.append(side)
        main = box(True, 0)
        main.set_hexpand(True)
        root.append(main)
        top = box(css="topbar")
        dev = box(True, 3)
        self.device_label = label("No keyboard connected", "title")
        self.device_detail = label("Connect a VIA or Vial keyboard to get started", "muted")
        dev.append(self.device_label)
        dev.append(self.device_detail)
        dev.set_hexpand(True)
        top.append(dev)
        self.connect_button = button("Connect", self.connect_dialog, icon="network-wired-symbolic")
        top.append(self.connect_button)
        self.import_button = button("Import", lambda: self.file_dialog(False), icon="document-open-symbolic")
        self.export_button = button("Export", self.export_dialog, icon="document-save-symbolic")
        top.append(self.import_button)
        top.append(self.export_button)
        main.append(top)
        self.body = padded(box(True, 18), 26)
        main.append(scroll(self.body))
        footer = box(css="footer")
        self.status_label = label("Ready. No device changes have been made.", "muted", True)
        self.status_label.set_hexpand(True)
        footer.append(self.status_label)
        self.undo_button = button("Undo", self.undo)
        self.discard_button = button("Discard", self.discard)
        self.apply_button = button("Review changes", self.review, "primary")
        footer.append(self.undo_button)
        footer.append(self.discard_button)
        footer.append(self.apply_button)
        main.append(footer)
        self.key_controller = Gtk.EventControllerKey()
        self.key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.key_controller.connect("key-pressed", self.key_pressed)
        self.key_controller.connect("key-released", self.key_released)
        self.add_controller(self.key_controller)
        self.update_footer()

    def update_footer(self, message=None):
        dirty = bool(self.draft and self.draft.dirty)
        self.apply_button.set_label("Export changes" if self.offline else "Review changes")
        self.apply_button.set_sensitive(dirty and not self.busy and not (self.session and self.session.failed))
        self.body.set_sensitive(not self.busy)
        for b in self.nav_buttons.values(): b.set_sensitive(not self.busy)
        self.discard_button.set_sensitive((dirty or bool(self.macro_invalid)) and not self.busy)
        self.undo_button.set_sensitive(bool(self.draft and self.draft.undo_stack) and not self.busy)
        self.connect_button.set_sensitive(not self.busy)
        self.import_button.set_sensitive(bool(self.draft) and not self.busy)
        self.export_button.set_sensitive(bool(self.draft) and not self.busy and not self.macro_invalid)
        if self.macro_invalid: self.apply_button.set_sensitive(False)
        if message:
            self.status_label.set_text(message)
        elif self.macro_invalid:
            self.status_label.set_text("A macro is incomplete. Finish or discard it in Macros before exporting or applying.")
        elif self.busy:
            self.status_label.set_text("Working with the keyboard…")
        elif dirty:
            self.status_label.set_text(f"{len(self.draft.changes())} pending changes · saved only in this draft")
        elif self.draft:
            self.status_label.set_text("Offline profile · export to save changes" if self.offline else "Demo workspace · no device attached" if self.demo else "Configuration backed up · no pending changes")
        if hasattr(self,"canvas"):
            self.canvas.queue_draw()

    def navigate(self, page):
        if page != "Test keys": self.matrix_testing = False
        self.page = page
        self.render_page()

    def render_page(self):
        clear(self.body)
        for name, b in self.nav_buttons.items():
            b.remove_css_class("active")
            if name == self.page:
                b.add_css_class("active")
        if not self.draft:
            self.welcome()
            return
        subtitles = {"Keymap":"Make every key yours. Select a key, then choose its new action.", "Omarchy":"Put your desktop shortcuts exactly where you want them.", "Macros":"Give a sequence of actions a single key.", "Lighting":"Set the mood with the controls your firmware exposes.", "Encoders":"A turn in the right direction. Map each rotation independently.", "Advanced":"Fine-tune the features provided by your keyboard’s firmware.", "Test keys":"Check what reaches the app, one key at a time.", "Backups":"A way back, before every change."}
        self.body.append(label(self.page, "heading"))
        self.body.append(label(subtitles[self.page], "muted", True))
        {"Keymap":self.keymap_page,"Omarchy":self.omarchy_page,"Macros":self.macros_page,"Lighting":self.lighting_page,"Encoders":self.encoders_page,"Advanced":self.advanced_page,"Test keys":self.test_page,"Backups":self.backups_page}[self.page]()
        self.update_footer()

    def welcome(self):
        self.body.append(label("A little more you.\nIn every keystroke.", "heading"))
        self.body.append(label("A native home for your QMK keyboard. Shape your layers, build shortcuts, and keep a copy of what worked.", "muted", True))
        card = box(True, 18, "card")
        card.append(label("Start with your keyboard", "title"))
        card.append(label("Connect a keyboard running VIA or Vial firmware. Omakeeb reads its current configuration and saves a backup before you edit.", wrap=True))
        actions = box()
        actions.append(button("Connect a keyboard", self.connect_dialog, "primary"))
        actions.append(button("Explore a demo", self.open_demo))
        actions.append(button("Open saved profile…", self.open_profile_dialog))
        card.append(actions)
        self.body.append(card)
        for title, detail in [("Works offline", "The bundled VIA library identifies supported boards. Vial boards supply their own layout."), ("At home in Omarchy", "Your active theme and desktop shortcuts, together in one workspace."), ("You decide when to write", "Edit a draft, review the changes, then apply. Configuration backups are saved automatically.")]:
            row = box(True, 5, "card")
            row.append(label(title,"title"))
            row.append(label(detail,"muted",True))
            self.body.append(row)

    def open_demo(self):
        def go():
            self.close_transport()
            self.definition = demo_definition()
            self.draft = Draft(demo_snapshot(self.definition))
            self.macro_edits.clear(); self.macro_invalid.clear()
            self.session = None
            self.demo = True
            self.offline = False
            self.layer = 0
            self.selected = None
            self.device_label.set_text(self.definition.name)
            self.device_detail.set_text("Demo workspace · 4 layers · hardware is untouched")
            self.render_page()
        self.guard_draft(go)

    def layer_bar(self, parent):
        row = box(spacing=6)
        row.append(label("LAYER", "eyebrow"))
        for index in range(len(self.draft.current["layers"])):
            b = button(str(index), lambda n=index: self.select_layer(n), "selected" if self.layer == index else None)
            row.append(b)
        parent.append(row)

    def layout_controls(self):
        labels = self.definition.layout_labels
        if not labels or self.draft.current.get("layout_options") is None: return
        choices = unpack_layout_options(labels,self.draft.current["layout_options"])
        row = box()
        for group,(title,options) in enumerate(layout_choices(labels)):
            column = box(True,4); column.append(label(title,"small"))
            menu = Gtk.DropDown.new_from_strings(options); menu.set_selected(choices.get(group,0))
            def change(widget,_param,g=group):
                values=unpack_layout_options(labels,self.draft.current["layout_options"])
                values[g]=widget.get_selected()
                encoded=pack_layout_options(labels,values,self.draft.current["layout_options"])
                self.draft.edit(lambda d:d.__setitem__("layout_options",encoded))
                self.selected=None; self.update_selection(); self.update_footer()
            menu.connect("notify::selected",change); column.append(menu); row.append(column)
        self.body.append(row)

    def select_layer(self, index):
        self.layer = index
        self.render_page()

    def keymap_page(self):
        self.layer_bar(self.body)
        self.layout_controls()
        card = box(True, 8, "card")
        self.canvas = KeyboardCanvas(self)
        card.append(self.canvas)
        hint = box()
        hint.append(label("●  Edited key", "small"))
        gap = box(); gap.set_hexpand(True); hint.append(gap)
        hint.append(label("Layer 0 is the base layer. Transparent keys fall through.", "small"))
        card.append(hint)
        card.append(label("Arrow keys select a key · Enter edits its QMK code · Ctrl+Z undoes", "small"))
        self.body.append(card)
        self.selection_label = label("Select a key on your keyboard", "title")
        self.body.append(self.selection_label)
        editor = box()
        self.code_entry = Gtk.Entry(placeholder_text="QMK keycode · e.g. KC_A, LGUI(KC_E), MO(1), 0x0004")
        self.code_entry.set_hexpand(True)
        self.code_entry.connect("activate", lambda *_: self.assign_entry())
        editor.append(self.code_entry)
        editor.append(button("Assign", self.assign_entry, "primary"))
        self.body.append(editor)
        category_row = box()
        category_row.append(label("KEY GROUP", "eyebrow"))
        categories = ["Keys", "All", "Media", "Layers", "Macros", "Lighting", "Mouse", "Audio", "System", "Custom"]
        self.palette_category = Gtk.DropDown.new_from_strings(categories)
        category_row.append(self.palette_category)
        self.body.append(category_row)
        search = Gtk.SearchEntry(placeholder_text="Find a key, media action, layer, or macro…")
        self.palette_category.connect("notify::selected", lambda *_: self.fill_palette(search.get_text()))
        search.connect("search-changed", lambda e: self.fill_palette(e.get_text()))
        self.body.append(search)
        self.palette = Gtk.FlowBox(max_children_per_line=10, selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6)
        self.body.append(self.palette)
        self.fill_palette("")
        self.update_selection()

    def fill_palette(self, query):
        clear(self.palette)
        category = self.palette_category.get_selected_item().get_string()
        for code,title,short,tip in self.custom_codes():
            if category not in ("Custom","All") and not query:continue
            if query.lower() not in f"{title} {short} {tip}".lower():continue
            b=button(short,lambda value=code:self.assign_code(value),"key-option");b.set_tooltip_text(tip);self.palette.insert(b,-1)
        for item in list(keycodes.search(query, protocol=self.code_version, category=None if query or category == "All" else category))[:100]:
            code = getattr(item, "code", getattr(item, "value", 0))
            name = getattr(item, "name", keycodes.name(code, protocol=self.code_version))
            b = button(keycodes.label(code, protocol=self.code_version), lambda value=code:self.assign_code(value), "key-option")
            b.set_tooltip_text(f"{name} · 0x{code:04X}")
            self.palette.insert(b, -1)

    def update_selection(self):
        if self.selected and self.draft:
            r,c = self.selected
            code = self.draft.current["layers"][self.layer][r][c]
            if hasattr(self,"selection_label"):
                self.selection_label.set_text(f"Layer {self.layer} · row {r + 1}, key {c + 1} · {keycodes.name(code, protocol=self.code_version)}")
            if hasattr(self,"code_entry"):
                self.code_entry.set_text(keycodes.name(code, protocol=self.code_version))
        if hasattr(self,"canvas"):
            self.canvas.queue_draw()

    def assign_entry(self):
        try:
            self.assign_code(keycodes.parse(self.code_entry.get_text().strip(), protocol=self.code_version))
        except ValueError as error:
            self.alert("Check this keycode", str(error))

    def assign_code(self, code):
        if self.busy:
            return
        if self.selected is None:
            self.alert("Choose a key first", "Select a key in the Keymap tab, then choose the action to put there.")
            return
        r,c = self.selected
        from .safety import validate_new_keycode, SafetyError
        try: validate_new_keycode(code,self.draft.current["layers"][self.layer][r][c],self.draft.current)
        except SafetyError as error:
            self.alert("Protected firmware action",str(error)); return
        self.draft.edit(lambda data: data["layers"][self.layer][r].__setitem__(c,code))
        self.update_selection()
        self.update_footer()

    def omarchy_page(self):
        self.body.append(label("Choose a destination in Keymap, then assign a shortcut here. Your desktop bindings are read, never changed.", "muted", True))
        self.body.append(label(f"Destination: layer {self.layer}, row {self.selected[0]+1}, key {self.selected[1]+1}" if self.selected else "No destination selected yet", "status"))
        entry = Gtk.SearchEntry(placeholder_text="Search your Omarchy shortcuts…")
        self.body.append(entry)
        rows = box(True,8)
        self.body.append(rows)
        shortcuts = discover_shortcuts()
        def fill(query=""):
            clear(rows)
            for item in shortcuts:
                chord = " + ".join((*item.modifiers, item.key))
                if query.lower() not in f"{item.label} {item.category} {chord}".lower():
                    continue
                row = box(css="card")
                info = box(True,4); info.set_hexpand(True)
                info.append(label(item.label,"title"))
                info.append(label(f"{item.category} · {chord}","muted"))
                row.append(info)
                b = button("Assign", lambda value=item.keycode:self.assign_code(value))
                b.set_sensitive(item.keycode is not None and self.selected is not None)
                b.set_tooltip_text(item.source if item.keycode is not None else "This binding cannot be represented by a single QMK keycode.")
                row.append(b); rows.append(row)
        entry.connect("search-changed",lambda e:fill(e.get_text()))
        fill()

    def macro_protocol(self):
        return self.draft.current.get("via_protocol",12)

    def macros_page(self):
        data = self.draft.current
        count = data.get("macro_count",0)
        if not count:
            self.empty_state("No dynamic macros", "This firmware does not expose writable macro slots.")
            return
        try:
            expressions = macros.decode(bytes.fromhex(data["macro_buffer"]), count, protocol=self.macro_protocol(), vial_protocol=data.get("vial_protocol"))
        except ValueError as error:
            self.empty_state("Macro data is preserved", f"This macro format cannot safely be edited: {error}")
            return
        self.body.append(label("Text types literally. Use {KC_LCTL,KC_C} for a chord, {250} for a delay in milliseconds, and {+KC_LSFT} / {-KC_LSFT} to hold / release.", "muted", True))
        row = box()
        selector = Gtk.DropDown.new_from_strings([f"Macro {i}" for i in range(count)])
        row.append(selector)
        usage = label(f"{len(bytes.fromhex(data['macro_buffer']))} bytes available · {count} slots", "muted")
        row.append(usage)
        self.body.append(row)
        text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        text.get_buffer().set_enable_undo(True)
        text.set_top_margin(16); text.set_left_margin(16); text.set_bottom_margin(16); text.set_right_margin(16)
        text.add_css_class("mono")
        text.set_size_request(-1,180)
        self.body.append(text)
        message=label("Valid macros are staged automatically. Hardware changes only after review.","muted",True)
        self.body.append(message)
        changing={"slot":0,"loading":False}
        def edited(*_):
            if changing["loading"]:return
            buf=text.get_buffer()
            expression=buf.get_text(buf.get_start_iter(),buf.get_end_iter(),True)
            slot=changing["slot"]
            self.macro_edits[slot]=expression
            proposed=list(expressions); proposed[slot]=expression
            try:
                encoded=macros.encode(proposed,protocol=self.macro_protocol(),size=len(bytes.fromhex(data["macro_buffer"])),vial_protocol=data.get("vial_protocol"))
                expressions[:]=proposed
                self.draft.edit(lambda d:d.__setitem__("macro_buffer",encoded.hex()))
                self.macro_invalid.discard(slot)
                used=len(macros.encode(proposed,protocol=self.macro_protocol(),vial_protocol=data.get("vial_protocol")))
                message.set_text(f"Macro staged · {used} bytes used. Review changes to apply.")
            except ValueError as error:
                self.macro_invalid.add(slot)
                message.set_text(f"Not staged: {error}")
            self.update_footer()
        def select(*_):
            changing["loading"]=True
            changing["slot"]=selector.get_selected()
            text.get_buffer().set_text(self.macro_edits.get(changing["slot"],expressions[changing["slot"]]))
            changing["loading"]=False
            message.set_text("This macro needs correction before applying." if changing["slot"] in self.macro_invalid else "Valid macros are staged automatically. Hardware changes only after review.")
        text.get_buffer().connect("changed",edited)
        selector.connect("notify::selected",select); select()
        def assign():
            try:self.assign_code(keycodes.macro_code(selector.get_selected(),self.draft.current))
            except ValueError as error:self.alert("Macro cannot be assigned",str(error))
        self.body.append(button("Assign to selected key",assign))
        self.body.append(label("Text uses QMK’s ASCII send-string layout, normally US. Holds must have a matching release.","muted",True))

    def lighting_page(self):
        lighting = self.draft.current.get("lighting",{})
        if not lighting:
            self.empty_state("No verified lighting controls", "This firmware rejected its advertised lighting controls, so their values could not be read or backed up. You can assign RGB actions in Keymap → Lighting." if self.draft.current.get("unsupported_controls") else "Omakeeb only offers lighting controls declared by this keyboard’s definition and supported by its firmware. RGB keycodes can still be assigned in Keymap.")
            return
        from .editors import lighting_editor
        lighting_editor(self,self.body)
        self.body.append(label("Changes are staged. Apply once you are happy with the values; effects are not previewed on the device while editing.","muted",True))

    def demo_lighting_specs(self):
        return {name:{"min":0,"max":255,"components":["Hue","Saturation"] if name.endswith("color") else ["Value"]} for name in self.draft.current.get("lighting",{})}

    def stage_lighting(self,field,index,value):
        self.draft.edit(lambda d:d["lighting"][field].__setitem__(index,value))
        self.update_footer()

    def encoders_page(self):
        encoders = self.draft.current.get("encoders",[])
        if not encoders or not encoders[0]:
            self.empty_state("No dynamic encoder mappings", "Some knobs are implemented as ordinary matrix keys. Those actions are edited in Keymap. Dynamic encoder mappings appear here when the firmware exposes them.")
            return
        self.layer_bar(self.body)
        for index, values in enumerate(encoders[self.layer]):
            card = box(True,10,"card")
            card.append(label(f"Encoder {index+1}","title"))
            for direction, value in enumerate(values):
                row = box()
                clockwise = direction == 1
                row.append(label("↷ Clockwise" if clockwise else "↶ Counterclockwise"))
                entry = Gtk.Entry(text=keycodes.name(value,protocol=self.code_version)); entry.set_hexpand(True)
                row.append(entry)
                def assign(i=index,d=direction,e=entry):
                    try:
                        code = keycodes.parse(e.get_text(),protocol=self.code_version)
                        self.draft.edit(lambda data:data["encoders"][self.layer][i].__setitem__(d,code))
                        self.update_footer()
                    except ValueError as error:
                        self.alert("Check this keycode",str(error))
                row.append(button("Stage",assign)); card.append(row)
            self.body.append(card)

    def advanced_page(self):
        if self.session and self.draft.current.get("vial_protocol") is not None:
            actions=box()
            actions.append(button("Unlock keyboard physically…",self.unlock_dialog))
            actions.append(button("Lock keyboard",lambda:self.run_worker(self.session.protocol.lock_keyboard,lambda _status:self.alert("Keyboard locked","Vial’s physical lock is active. Unlock it before your next apply."))))
            self.body.append(actions)
        dynamic = self.draft.current.get("dynamic",{})
        if not any(dynamic.values()):
            self.empty_state("Vial advanced mappings", "Tap dance, combos, key overrides, and repeat actions appear when Vial firmware advertises them. Standard VIA firmware does not expose these features.")
        for kind, entries in dynamic.items():
            if not entries:
                continue
            card = box(True,8,"card")
            card.append(label(kind.replace("_"," ").title(),"title"))
            card.append(label(f"{len(entries)} slots available. Entries are preserved in every backup.","muted"))
            card.append(button("Edit entries",lambda k=kind:self.dynamic_dialog(k)))
            self.body.append(card)
        settings = self.draft.current.get("settings",{})
        self.body.append(label(f"QMK settings: {len(settings)} values backed up." if settings else "QMK settings are not exposed by this firmware.","muted"))
        if settings:
            from .editors import settings_editor
            settings_editor(self,self.body)
        self.body.append(label("Firmware compilation features require a firmware build. Omakeeb does not flash firmware or erase EEPROM.","muted",True))

    def dynamic_dialog(self, kind):
        from .editors import dynamic_editor
        dynamic_editor(self, kind)

    def test_page(self):
        self.body.append(label("Focused test · desktop shortcuts may be intercepted by Hyprland. This tests the current device mapping, not unapplied draft changes.","muted",True))
        self.layer_bar(self.body)
        self.canvas = KeyboardCanvas(self)
        self.body.append(self.canvas)
        self.test_label = label("Press a key to begin", "heading")
        self.body.append(self.test_label)
        self.test_history = label("", "mono", True)
        self.body.append(self.test_history)
        def reset():
            self.tested_codes.clear(); self.tested_matrix.clear(); self.event_log.clear()
            self.test_label.set_text("Press a key to begin"); self.test_history.set_text("")
            self.canvas.queue_draw()
        actions = box()
        actions.append(button("Clear test",reset))
        if self.session:
            self.matrix_button=button("Start physical matrix test",self.toggle_matrix_test)
            actions.append(self.matrix_button)
            if self.draft.current.get("vial_protocol") is not None:
                actions.append(button("Unlock keyboard…",self.unlock_dialog))
        self.body.append(actions)
        self.body.append(label("Only keys delivered to this focused window are shown. Nothing is recorded to disk.","small",True))
        self.canvas.grab_focus()

    def toggle_matrix_test(self):
        self.matrix_testing=not self.matrix_testing
        self.matrix_button.set_label("Stop physical matrix test" if self.matrix_testing else "Start physical matrix test")
        if self.matrix_testing:
            self.tested_matrix.clear()
            self.matrix_tick()

    def matrix_tick(self):
        if not self.matrix_testing or self.page!="Test keys" or not self.session:
            return False
        if self.matrix_inflight or self.busy or not self.is_active():
            GLib.timeout_add(150,self.matrix_tick); return False
        self.matrix_inflight=True
        protocol=self.session.protocol
        future=self.executor.submit(protocol.read_matrix_state,self.definition.raw)
        def done():
            self.matrix_inflight=False
            try:
                pressed=future.result()
                if self.matrix_testing and self.page=="Test keys":
                    self.tested_matrix.update(pressed)
                    self.test_label.set_text(f"{len(pressed)} pressed · {len(self.tested_matrix)} switches tested")
                    self.canvas.queue_draw()
            except Exception as error:
                self.matrix_testing=False
                if self.session and self.session.protocol is protocol and protocol._poisoned: self.session.failed=True
                self.update_footer()
                if self.page=="Test keys":
                    self.matrix_button.set_label("Start physical matrix test")
                    self.alert("Physical test unavailable",str(error))
            if self.matrix_testing: GLib.timeout_add(150,self.matrix_tick)
            return False
        future.add_done_callback(lambda _:GLib.idle_add(done))
        return False

    def unlock_dialog(self):
        if not self.session or self.busy:return
        self.matrix_testing=False
        dialog,content=self.dialog("Unlock with your keyboard",620,370)
        message=label("Vial requires you to hold a physical key combination. Start to see the key positions, then hold them until the firmware unlocks.",wrap=True)
        content.append(message)
        progress=label("", "status",True); content.append(progress)
        state={"started":False,"finished":False,"inflight":False}
        def abort(*_):
            if state["inflight"]: return True
            if state["started"] and not state["finished"]:
                state["started"]=False
                self.close_transport(); self.session.failed=True
                self.alert("Unlock interrupted","Unplug and reconnect this keyboard to leave the firmware’s unlock handshake. Reconnect in Omakeeb before trying another operation.")
            self.busy=False; self.update_footer()
            return False
        dialog.connect("close-request",abort)
        def poll():
            if state["finished"] or not state["started"]:return False
            state["inflight"]=True
            future=self.executor.submit(self.session.protocol.poll_unlock)
            def done():
                state["inflight"]=False
                try:
                    status=future.result()
                    if status["unlocked"]:
                        state["finished"]=True; self.busy=False
                        progress.set_text("Keyboard unlocked. You can now review and apply changes.")
                        self.update_footer(); return False
                    progress.set_text(f"Keep holding the keys · {status.get('remaining',0)} checks remaining")
                    GLib.timeout_add(200,poll)
                except Exception as error:
                    progress.set_text(str(error)); self.busy=False; self.update_footer()
                return False
            future.add_done_callback(lambda _:GLib.idle_add(done))
            return False
        def start():
            start_button.set_sensitive(False)
            state["inflight"]=True
            def ready(status):
                state["inflight"]=False
                state["started"]=True
                if status["unlocked"]:
                    state["finished"]=True; progress.set_text("Keyboard is already unlocked."); return
                names=[f"row {r+1}, key {c+1}" for r,c in status.get("keys",[])]
                message.set_text("Hold together: "+" and ".join(names)+". These are matrix positions shown when you select a key in Keymap.")
                self.busy=True; self.update_footer(); poll()
            def failed(_error):
                state["inflight"]=False; state["started"]=True
                progress.set_text("Unlock could not be confirmed. Close, unplug, and reconnect before continuing.")
            self.run_worker(self.session.protocol.begin_unlock,ready,failed)
        start_button=button("Start physical unlock",start,"primary");content.append(start_button)
        content.append(button("Close",dialog.close));dialog.present()

    def key_pressed(self,_controller,keyval,keycode,state):
        if self.page=="Test keys" and self.draft:
            name = Gdk.keyval_name(keyval) or str(keyval)
            from .tester import qmk_for_keycode
            code = qmk_for_keycode(keycode)
            if code is not None:
                self.tested_codes.add(code)
            self.event_log = [f"↓ {name} · hardware {keycode}"] + self.event_log[:15]
            self.test_label.set_text(name)
            self.test_history.set_text("\n".join(self.event_log))
            self.canvas.queue_draw()
            return keyval != Gdk.KEY_Tab
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_z,Gdk.KEY_Z):
            if self.busy:return True
            focus=self.get_focus()
            if focus and (isinstance(focus,(Gtk.Editable,Gtk.TextView)) or focus.get_ancestor(Gtk.Entry) or focus.get_ancestor(Gtk.TextView)):
                return False
            if self.draft:
                if state & Gdk.ModifierType.SHIFT_MASK:
                    self.draft.redo(); self.macro_edits.clear(); self.macro_invalid.clear(); self.render_page()
                else: self.undo()
                return True
        return False

    def key_released(self,_controller,keyval,keycode,state):
        if self.page=="Test keys" and hasattr(self,"test_history"):
            self.event_log = [f"↑ {Gdk.keyval_name(keyval)}"] + self.event_log[:15]
            self.test_history.set_text("\n".join(self.event_log))

    def focus_changed(self,*_):
        if self.page=="Test keys" and hasattr(self,"test_label") and not self.is_active():
            self.test_label.set_text("Test paused · focus this window")

    def backups_page(self):
        card = box(True,12,"card")
        card.append(label("Configuration is recoverable", "title"))
        card.append(label("The original configuration is saved on connection, and again before each apply. Backups contain the readable keymap, macro bytes, lighting, encoders, and advertised Vial settings.",wrap=True))
        card.append(button("Export current draft",self.export_dialog))
        self.body.append(card)
        card = box(True,10,"card")
        card.append(label("Configuration ≠ firmware", "title"))
        card.append(label("VIA and Vial do not provide a general way to read back executable firmware. These backups cannot restore a damaged firmware image. Keep the manufacturer’s exact firmware and recovery instructions with your keyboard. Omakeeb never flashes, resets, or enters the bootloader.",wrap=True))
        self.body.append(card)
        archive = box(True, 8, "card")
        archive.append(label("Keep the original firmware", "title"))
        archive.append(label("Archive a manufacturer firmware file with a checksum for safekeeping. This does not read firmware from the keyboard or verify compatibility.", "muted", True))
        archive.append(button("Archive firmware file…", self.archive_firmware_dialog))
        self.body.append(archive)
        if self.session:
            directory = Path(self.session.backups.directory)
            self.body.append(label(str(directory),"mono",True))
            for path in sorted(directory.glob("*.json"),reverse=True)[:20]:
                row = box(css="card")
                title = label(path.name,"small",True); title.set_hexpand(True); row.append(title)
                row.append(button("Restore to draft",lambda p=path:self.import_path(p)))
                self.body.append(row)
        else:
            self.body.append(label("Offline workspaces create no automatic hardware backups. Export a draft to save your work.","muted",True))

    def archive_firmware_dialog(self):
        from .firmware_files import archive_firmware
        dialog=Gtk.FileChooserNative.new("Archive manufacturer firmware",self,Gtk.FileChooserAction.OPEN,"Archive","Cancel")
        def response(native,result):
            if result==Gtk.ResponseType.ACCEPT:
                file=native.get_file()
                if file and file.get_path():
                    try:
                        path=archive_firmware(Path(file.get_path()),identity=self.draft.current["identity"])
                        self.alert("Firmware file archived",f"Saved with a SHA-256 checksum. This file has not been flashed or checked for hardware compatibility.\n\n{path}")
                    except Exception as error:self.alert("Archive stopped",str(error))
            native.destroy()
        dialog.connect("response",response); dialog.show()

    def empty_state(self,title,detail):
        card = box(True,10,"card"); card.append(label(title,"title")); card.append(label(detail,"muted",True)); self.body.append(card)

    def dialog(self,title,width=580,height=300):
        dialog = Gtk.Window(title=title,transient_for=self,modal=True,default_width=width,default_height=height)
        content = padded(box(True,14),22)
        dialog.set_child(content)
        content.append(label(title,"title"))
        return dialog,content

    def alert(self,title,detail):
        dialog,content = self.dialog(title)
        content.append(label(detail,wrap=True))
        content.append(button("Close",dialog.close))
        dialog.present()

    def guard_draft(self,action):
        if self.busy:
            return
        if (not self.draft or not self.draft.dirty) and not self.macro_invalid:
            action(); return
        dialog,content = self.dialog("Keep your draft?")
        content.append(label("Switching keyboards discards your pending changes. Export your draft first if you want to keep it.",wrap=True))
        actions=box(); actions.append(button("Keep editing",dialog.close))
        actions.append(button("Discard and continue",lambda:(dialog.close(),action())))
        content.append(actions); dialog.present()

    def connect_dialog(self):
        self.guard_draft(self._connect_dialog)

    def _connect_dialog(self):
        dialog,content = self.dialog("Connect a keyboard",720,460)
        content.append(label("Only QMK raw HID interfaces are listed. Connecting reads the configuration and saves a backup; it does not change your keyboard.","muted",True))
        rows = box(True,8); content.append(scroll(rows))
        info = label("Looking for keyboards…","muted"); rows.append(info)
        def found(devices):
            clear(rows)
            if not devices:
                rows.append(label("No VIA/Vial interfaces found. Connect by USB and use firmware with VIA or Vial enabled.",wrap=True))
            for device in devices:
                row=box(css="card"); title=box(True,4); title.set_hexpand(True)
                title.append(label(device.product or "QMK keyboard","title"))
                title.append(label(f"{device.vendor_id:04X}:{device.product_id:04X} · {device.path}","small"))
                row.append(title)
                row.append(button("Connect",lambda d=device:(dialog.close(),self.connect_device(d))))
                rows.append(row)
        from .hid import enumerate_devices
        self.run_worker(enumerate_devices,found)
        content.append(button("Close",dialog.close)); dialog.present()

    def connect_device(self,device,definition=None):
        self.session = None; self.draft = None; self.definition = None
        self.macro_edits.clear(); self.macro_invalid.clear()
        self.device_label.set_text("Reading keyboard…")
        self.device_detail.set_text("No configuration changes are being made")
        self.render_page()
        from .hid import HidTransport
        from .protocol import Protocol
        def probe():
            self.close_transport()
            transport=HidTransport(device)
            protocol=Protocol(transport,device.as_dict())
            try:
                details=protocol.probe()
                raw=protocol.read_vial_definition()
                return transport,protocol,raw,details
            except Exception:
                transport.close(); raise
        def ready(result):
            transport,protocol,raw,details=result
            self.transport=transport
            if raw:
                self.begin_session(protocol,load_definition(raw,vial=True),device)
            elif definition:
                self.begin_session(protocol,definition,device)
            else:
                matches=self.catalog.find(device.vendor_id,device.product_id)
                if len(matches)==1 and device.vendor_id not in (0xFEED,0):
                    self.begin_session(protocol,matches[0],device)
                else:
                    self.choose_definition(protocol,device,matches)
        self.run_worker(probe,ready)

    def choose_definition(self,protocol,device,matches):
        dialog,content=self.dialog("Choose the exact keyboard",700,540)
        content.append(label("This USB ID is shared or has no unique definition. Choose the exact hardware revision; a similar-looking layout is not enough.","warning",True))
        rows=box(True,8); content.append(scroll(rows))
        for definition in matches:
            rows.append(button(definition.name,lambda d=definition:(dialog.close(),self.begin_session(protocol,d,device))))
        def imported(path):
            try:
                definition=self.catalog.add_file(path,persist=True)
                if (definition.vendor_id,definition.product_id)!=(device.vendor_id,device.product_id):
                    raise ValueError("The definition's USB IDs do not match this keyboard.")
                dialog.close(); self.begin_session(protocol,definition,device)
            except Exception as error:
                self.alert("Definition rejected",str(error))
        content.append(button("Import manufacturer definition…",lambda:self.pick_file("Keyboard definition",False,imported)))
        content.append(button("Cancel",lambda:(dialog.close(),self.close_transport())))
        dialog.present()

    def begin_session(self,protocol,definition,device):
        from .safety import SafetySession
        def connect():
            return SafetySession(protocol,definition.raw)
        def ready(session):
            self.session=session; self.definition=definition; self.draft=Draft(session.baseline)
            self.demo=False; self.offline=False; self.layer=0; self.selected=None
            self.device_label.set_text(definition.name)
            self.device_detail.set_text(f"{device.vendor_id:04X}:{device.product_id:04X} · {len(session.baseline['layers'])} layers · backed up")
            self.render_page()
        self.run_worker(connect,ready)

    def close_transport(self):
        if self.transport:
            self.transport.close(); self.transport=None

    def library_dialog(self):
        dialog,content=self.dialog("Keyboard library",760,620)
        content.append(label("Bundled VIA definitions work offline. Vial reads its definition from the keyboard.","muted",True))
        search=Gtk.SearchEntry(placeholder_text="Search a keyboard or USB ID…"); content.append(search)
        rows=box(True,6); content.append(scroll(rows))
        def fill(query=""):
            clear(rows)
            found=0
            for definition in self.catalog.search(query, limit=100):
                description=f"{definition.name} {definition.vendor_id:04X}:{definition.product_id:04X}"
                if query.lower() not in description.lower(): continue
                rows.append(label(description,wrap=True)); found+=1
                if found>=100: break
            if found==100: rows.append(label("Keep typing to narrow the first 100 results.","muted"))
        search.connect("search-changed",lambda e:fill(e.get_text())); fill()
        def imported(path):
            try:
                definition=self.catalog.add_file(path,persist=True)
                self.alert("Definition saved",f"{definition.name} will be available the next time you connect.")
                fill(search.get_text())
            except Exception as error:self.alert("Definition rejected",str(error))
        content.append(button("Add manufacturer definition…",lambda:self.pick_file("Keyboard definition",False,imported)))
        content.append(button("Close",dialog.close)); dialog.present()

    def run_worker(self,work,done,on_error=None):
        if self.busy:
            return
        self.busy=True; self.update_footer()
        future=self.executor.submit(work)
        def completed():
            self.busy=False
            try:
                result=future.result(); done(result)
            except Exception as error:
                if self.session and self.session.protocol._poisoned: self.session.failed=True
                if on_error: on_error(error)
                self.alert("Operation stopped",f"{error}\n\nNo further writes will be attempted. If an apply was interrupted, reconnect and compare with your backup.")
                self.update_footer(str(error))
            else:self.update_footer()
            return False
        future.add_done_callback(lambda _f:GLib.idle_add(completed))

    def pick_file(self,title,save,callback,suggested="keymap.omakeeb.json"):
        dialog=Gtk.FileChooserNative.new(title,self,Gtk.FileChooserAction.SAVE if save else Gtk.FileChooserAction.OPEN,"Save" if save else "Open","Cancel")
        if save: dialog.set_current_name(suggested)
        file_filter=Gtk.FileFilter(); file_filter.set_name("Keyboard files (*.json, *.vil)"); file_filter.add_pattern("*.json"); file_filter.add_pattern("*.vil"); dialog.add_filter(file_filter)
        def response(native,result):
            if result==Gtk.ResponseType.ACCEPT:
                file=native.get_file()
                if file and file.get_path(): callback(Path(file.get_path()))
            native.destroy()
        dialog.connect("response",response); dialog.show()

    def file_dialog(self,save):
        if not self.draft:return
        self.pick_file("Import keymap into draft",False,self.import_path)

    def open_profile_dialog(self):
        def load(path):
            from .keymap_files import load_profile_bundle
            from .protocol import definition_hash
            try:
                data,raw=load_profile_bundle(path)
                if raw:
                    if definition_hash(raw)!=data["definition_sha256"]: raise ValueError("Profile definition checksum does not match.")
                    definition=load_definition(raw,vial=data.get("vial_protocol") is not None)
                else:
                    candidates=self.catalog.find(data["identity"]["vendor_id"],data["identity"]["product_id"])
                    matching=[d for d in candidates if definition_hash(d.raw)==data["definition_sha256"]]
                    if len(matching)!=1: raise ValueError("Add this keyboard’s exact definition to the library before opening this profile.")
                    definition=matching[0]
                self.close_transport(); self.session=None; self.definition=definition; self.draft=Draft(data)
                self.offline=True
                self.macro_edits.clear(); self.macro_invalid.clear()
                self.demo=True; self.layer=0; self.selected=None
                self.device_label.set_text(definition.name)
                self.device_detail.set_text("Offline profile · export to save changes · no device attached")
                self.render_page()
            except Exception as error:self.alert("Cannot open profile",str(error))
        self.guard_draft(lambda:self.pick_file("Open saved profile",False,load))

    def import_path(self,path):
        from .keymap_files import import_keymap
        try:
            data=import_keymap(path,self.draft.baseline)
            self.draft.replace(data); self.macro_edits.clear(); self.macro_invalid.clear(); self.render_page()
            self.update_footer("Keymap imported into draft. Review before applying.")
        except Exception as error:self.alert("Keymap rejected",str(error))

    def export_dialog(self):
        if not self.draft or self.macro_invalid:return
        dialog,content=self.dialog("Export keymap")
        content.append(label("Omakeeb keeps the complete configuration and layout. VIA carries keys, macros, and encoders. Vial also carries advanced mappings. Use Omakeeb format to preserve lighting.",wrap=True))
        for title,format,suffix in [("Omakeeb backup · complete","omakeeb","omakeeb.json"),("VIA keymap","via","json"),("Vial keymap","vial","vil")]:
            def choose(f=format,s=suffix):
                dialog.close()
                self.pick_file("Export keymap",True,lambda p:self.export_path(p,f),f"keymap.{s}")
            content.append(button(title,choose))
        dialog.present()

    def export_path(self,path,format):
        from .keymap_files import export_keymap
        try:
            export_keymap(path,self.draft.current,format=format,name=self.definition.name,definition=self.definition.raw)
            if self.offline and format=="omakeeb": self.draft=Draft(self.draft.current)
            self.update_footer(f"Exported {path.name}")
        except Exception as error:self.alert("Export stopped",str(error))

    def undo(self):
        if self.draft and not self.busy:
            self.draft.undo(); self.macro_edits.clear(); self.macro_invalid.clear(); self.render_page()

    def discard(self):
        if self.draft and not self.busy:
            self.draft.discard(); self.macro_edits.clear(); self.macro_invalid.clear(); self.render_page()

    def review(self):
        if not self.draft or not self.draft.dirty or self.macro_invalid:return
        if self.offline:
            self.export_dialog(); return
        from .safety import validate_staged
        try:
            validate_staged(self.draft.baseline,self.draft.current,self.session.protocol.lighting_specs if self.session else self.demo_lighting_specs())
        except Exception as error:
            self.alert("Changes need attention",str(error)); return
        dialog,content=self.dialog("Review changes",760,540)
        content.append(label("Demo changes stay on this computer." if self.demo else "Omakeeb will re-read the keyboard, verify it still matches your baseline, save a backup, and verify each change after writing.","muted",True))
        rows=box(True,8)
        for change in self.draft.changes():rows.append(label(change,"mono",True))
        content.append(scroll(rows))
        content.append(label("Keep the keyboard connected while applying. A failed verification stops the operation; reconnect before any further writes.","warning",True))
        actions=box(); actions.append(button("Keep editing",dialog.close))
        if self.session and self.draft.current.get("vial_protocol") is not None:
            actions.append(button("Unlock keyboard…",lambda:(dialog.close(),self.unlock_dialog())))
        def apply():
            dialog.close()
            if self.demo:
                self.draft=Draft(self.draft.current); self.render_page(); self.update_footer("Demo changes accepted · no hardware accessed"); return
            self.session.staged=deepcopy(self.draft.current)
            def done(_result):
                self.draft=Draft(self.session.baseline); self.macro_edits.clear(); self.macro_invalid.clear(); self.render_page()
                notes=getattr(self.session,"normalizations",[])
                self.update_footer("Changes applied and verified · backup saved"+(" · "+"; ".join(notes) if notes else ""))
            self.run_worker(self.session.apply,done)
        actions.append(button("Apply to demo" if self.demo else "Apply to keyboard",apply,"primary")); content.append(actions)
        dialog.present()

    def on_close(self,*_):
        if self.busy:
            self.alert("Operation in progress","Wait for the keyboard operation to finish before closing."); return True
        if self.draft and (self.draft.dirty or self.macro_invalid):
            dialog,content=self.dialog("Close with pending changes?")
            content.append(label("Your draft has not been applied. Export it before closing to keep a copy.",wrap=True))
            content.append(button("Keep editing",dialog.close))
            def close():
                dialog.close(); self.draft=None; self.close()
            content.append(button("Discard draft and close",close)); dialog.present(); return True
        self.matrix_testing=False
        if self.theme_timer:GLib.source_remove(self.theme_timer)
        self.close_transport(); self.executor.shutdown(wait=False,cancel_futures=True)
        return False


class Application(Gtk.Application):
    def __init__(self,demo=False,non_unique=False):
        super().__init__(application_id="org.omarchy.Omakeeb",flags=Gio.ApplicationFlags.NON_UNIQUE if non_unique else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.demo=demo
    def do_activate(self):
        window=self.get_active_window() or Window(self,self.demo)
        window.present()


def main():
    parser=argparse.ArgumentParser(description="Omakeeb — native QMK keyboard management")
    parser.add_argument("--demo",action="store_true",help="Explore without opening any keyboard")
    parser.add_argument("--version",action="version",version=__version__)
    args=parser.parse_args()
    return Application(args.demo).run([sys.argv[0]])
