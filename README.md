# Omakeeb

Omakeeb is a native GTK 4 application for configuring QMK keyboards on Omarchy. It uses the current Omarchy palette, discovers your live Hyprland shortcuts, and keeps edits in a local draft until you review and apply them.

The app talks directly to VIA- and Vial-enabled firmware through the system HID library. It includes an offline VIA definition catalog, so supported keyboards do not need a downloaded layout JSON or a browser connection. Python 3.11 and GTK 4.10 or newer are required. Python's standard library handles the application logic; GTK and HID access use system packages.

## Run or install

On Omarchy, install the system dependencies if they are missing:

```sh
omarchy pkg add python gtk4 python-gobject python-cairo hidapi
```

Run from this checkout:

```sh
make run
make demo     # Explore a keyboard without opening any USB device
```

Install for your user:

```sh
make install
```

Open **Omakeeb** from the application launcher, or run `~/.local/bin/omakeeb`. The installer copies the application and bundled resources into `~/.local/share/omakeeb/app`; moving this checkout afterward will not break it. Run `make install` again after updating the source. No virtual environment, pip packages, Node runtime, browser, or running server is needed.

The installer honors `XDG_DATA_HOME`. To inspect or change its destinations:

```sh
python3 scripts/install.py --dry-run
python3 scripts/install.py --data-home /path/to/data --bin-dir /path/to/bin
```

Use `make uninstall`, or the installed `scripts/install.py uninstall` with the same path options, to remove the app and launcher. Backups, firmware archives, and imported keyboard definitions remain in place. The installer refuses to overwrite unrelated files.

## Configure a keyboard

1. Connect by USB and choose **Connect**. Omakeeb recognizes the VIA raw-HID interface, reads the firmware version and configuration, and saves an automatic backup before enabling edits.
2. Choose a key and layer in **Keymap**, then select an action. The searchable palette includes ordinary keys, modifiers, layer functions, media keys and QMK codes. The **Omarchy** key group offers your current desktop bindings as single-key mappings, and keys that trigger one are outlined and labeled with its name.
3. Edit macros, supported lighting controls, encoder directions or Vial advanced features. Changes stay in the draft. Undo and discard are available before applying.
4. Review the changes and apply them. Omakeeb checks that the device still matches the original snapshot, saves another backup, and verifies the result by reading it back.
5. Use **Test keys** to check the keyboard. The focused tester shows received keys and releases. Physical matrix testing reads switch positions where supported; Vial requires physical unlocking first. Hyprland may intercept desktop shortcuts in the focused tester. Tests use the applied mapping, not an unapplied draft.

A board must already run compatible VIA or Vial firmware. Plain QMK firmware does not expose arbitrary configuration changes over USB. Omakeeb does not compile firmware or turn a keyboard into a VIA-compatible device.

## Supported features

| Feature | Availability |
| --- | --- |
| Layer keymaps and modifier/layer keycodes | VIA protocols 9–13; Vial protocols 0–6, with version-specific keycode tables |
| Dynamic macros | Firmware-provided slots and capacity; text, taps, chords, holds/releases, and delays where supported |
| Rotary encoders | Firmware-provided dynamic encoder mappings; knobs implemented as matrix keys use Keymap |
| Lighting | Declared QMK backlight, underglow, RGB matrix and LED matrix channels; supported Vial RGB controls |
| Tap dance and combos | Vial firmware advertising these dynamic features |
| Key overrides and alternate repeat keys | Supported Vial firmware versions |
| QMK settings | Recognized Vial setting IDs, widths and ranges |
| Omarchy shortcuts | Current described Hyprland bindings; curated defaults when Hyprland is unavailable |
| Import/export | Native Omakeeb profiles, VIA JSON, Vial `.vil`, and verified automatic backups |

Features absent from the firmware stay unavailable. Manufacturer-specific VIA menus and unknown settings are not guessed. Compile-time QMK features need a firmware build outside Omakeeb. Unknown numeric keycodes can be preserved; new bootloader, reset and EEPROM-clearing assignments are blocked.

Macro text uses QMK's ASCII send-string support, normally the US layout. A macro cannot leave a key held down. Vial and VIA encode some actions differently; imports translate supported actions and reject malformed or oversized sequences.

