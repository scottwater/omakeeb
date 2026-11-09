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
from omakeeb.app import Application, Window, box
from omakeeb.editors import lighting_editor
from omakeeb import macros
from omakeeb.compose import ComposeEntry
from omakeeb.keycodes import parse, macro_code
from omakeeb.keymap_files import load_profile_bundle
from omakeeb.model import Draft
import tempfile
from types import SimpleNamespace

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
    vialrgb={'underglow_brightness':[180],'vialrgb_mode':[1,0,128,10,255,200]}
    live=SimpleNamespace(session=SimpleNamespace(protocol=SimpleNamespace(lighting_specs={'vialrgb_mode':{'effects':[0,1,2],'brightness_max':200,'components':['Effect low byte','Effect high byte','Speed','Hue','Saturation','Brightness']}})),draft=Draft({'lighting':{'vialrgb_mode':vialrgb['vialrgb_mode']}}))
    parent=box(True); lighting_editor(live,parent)
    assert [d.get_selected() for d in descendants(parent,Gtk.DropDown)]==[1]
    # Offline profiles have no firmware effect list: Vial RGB is read-only, other lighting stays editable.
    window.session=None; window.offline=True; window.draft=Draft({**window.draft.current,'lighting':vialrgb})
    window.navigate('Lighting')
    assert not descendants(window.body,Gtk.DropDown)
    assert any(l.get_label().startswith('Effect 1 · Speed 128') for l in descendants(window.body,Gtk.Label))
    scales=descendants(window.body,Gtk.Scale)
    assert len(scales)==1
    scales[0].set_value(90)
    assert window.draft.current['lighting']=={'underglow_brightness':[90],'vialrgb_mode':[1,0,128,10,255,200]}
    # Profiles may carry any saved payload length; undecodable ones show raw bytes.
    for payload,text in (([],'Saved bytes: none'),([1,0,128],'Saved bytes: 1 0 128'),([1,0,128,10,255,200,7],'Saved bytes: 1 0 128 10 255 200 7')):
        window.draft=Draft({**window.draft.current,'lighting':{'underglow_brightness':[180],'vialrgb_mode':payload}})
        window.navigate('Lighting')
        assert not descendants(window.body,Gtk.DropDown)
        assert text in [l.get_label() for l in descendants(window.body,Gtk.Label)]
        descendants(window.body,Gtk.Scale)[0].set_value(90)
        assert window.draft.current['lighting']=={'underglow_brightness':[90],'vialrgb_mode':payload}
    print('Exercised macros, validation, lighting, offline Vial RGB, QMK settings, tap dance, self-contained export, and reviewed demo apply.')

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
            window.compose_entries=[ComposeEntry(('m','s'),'😄','smile')]; window.compose_key='{KC_LSFT,KC_CAPS}'
            window.palette_category.set_selected(2)
            assert window.shortcut_rows.get_first_child() and not window.palette.get_first_child()
            window.assign_compose('{KC_LSFT,KC_CAPS}ms')
            data=window.draft.current
            assert macros.decode(bytes.fromhex(data['macro_buffer']),data['macro_count'],protocol=window.macro_protocol(),vial_protocol=data.get('vial_protocol'))[0]=='{+KC_LSFT}{+KC_CAPS}{-KC_CAPS}{-KC_LSFT}ms'
            window.selected=(0,2); window.assign_compose('{KC_LSFT,KC_CAPS}ms')
            assert window.draft.current['layers'][0][0][2]==macro_code(0,data)  # reused, not a second slot
            window.undo()
            assert data['layers'][0][0][1]==macro_code(0,data)
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
