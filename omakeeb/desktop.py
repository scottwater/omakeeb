"""Read-only Omarchy integration, independent of GTK and keyboard transports."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib


DEFAULT_COLORS = {
    "background": "#1a1b26", "foreground": "#c0caf5", "accent": "#7aa2f7",
    "selection": "#292e42", "muted": "#565f89", "dark_background": "#13141c",
    "lighter_background": "#24283b", "dark_foreground": "#828bb8",
    "red": "#f7768e", "green": "#9ece6a", "yellow": "#e0af68",
}
_HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")


@dataclass(frozen=True)
class Theme:
    name: str
    colors: dict[str, str]
    source: Path | None = None
    mode: str = "dark"


def load_theme(home: Path | None = None) -> Theme:
    """Read current state directory, with the pre-migration config path fallback.

    Call again after a theme switch; directory replacement/symlink changes are
    followed each time. Theme files supply data only, never executable CSS.
    """
    home = Path.home() if home is None else Path(home)
    candidates = [
        home / ".local/state/omarchy/current/theme",
        home / ".config/omarchy/current/theme",
    ]
    for directory in candidates:
        source = directory / "colors.toml"
        try:
            if source.stat().st_size > 65536:
                continue
            data = tomllib.loads(source.read_text())
        except (OSError, ValueError, UnicodeError):
            continue
        colors = dict(DEFAULT_COLORS)
        aliases = {"background": ("bg", "color0"), "foreground": ("fg", "color7"),
                   "accent": ("blue", "color4"), "selection": ("color8",),
                   "dark_background": ("dark_bg",), "lighter_background": ("lighter_bg",),
                   "dark_foreground": ("dark_fg", "muted"),
                   "red": ("color1",), "green": ("color2",), "yellow": ("color3",)}
        for key in colors:
            for candidate in (key, *aliases.get(key, ())):
                value = data.get(candidate)
                if isinstance(value, str) and _HEX.fullmatch(value):
                    colors[key] = value.lower()
                    break
        mode = data.get("mode", data.get("theme_type"))
        if mode not in ("dark", "light"):
            rgb = colors["background"][1:]
            mode = "light" if sum(int(rgb[i:i+2], 16) for i in (0, 2, 4)) > 382 else "dark"
        # Derive missing surface colors from the actual palette, especially on
        # light themes which must not inherit dark default card backgrounds.
        for key, ratio in (("dark_background", 0.04), ("lighter_background", 0.09), ("selection", 0.16)):
            if not any(_HEX.fullmatch(str(data.get(alias, ""))) for alias in (key, *aliases.get(key, ()))):
                colors[key] = _mix(colors["background"], colors["foreground"], ratio)
        if not any(_HEX.fullmatch(str(data.get(alias, ""))) for alias in ("dark_foreground", "dark_fg", "muted")):
            colors["dark_foreground"] = _mix(colors["background"], colors["foreground"], 0.65)
        try:
            name = (directory.parent / "theme.name").read_text().strip()[:120]
        except OSError:
            name = directory.resolve().name
        return Theme(name or "Omarchy", colors, source, mode)
    return Theme("Omarchy", dict(DEFAULT_COLORS))


def _mix(a: str, b: str, amount: float) -> str:
    return "#" + "".join(f"{round(int(a[i:i+2], 16)*(1-amount) + int(b[i:i+2],16)*amount):02x}" for i in (1, 3, 5))


def theme_css(theme: Theme) -> str:
    """GTK named colors only; class styling belongs to the UI."""
    return "\n".join(f"@define-color omakeeb_{key} {value};" for key, value in theme.colors.items()
                     if key in DEFAULT_COLORS and _HEX.fullmatch(value))


@dataclass(frozen=True)
class Shortcut:
    label: str
    category: str
    modifiers: tuple[str, ...]
    key: str
    keycode: int | None
    source: str = "Omarchy defaults"

    @property
    def chord(self) -> str:
        return " + ".join((*self.modifiers, self.key))


# XKB/Hypr names mapped to USB HID keyboard-page usages. No keyboard layout is
# changed. Non-US layout users should check symbol bindings in the tester.
_KEYS = {chr(65+i): 4+i for i in range(26)}
_KEYS.update({str(i): 29+i for i in range(1, 10)})
_KEYS.update({"0": 39, "RETURN": 40, "ENTER": 40, "ESCAPE": 41, "BACKSPACE": 42,
    "TAB": 43, "SPACE": 44, "MINUS": 45, "EQUAL": 46, "BRACKETLEFT": 47,
    "BRACKETRIGHT": 48, "BACKSLASH": 49, "SEMICOLON": 51, "APOSTROPHE": 52,
    "GRAVE": 53, "COMMA": 54, "PERIOD": 55, "SLASH": 56, "CAPS_LOCK": 57,
    "PRINT": 70, "SCROLL_LOCK": 71, "PAUSE": 72, "INSERT": 73, "HOME": 74,
    "PRIOR": 75, "PAGE_UP": 75, "DELETE": 76, "END": 77, "NEXT": 78,
    "PAGE_DOWN": 78, "RIGHT": 79, "LEFT": 80, "DOWN": 81, "UP": 82,
    "XF86TOOLS": 104,
    # QMK's extended basic consumer codes are stable in VIA 9 through 13.
    "XF86AUDIORAISEVOLUME": 169, "XF86AUDIOLOWERVOLUME": 170,
    "XF86AUDIOMUTE": 168, "XF86AUDIOPLAY": 174, "XF86AUDIOPAUSE": 174,
    "XF86AUDIONEXT": 171, "XF86AUDIOPREV": 172, "XF86AUDIOSTOP": 173,
    "XF86MONBRIGHTNESSUP": 189, "XF86MONBRIGHTNESSDOWN": 190})
_KEYS.update({f"F{i}": 57+i if i <= 12 else 91+i for i in range(1, 25)})
# Linux evdev codes + 8 (the codes reported by Hyprland) to USB, for common
# physical keys used by stock workspace/group bindings.
_XKB_CODES = {i+9: _KEYS[str(i)] for i in range(1, 10)}
_XKB_CODES.update({19: 39, 20: 45, 21: 46, 36: 40, 65: 44, 201: 114})
_MOD_BITS = {"CTRL": 1, "SHIFT": 2, "ALT": 4, "SUPER": 8}


def shortcut_keycode(modifiers: tuple[str, ...], key: str, physical_code: int = 0) -> int | None:
    base = _XKB_CODES.get(physical_code) if physical_code else _KEYS.get(key.upper())
    if base is None or any(mod not in _MOD_BITS for mod in modifiers):
        return None
    mask = 0
    for mod in modifiers:
        mask |= _MOD_BITS[mod]
    return (mask << 8) | base


def _category(label: str) -> str:
    text = label.lower()
    if any(x in text for x in ("workspace", "scratchpad", "monitor")):
        return "Workspaces"
    if any(x in text for x in ("window", "floating", "full screen", "group")):
        return "Windows"
    if any(x in text for x in ("volume", "mute", "media", "brightness", "track")):
        return "Media"
    if any(x in text for x in ("menu", "capture", "screenshot", "theme", "lock", "keybindings", "dictation")):
        return "System"
    return "Applications"


def shortcuts_from_binds(binds: list[dict]) -> list[Shortcut]:
    """Convert live binding metadata without running any dispatcher or command.

    Mouse bindings, nondefault submaps, lock modifiers, and unsupported keysyms
    remain unmappable or excluded instead of silently producing a wrong chord.
    """
    result = []
    seen = set()
    for bind in binds:
        if not isinstance(bind, dict) or bind.get("mouse") or bind.get("submap"):
            continue
        key = bind.get("key", "")
        mask = bind.get("modmask", 0)
        if not isinstance(key, str) or type(mask) is not int:
            continue
        if key.lower().startswith("mouse") or bind.get("catch_all"):
            continue
        mods = tuple(name for bit, name in ((64, "SUPER"), (4, "CTRL"), (8, "ALT"), (1, "SHIFT")) if mask & bit)
        label = bind.get("description")
        if not isinstance(label, str) or not label:
            # Private command text is deliberately not used as display data.
            continue
        physical = bind.get("keycode", 0)
        physical = physical if type(physical) is int else 0
        code = shortcut_keycode(mods, key, physical)
        if mask & ~(64 | 4 | 8 | 1) or bind.get("longPress"):
            code = None
        display_key = key or f"code:{physical}"
        identity = (label, mods, display_key)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(Shortcut(label[:200], _category(label), mods, display_key, code, "Live Hyprland binding"))
    return sorted(result, key=lambda s: (s.category, s.label.casefold(), s.chord))


def default_shortcuts() -> list[Shortcut]:
    definitions = [
        ("Terminal", "SUPER", "RETURN"), ("Browser", "SUPER SHIFT", "B"),
        ("File manager", "SUPER SHIFT", "F"), ("Editor", "SUPER SHIFT", "N"),
        ("Omarchy menu", "SUPER", "SPACE"), ("Apps menu", "SUPER ALT", "SPACE"),
        ("Keybindings", "SUPER", "K"), ("System menu", "SUPER", "ESCAPE"),
        ("Capture menu", "SUPER CTRL", "C"), ("Screenshot", "", "PRINT"),
        ("Theme menu", "SUPER CTRL SHIFT", "SPACE"), ("Close window", "SUPER", "W"),
        ("Toggle window floating/tiling", "SUPER", "T"), ("Full screen", "SUPER", "F"),
        ("Toggle window grouping", "SUPER", "G"), ("Toggle scratchpad", "SUPER", "S"),
        ("Next workspace", "SUPER", "TAB"), ("Previous workspace", "SUPER SHIFT", "TAB"),
    ]
    for number in range(1, 11):
        definitions.append((f"Switch to workspace {number}", "SUPER", str(number % 10)))
        definitions.append((f"Move window to workspace {number}", "SUPER SHIFT", str(number % 10)))
    for direction in ("LEFT", "RIGHT", "UP", "DOWN"):
        definitions.append((f"Focus {direction.lower()} window", "SUPER", direction))
    return [Shortcut(label, _category(label), tuple(mods.split()), key, shortcut_keycode(tuple(mods.split()), key))
            for label, mods, key in definitions]


def discover_shortcuts() -> list[Shortcut]:
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return default_shortcuts()
    try:
        process = subprocess.run(["hyprctl", "-j", "binds"], capture_output=True, text=True, timeout=2, check=True)
        if len(process.stdout) > 2_000_000:
            return default_shortcuts()
        binds = json.loads(process.stdout)
        if isinstance(binds, list):
            return shortcuts_from_binds(binds)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return default_shortcuts()
