#!/usr/bin/env python3
"""Exercise native pages and draft operations without opening a HID device.

Optionally render the app's own widget tree to a PNG (no desktop capture).
Run from the checkout: python scripts/smoke_ui.py [output-directory]
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Gdk','4.0')
gi.require_version('Graphene','1.0')
from gi.repository import Gtk, Gdk, GLib, Graphene
from omakeeb.app import Application, Window
from omakeeb.keycodes import parse
from omakeeb.keymap_files import load_profile_bundle
from omakeeb.model import Draft
import tempfile

app = Application(True,non_unique=True)
app.register(None)
window = Window(app, True)
window.present()
failures = []
old_hook = sys.excepthook
sys.excepthook = lambda typ,value,tb: (failures.append(str(value)),old_hook(typ,value,tb))
output = Path(sys.argv[1]) if len(sys.argv)>1 else None
pages = iter(['Keymap','Macros','Lighting','Encoders','Advanced','Test keys','Backups'])
loop = GLib.MainLoop()

def snapshot(name):
    if not output: return
    output.mkdir(parents=True,exist_ok=True)
    widget = Gtk.WidgetPaintable.new(window)
    drawing = Gtk.Snapshot()
    widget.snapshot(drawing, window.get_width(), window.get_height())
    node = drawing.to_node()
    rect = Graphene.Rect().init(0,0,window.get_width(),window.get_height())
    texture = window.get_renderer().render_texture(node,rect)
    texture.save_to_png(str(output / (name.replace(' ','-').lower()+'.png')))

def descendants(widget, kind):
    result=[]
    if isinstance(widget,kind):result.append(widget)
    child=widget.get_first_child()
    while child:
        result.extend(descendants(child,kind));child=child.get_next_sibling()
    return result

def exercise_editors():
    window.navigate('Macros')
    text=descendants(window.body,Gtk.TextView)[0]
    text.get_buffer().set_text('Hello{KC_ENT}')
    assert window.draft.dirty and not window.macro_invalid
    window.navigate('Keymap');window.navigate('Macros')
    text=descendants(window.body,Gtk.TextView)[0]
    assert text.get_buffer().get_text(text.get_buffer().get_start_iter(),text.get_buffer().get_end_iter(),True)=='Hello{KC_ENT}'
    text.get_buffer().set_text('{')
    assert window.macro_invalid and not window.export_button.get_sensitive()
    text.get_buffer().set_text('Hello{KC_ENT}')
    assert not window.macro_invalid and window.export_button.get_sensitive()
    window.navigate('Lighting')
    scale=descendants(window.body,Gtk.Scale)[0]
    scale.set_value(123)
    assert window.draft.current['lighting']['underglow_brightness']==[123]
    window.navigate('Advanced')
    spin=descendants(window.body,Gtk.SpinButton)[0]
    spin.set_value(250)
    assert window.draft.current['settings']['7']=='fa00'
    window.dynamic_dialog('tap_dance')
    dialogs=[w for w in Gtk.Window.list_toplevels() if w is not window and w.get_title()=='Tap Dance']
    assert len(dialogs)==1
    dialog=dialogs[0]
    descendants(dialog,Gtk.Entry)[0].set_text('KC_X')
    [b for b in descendants(dialog,Gtk.Button) if b.get_label()=='Stage entry'][0].emit('clicked')
    assert window.draft.current['dynamic']['tap_dance'][0][0]==parse('KC_X')
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'roundtrip.omakeeb.json'
        window.export_path(path,'omakeeb')
        snapshot,definition=load_profile_bundle(path)
        assert snapshot==window.draft.current and definition==window.definition.raw
    window.review()
    dialogs=[w for w in Gtk.Window.list_toplevels() if w is not window and w.get_title()=='Review changes']
    assert len(dialogs)==1
    [b for b in descendants(dialogs[0],Gtk.Button) if b.get_label()=='Apply to demo'][0].emit('clicked')
    assert not window.draft.dirty
    print('Exercised macros, validation, lighting, QMK settings, tap dance, self-contained export, and reviewed demo apply.')

def step():
    try:
        page = next(pages)
    except StopIteration:
        try:
            window.navigate('Keymap')
            window.selected=(0,1)
            window.assign_code(parse('LGUI(KC_E)'))
            assert window.draft.dirty
            assert 'LGUI' in window.draft.changes()[0]
            window.undo()
            assert not window.draft.dirty
            window.palette_category.set_selected(1)
            assert window.palette_group==1 and window.shortcut_rows.get_first_child() and not window.palette.get_first_child()
            window.select_layer(1)
            assert window.palette_category.get_selected_item().get_string()=='Omarchy'
            window.select_layer(0)
            exercise_editors()
        except Exception as error:
            failures.append(str(error))
        window.draft=None
        window.close()
        loop.quit()
        return False
    try:
        window.navigate(page)
        GLib.timeout_add(250, lambda: (snapshot(page),False)[1])
        print('Opened',page)
    except Exception as error:
        failures.append(f'{page}: {error}')
    return True
GLib.timeout_add(600,step)
loop.run()
if failures: raise SystemExit('\n'.join(failures))
print('Native UI smoke checks passed; no HID device was opened.')
