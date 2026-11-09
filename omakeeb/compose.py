"""Read-only XCompose integration: list Compose sequences as macro expressions.

A macro taps the Compose key, then types the sequence's keys. Only sequences
that start with <Multi_key> and type plain ASCII keys can become macros.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess

# XKB compose options mapped to the macro action that produces Multi_key.
COMPOSE_KEYS = {
    "omarchy:shift_caps_compose": "{KC_LSFT,KC_CAPS}",
    "compose:caps": "{KC_CAPS}", "compose:ralt": "{KC_RALT}",
    "compose:lwin": "{KC_LGUI}", "compose:rwin": "{KC_RGUI}",
    "compose:menu": "{KC_APP}", "compose:lctrl": "{KC_LCTL}",
    "compose:rctrl": "{KC_RCTL}", "compose:prsc": "{KC_PSCR}",
}
OMARCHY_OPTIONS = "omarchy:shift_caps_compose"
_ASCII = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#", "dollar": "$",
    "percent": "%", "ampersand": "&", "apostrophe": "'", "parenleft": "(",
    "parenright": ")", "asterisk": "*", "plus": "+", "comma": ",", "minus": "-",
    "period": ".", "slash": "/", "colon": ":", "semicolon": ";", "less": "<",
    "equal": "=", "greater": ">", "question": "?", "at": "@", "bracketleft": "[",
    "backslash": "\\", "bracketright": "]", "asciicircum": "^", "underscore": "_",
    "grave": "`", "braceleft": "{", "bar": "|", "braceright": "}", "asciitilde": "~",
}
_LINE = re.compile(r'\s*((?:<[^>]+>\s*)+):\s*"((?:[^"\\]|\\.)*)"[^#]*(?:#\s*(.*))?$')
_INCLUDE = re.compile(r'\s*include\s+"([^"]*)"')


@dataclass(frozen=True)
class ComposeEntry:
    keys: tuple[str, ...]  # keysyms after <Multi_key>
    output: str
    comment: str = ""

    @property
    def sequence(self) -> str:
        return " ".join(_ASCII.get(k, k) if k != "space" else "Space" for k in self.keys)

    def text(self) -> str | None:
        """ASCII the macro types after Compose, or None if a key cannot be typed."""
        chars = []
        for key in self.keys:
            char = _ASCII.get(key, key if len(key) == 1 and key.isascii() and key.isalnum() else None)
            if char is None:
                return None
            chars.append("\\" + char if char in "{}\\" else char)
        return "".join(chars)


def parse(path: Path, home: Path, entries: dict | None = None, seen: set | None = None) -> dict[tuple[str, ...], ComposeEntry]:
    """Later definitions win, as in libX11, including over earlier sequences that
    are a prefix of them (or that they prefix). %L/%S (system locale tables) are skipped."""
    entries = {} if entries is None else entries
    seen = set() if seen is None else seen
    try:
        path = path.resolve()
        if path in seen or len(seen) > 8 or path.stat().st_size > 1_000_000:
            return entries
        seen.add(path)
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return entries
    for line in lines:
        if include := _INCLUDE.match(line):
            target = include.group(1)
            if "%L" not in target and "%S" not in target:
                parse(Path(target.replace("%H", str(home))), home, entries, seen)
        elif (match := _LINE.match(line)):
            keys = tuple(re.findall(r"<([^>]+)>", match.group(1)))
            if len(keys) > 1 and keys[0] == "Multi_key":
                keys = keys[1:]
                # ponytail: O(n²) conflict scan; fine for hand-written files (%L tables are skipped).
                for other in [k for k in entries if k[:len(keys)] == keys or keys[:len(k)] == k]:
                    del entries[other]
                output = re.sub(r'\\(["\\])', r'\1', match.group(2))
                entries[keys] = ComposeEntry(keys, output, (match.group(3) or "").strip()[:120])
    return entries


def load_entries(home: Path | None = None, environ=os.environ) -> list[ComposeEntry]:
    home = Path.home() if home is None else Path(home)
    path = Path(environ["XCOMPOSEFILE"]) if environ.get("XCOMPOSEFILE") else home / ".XCompose"
    return list(parse(path, home).values())


def compose_key(kb_options: str | None) -> str | None:
    """Macro action for the configured Compose key; the last compose option wins."""
    found = None
    for option in (kb_options or "").split(","):
        found = COMPOSE_KEYS.get(option.strip(), found)
    return found


def live_kb_options() -> str:
    """Hyprland's kb_options, or Omarchy's default when Hyprland is not reachable."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        try:
            process = subprocess.run(["hyprctl", "-j", "getoption", "input:kb_options"],
                                     capture_output=True, text=True, timeout=2, check=True)
            value = json.loads(process.stdout).get("str")
            if isinstance(value, str):
                return value
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
            pass
    return OMARCHY_OPTIONS


def macro_expression(entry: ComposeEntry, key: str | None) -> str | None:
    text = entry.text()
    return None if key is None or text is None else key + text