## Definitions and imports

The bundled catalog contains 2,083 VIA definitions from a pinned upstream revision. It is available offline and does not update itself. If more than one definition matches a device's USB IDs, choose the correct model rather than relying on an arbitrary match. Vial boards can supply their own definition through firmware.

A definition describes key geometry, matrix addresses and available controls. It is different from a keymap, which contains your assignments. For a missing board, import the maker's definition JSON once; it is saved under `~/.local/share/omakeeb/definitions`. The DOIO manufacturer definition discussed during development is not redistributed because its download did not supply a redistribution license.

Use native `.omakeeb.json` exports for complete configuration preservation. These files include the exact keyboard definition and can be opened offline from the welcome screen. Native exports from the app embed the verified keyboard definition, so they can be opened for offline editing without reconnecting the device. VIA exports contain keymaps, macros and encoders. Vial exports also contain supported dynamic mappings and settings. Those external formats do not carry the complete lighting backup. Import always creates a draft and validates the keyboard identity, dimensions and capabilities before any write can occur.

## Backups and firmware

Automatic backups live under `~/.local/share/omakeeb/backups`, or the equivalent `XDG_DATA_HOME` directory. They include the definition, readable configuration, device identity and a checksum. Files have private permissions because macro text can contain personal information. Backups must succeed before an apply can begin.

A configuration backup is not a firmware image. VIA and Vial do not provide a general way to read back the executable firmware. **Archive firmware file** saves a local manufacturer `.bin`, `.hex`, `.uf2` or `.zip` with a SHA-256 checksum and provenance. It does not verify that the file is compatible with the keyboard.

Omakeeb has no firmware-flashing, bootloader-entry, factory-reset or EEPROM-erase operation. A USB interruption can still leave configuration partly updated. The app stops, retains the backup, and requires a fresh connection before recovery. It does not blindly retry writes or attempt an automatic rollback on an uncertain connection.

## Linux device permissions

The application runs as your user. If it can see a keyboard but cannot open its HID interface, grant access to that model through a udev rule. Use the actual vendor and product IDs from the connection information, not a rule that permits all HID devices.

For example, an administrator can add a rule to `/etc/udev/rules.d/59-omakeeb.rules`:

```udev
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="8968", ATTRS{idProduct}=="4e54", MODE="0660", TAG+="uaccess"
```

Those IDs are an example, not a universal QMK identifier. After reloading udev rules, reconnect the keyboard. The `59-` prefix places the rule before the seat-access rules. Omakeeb does not install privileged rules or change system permissions automatically.

## Development

```sh
make check
make ui-check   # Requires a graphical session; uses a demo, never a USB device
```

Tests use fake transports and temporary data directories. They exercise protocol framing, interrupted writes, stale snapshots, backup failures, import validation, macro formats, catalog parsing, and user installation. They never write to a physical keyboard. Read-only checks have verified configuration discovery on the connected NovelKeys NK Classic TKL and DOIO board. The DOIO firmware does not respond to the declared RGB getter commands; those controls stay unavailable instead of sending speculative writes. No configuration writes or firmware flashes were performed on either physical keyboard during development. A passing suite does not establish compatibility with every board in the catalog; firmware-specific hardware checks are still needed.

The code is organized around independent modules: `hid.py` handles transport, `protocol.py` negotiates capabilities, `safety.py` controls verified transactions, and `app.py` provides the GTK interface. File conversion, macro encoding, keycodes, definitions and desktop integration can be tested without GTK or a keyboard.

To refresh bundled data from reviewed local upstream checkouts:

```sh
python3 scripts/vendor_definitions.py /path/to/the-via/keyboards
python3 scripts/vendor_keycodes.py /path/to/the-via/app
python3 scripts/vendor_vial_keycodes.py /path/to/vial-gui
make check
make ui-check   # Requires a graphical session; uses a demo, never a USB device
```

Review the generated source manifests, rejected definitions and license changes before shipping an update. The application performs no automatic catalog download.

Omakeeb's original source is licensed under GPL-3.0-or-later. Bundled upstream data retain their own licenses. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).
