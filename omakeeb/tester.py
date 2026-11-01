"""XKB hardware codes (evdev + 8) to USB HID basic keycodes, independent of layout."""
# GTK uses XKB keycodes on both X11 and Wayland. No input devices are opened here.
_EVDEV = {
    1:41, 14:42, 15:43, 28:40, 29:224, 42:225, 54:229, 56:226, 57:44, 58:57,
    97:228, 100:230, 125:227, 126:231, 127:101,
    12:45,13:46,26:47,27:48,39:51,40:52,41:53,43:49,51:54,52:55,53:56,
    102:74,103:82,104:75,105:80,106:79,107:77,108:81,109:78,110:73,111:76,
    69:83,70:71,96:88,98:84,55:85,74:86,78:87,83:99,99:70,119:72,
}
for start, letters in [(16,"QWERTYUIOP"),(30,"ASDFGHJKL"),(44,"ZXCVBNM")]:
    for index, letter in enumerate(letters): _EVDEV[start+index]=ord(letter)-ord("A")+4
for index in range(10): _EVDEV[2+index]=30+index
for index in range(10): _EVDEV[59+index]=58+index
_EVDEV.update({87:68,88:69})
for index in range(12): _EVDEV[183+index]=104+index
for index,evdev in enumerate([79,80,81,75,76,77,71,72,73,82]): _EVDEV[evdev]=89+index

def qmk_for_keycode(code:int)->int|None:
    return _EVDEV.get(code-8)
