# Third-party sources

Omakeeb's original implementation is licensed under GPL-3.0-or-later. The following bundled resources retain their upstream terms and notices. No VIA or Vial graphical application is embedded or required at runtime.

| Bundled resource | Source and pinned revision | License |
| --- | --- | --- |
| `resources/definitions.json.gz` | [the-via/keyboards](https://github.com/the-via/keyboards/tree/9e3e9f4aa9f50f9439fdebc747560f51aaf109a0), commit `9e3e9f4aa9f50f9439fdebc747560f51aaf109a0` | GPL-3.0, as declared upstream; [license text](resources/licenses/via-keyboards-GPL-3.0.txt) |
| `resources/keycodes.json` | [the-via/app](https://github.com/the-via/app/tree/935106a990af39bdd1ade881c02366eac486240d), commit `935106a990af39bdd1ade881c02366eac486240d`; literal tables from `src/utils/key-to-byte` | GPL-3.0, as declared upstream; [license text](resources/licenses/via-app-GPL-3.0.txt) |
| `resources/vial-keycodes.json` | [vial-kb/vial-gui](https://github.com/vial-kb/vial-gui/tree/aef8222a2d0429a183b2ed692d5f9efcfd383f08), commit `aef8222a2d0429a183b2ed692d5f9efcfd383f08`; literal legacy and modern keycode aliases | GPL-2.0-or-later; [license text](resources/licenses/vial-gui-GPL-2.0.txt) |
| `omakeeb/data/qmk_settings.json` | [vial-kb/vial-gui](https://github.com/vial-kb/vial-gui/tree/aef8222a2d0429a183b2ed692d5f9efcfd383f08), commit `aef8222a2d0429a183b2ed692d5f9efcfd383f08`; `src/main/resources/base/qmk_settings.json` | GPL-2.0-or-later, under the Vial source's licensing; [license text](resources/licenses/vial-gui-GPL-2.0.txt) |

The keyboard catalog contains validated upstream definition documents. `scripts/vendor_definitions.py` compresses them without carrying firmware or keymaps from users; its output records the source commit, content checksum, accepted count and rejected documents in `resources/definitions-source.json`. `scripts/vendor_keycodes.py` extracts literal keycode tables and records their provenance in `resources/keycodes.json`.

The protocol implementation was developed using the public QMK/VIA protocol and the VIA and Vial source implementations as references. Vial's relevant Python protocol and settings code carries `SPDX-License-Identifier: GPL-2.0-or-later`. The bundled license texts accompany the installed application.

Manufacturer definition files imported by a user remain in that user's data directory. A manufacturer DOIO JSON without a redistribution license is intentionally absent from the distributed catalog. Installing Omakeeb does not copy local imported definitions into its application resources.

GTK 4, PyGObject, Pycairo and hidapi are installed separately through the operating system. Omakeeb does not vendor those libraries; their distributions supply their own licenses.
