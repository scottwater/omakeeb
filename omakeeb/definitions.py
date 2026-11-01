"""Offline VIA/Vial keyboard definitions. Definitions describe geometry, never firmware.

The matrix coordinate printed on each KLE key is the only address used for edits.
Physical positions, layout alternatives, and encoder positions are not addresses.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import gzip
import hashlib
import json
import math
import os
import re
import tempfile

RESOURCE_DIR = Path(__file__).resolve().parent.parent / "resources"
MAX_DEFINITION_BYTES = 4 * 1024 * 1024


class DefinitionError(ValueError):
    pass


def _integer(value, name, low=0, high=65535):
    if isinstance(value, str):
        try:
            value = int(value, 16 if value.lower().startswith("0x") else 10)
        except ValueError:
            raise DefinitionError(f"{name} must be an integer") from None
    if type(value) is not int or not low <= value <= high:
        raise DefinitionError(f"{name} must be between {low} and {high}")
    return value


def _number(value, name, low=-100, high=100):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise DefinitionError(f"Invalid {name}")
    return float(value)


@dataclass(frozen=True)
class Key:
    row: int
    col: int
    x: float
    y: float
    w: float = 1
    h: float = 1
    label: str = ""
    rotation: float = 0
    rotation_x: float = 0
    rotation_y: float = 0
    encoder: int | None = None
    variant: tuple[int, int] | None = None
    decal: bool = False
    encoder_direction: int | None = None

    @property
    def address(self):
        return self.row, self.col


@dataclass
class Definition:
    id: str
    name: str
    vendor_id: int
    product_id: int
    rows: int
    cols: int
    keys: list[Key]
    raw: dict
    source: str = "imported"

    @property
    def encoder_count(self):
        return max((k.encoder + 1 for k in self.keys if k.encoder is not None), default=0)

    @property
    def layout_labels(self):
        return self.raw.get("layouts", {}).get("labels", [])

    def visible_keys(self, options: dict[int, int] | None = None):
        if type(options) is int:
            options = unpack_layout_options(self.layout_labels, options)
        options = options or {}
        # Alternative KLE groups are drawn beside/below the base layout in
        # source files; align each selected group with its option-zero anchor.
        anchors = {}
        for key in self.keys:
            if key.variant is not None and not key.decal:
                points = _corners(key)
                left, top = min(p[0] for p in points), min(p[1] for p in points)
                previous = anchors.get(key.variant, (left, top))
                anchors[key.variant] = min(previous[0], left), min(previous[1], top)
        visible = []
        for key in self.keys:
            if key.decal:
                continue
            if key.variant is not None:
                group, choice = key.variant
                if options.get(group, 0) != choice:
                    continue
                anchor = anchors.get((group, 0))
                current = anchors[key.variant]
                if anchor and choice != 0:
                    dx, dy = anchor[0] - current[0], anchor[1] - current[1]
                    key = replace(key, x=key.x + dx, y=key.y + dy,
                                  rotation_x=key.rotation_x + dx, rotation_y=key.rotation_y + dy)
            visible.append(key)
        return visible

    def to_dict(self):
        return json.loads(json.dumps(self.raw))


def _parse_kle(layout, rows, cols, *, vial=False):
    if not isinstance(layout, list) or not layout or len(layout) > 256:
        raise DefinitionError("layouts.keymap must contain KLE rows")
    result = []
    x = y = rx = ry = rotation = 0.0
    w = h = 1.0
    decal = False
    for line in layout:
        if isinstance(line, dict):  # KLE metadata
            continue
        if not isinstance(line, list):
            raise DefinitionError("Each KLE row must be an array")
        for part in line:
            if isinstance(part, dict):
                if "r" in part:
                    rotation = _number(part["r"], "rotation", -360, 360)
                if "rx" in part:
                    rx = _number(part["rx"], "rotation x")
                    x, y = rx, ry
                if "ry" in part:
                    ry = _number(part["ry"], "rotation y")
                    x, y = rx, ry
                x += _number(part.get("x", 0), "key x")
                y += _number(part.get("y", 0), "key y")
                w = _number(part.get("w", w), "key width", 0.05, 30)
                h = _number(part.get("h", h), "key height", 0.05, 30)
                decal = bool(part.get("d", decal))
                continue
            if not isinstance(part, str) or len(part) > 4096:
                raise DefinitionError("A KLE key label must be a short string")
            labels = part.split("\n")
            matrix = re.fullmatch(r"\s*(\d+)\s*,\s*(\d+)\s*", labels[0])
            encoder = next((int(m.group(1)) for s in labels if (m := re.fullmatch(r"e(\d+)", s.strip()))), None)
            encoder_direction = None
            vial_encoder = vial and any(label.strip() == "e" for label in labels[1:])
            variant = None
            if len(labels) > 3 and labels[3].strip():
                option = re.fullmatch(r"\s*(\d+)\s*,\s*(\d+)\s*", labels[3])
                if option:
                    variant = tuple(map(int, option.groups()))
            if vial_encoder:
                if not matrix:
                    raise DefinitionError("Vial encoder requires index,direction coordinates")
                encoder, encoder_direction = map(int, matrix.groups())
                if encoder_direction not in (0, 1):
                    raise DefinitionError("Vial encoder direction must be 0 or 1")
                row = col = -1
            elif matrix:
                row, col = map(int, matrix.groups())
                if not 0 <= row < rows or not 0 <= col < cols:
                    raise DefinitionError(f"Key {row},{col} is outside the declared {rows} × {cols} matrix")
            elif encoder is not None or decal:
                row = col = -1
            else:
                raise DefinitionError(f"Key has no matrix coordinate: {labels[0]!r}")
            if encoder is not None and encoder > 31:
                raise DefinitionError("Encoder index exceeds supported range")
            result.append(Key(row, col, x, y, w, h, labels[0], rotation, rx, ry, encoder, variant, decal, encoder_direction))
            if len(result) > 4096:
                raise DefinitionError("Too many keys in definition")
            x += w
            w = h = 1.0
            decal = False
        y += 1
        x = rx
    if not any(k.row >= 0 for k in result):
        raise DefinitionError("Definition contains no matrix keys")
    return result


def load_definition(raw: dict, *, id: str | None = None, source="imported", vial: bool | None = None) -> Definition:
    if not isinstance(raw, dict):
        raise DefinitionError("Definition must be a JSON object")
    name = raw.get("name", "Unnamed keyboard")
    if not isinstance(name, str) or not name.strip() or len(name) > 240:
        raise DefinitionError("Definition needs a valid name")
    # Vial device definitions omit USB IDs: the caller must bind them to the
    # enumerated device before use. 0 is intentionally not a wildcard.
    vid = _integer(raw.get("vendorId", 0), "vendorId")
    pid = _integer(raw.get("productId", 0), "productId")
    matrix = raw.get("matrix")
    if not isinstance(matrix, dict):
        raise DefinitionError("Definition needs matrix.rows and matrix.cols")
    rows = _integer(matrix.get("rows"), "matrix.rows", 1, 64)
    cols = _integer(matrix.get("cols"), "matrix.cols", 1, 64)
    if rows * cols > 2048:
        raise DefinitionError("Matrix exceeds supported size")
    layouts = raw.get("layouts")
    if not isinstance(layouts, dict):
        raise DefinitionError("Definition needs layouts.keymap")
    keys = _parse_kle(layouts.get("keymap"), rows, cols, vial=("vendorId" not in raw and "productId" not in raw) if vial is None else vial)
    try:
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DefinitionError("Definition is not finite JSON") from exc
    if len(canonical.encode()) > MAX_DEFINITION_BYTES:
        raise DefinitionError("Definition is too large")
    identity = id or hashlib.sha256(canonical.encode()).hexdigest()[:20]
    return Definition(identity, name.strip(), vid, pid, rows, cols, keys, json.loads(canonical), source)


def load_definition_file(path) -> Definition:
    path = Path(path)
    if path.stat().st_size > MAX_DEFINITION_BYTES:
        raise DefinitionError("Definition file exceeds 4 MiB")
    try:
        return load_definition(json.loads(path.read_text(encoding="utf-8-sig")), source=str(path))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise DefinitionError(f"Invalid definition JSON: {exc}") from exc


def custom_directory():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "omakeeb/definitions"


class Catalog:
    """Lazy catalog. Duplicate USB IDs remain explicit candidates, never guesses."""
    def __init__(self, path=None, custom_dir=None):
        self.path = Path(path) if path else RESOURCE_DIR / "definitions.json.gz"
        self.custom_dir = Path(custom_dir) if custom_dir else custom_directory()
        self._entries = {}
        self._cache = {}
        self.errors = []
        if self.path.exists():
            with gzip.open(self.path, "rt", encoding="utf-8") as stream:
                bundle = json.load(stream)
            self.metadata = bundle.get("metadata", {})
            self._entries = bundle["definitions"]
        else:
            self.metadata = {}
        if self.custom_dir.exists():
            for path in sorted(self.custom_dir.glob("*.json")):
                try:
                    definition = load_definition_file(path)
                    self._cache[definition.id] = definition
                    self._entries[definition.id] = definition.raw
                except (OSError, ValueError) as exc:
                    self.errors.append(f"{path.name}: {exc}")

    def __len__(self):
        return len(self._entries)

    def get(self, id):
        if id not in self._cache:
            self._cache[id] = load_definition(self._entries[id], id=id, source="VIA catalog")
        return self._cache[id]

    def all(self):
        return [self.get(id) for id in self._entries]

    def find(self, vendor_id, product_id):
        matches = []
        for id, entry in self._entries.items():
            if _integer(entry.get("vendorId", 0), "vendorId") == vendor_id and _integer(entry.get("productId", 0), "productId") == product_id:
                matches.append(self.get(id))
        return sorted(matches, key=lambda item: item.name.casefold())

    def search(self, query="", limit=100):
        query = query.casefold()
        selected = []
        for id, raw in self._entries.items():
            vid = _integer(raw.get("vendorId", 0), "vendorId")
            pid = _integer(raw.get("productId", 0), "productId")
            haystack = f"{raw.get('name', '')} {id} {vid:04x}:{pid:04x} {vid:04x} {pid:04x}".casefold()
            if query in haystack:
                selected.append(id)
        return [self.get(id) for id in sorted(selected, key=lambda id: self._entries[id].get("name", "").casefold())[:limit]]

    def add_file(self, path, persist=True):
        definition = load_definition_file(path)
        if persist:
            self.custom_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix=".definition-", dir=self.custom_dir)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(definition.raw, stream, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.custom_dir / f"{definition.id}.json")
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        self._entries[definition.id] = definition.raw
        self._cache[definition.id] = definition
        return definition


def demo_definition():
    """A compact offline sandbox, not the identity of a physical keyboard."""
    keymap = [
        [*[f"0,{col}" for col in range(13)], {"w": 2}, "0,13", {"x": .25}, "0,14"],
        [{"w": 1.5}, "1,0", *[f"1,{col}" for col in range(1,13)], {"w": 1.5}, "1,13", {"x": .25}, "1,14"],
        [{"w": 1.75}, "2,0", *[f"2,{col}" for col in range(1,12)], {"w": 2.25}, "2,12", {"x": .25}, "2,14"],
        [{"w": 2.25}, "3,0", *[f"3,{col}" for col in range(1,11)], {"w": 1.75}, "3,11", "3,12", {"x": .25}, "3,14"],
        [{"w": 1.25}, "4,0", {"w": 1.25}, "4,1", {"w": 1.25}, "4,2", {"w": 6.25}, "4,6", "4,9", "4,10", "4,11", "4,12", {"x": .25}, "4,13"],
    ]
    return load_definition({"name": "Omakeeb Studio 65", "vendorId": "0x0000", "productId": "0x0000", "matrix": {"rows": 5, "cols": 15}, "layouts": {"keymap": keymap}, "menus": ["qmk_rgb_matrix"]}, id="demo", source="demo")


def _corners(key):
    angle = math.radians(key.rotation)
    cosine, sine = math.cos(angle), math.sin(angle)
    return tuple((key.rotation_x + (x - key.rotation_x) * cosine - (y - key.rotation_y) * sine,
                  key.rotation_y + (x - key.rotation_x) * sine + (y - key.rotation_y) * cosine)
                 for x, y in ((key.x, key.y), (key.x + key.w, key.y),
                              (key.x + key.w, key.y + key.h), (key.x, key.y + key.h)))


def layout_choices(labels):
    """Return [(label, [choice names]), ...] for VIA/Vial layout controls."""
    if not isinstance(labels, list) or len(labels) > 32:
        raise DefinitionError("Invalid layout labels")
    result = []
    for item in labels:
        if isinstance(item, str) and item:
            result.append((item, ["Off", "On"]))
        elif isinstance(item, list) and 3 <= len(item) <= 33 and all(isinstance(value, str) and value for value in item):
            result.append((item[0], item[1:]))
        else:
            raise DefinitionError("Each layout label needs a name and at least two choices")
    if sum((len(choices) - 1).bit_length() for _, choices in result) > 32:
        raise DefinitionError("Layout options exceed the 32-bit firmware field")
    return result


def unpack_layout_options(labels, value):
    value = _integer(value, "layout options", 0, 0xFFFFFFFF)
    choices = layout_choices(labels)
    result = {}
    for index in reversed(range(len(choices))):
        count = len(choices[index][1])
        bits = (count - 1).bit_length()
        choice = value & ((1 << bits) - 1)
        if choice >= count:
            raise DefinitionError(f"Firmware layout option {index} has an unknown choice {choice}")
        result[index] = choice
        value >>= bits
    return result


def pack_layout_options(labels, options, previous=0):
    """Pack declared choices, preserving unrelated higher bits from the device."""
    previous = _integer(previous, "layout options", 0, 0xFFFFFFFF)
    choices = layout_choices(labels)
    if not isinstance(options, dict):
        raise DefinitionError("Layout options must map group indices to choices")
    if any(type(index) is not int or not 0 <= index < len(choices) for index in options):
        raise DefinitionError("Unknown physical layout group")
    current = unpack_layout_options(labels, previous)
    value = total_bits = 0
    for index, (_, names) in enumerate(choices):
        choice = _integer(options.get(index, current[index]), "layout choice", 0, len(names) - 1)
        bits = (len(names) - 1).bit_length()
        value = value << bits | choice
        total_bits += bits
    return previous & ~((1 << total_bits) - 1) | value
