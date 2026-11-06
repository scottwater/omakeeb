import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Bar entry for Omakeeb. The GTK app is installed separately with `make install`;
// it is a single-instance GApplication, so a second click raises the open window.
BarWidget {
  id: root
  moduleName: "io.github.scottwater.omakeeb"

  readonly property string launch: "b=$(command -v omakeeb || echo \"$HOME/.local/bin/omakeeb\"); "
    + "if [ -x \"$b\" ]; then exec uwsm-app -- \"$b\"; "
    + "else omarchy-notification-send 'Omakeeb is not installed' 'Run make install in ~/.config/omarchy/plugins/io.github.scottwater.omakeeb'; fi"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    slotSize: Style.bar.statusSlot
    tooltipText: "Omakeeb"
    onPressed: Quickshell.execDetached(["sh", "-c", root.launch])
  }
}
