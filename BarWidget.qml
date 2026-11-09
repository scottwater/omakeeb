import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Bar entry for Omakeeb. The GTK app is a single-instance GApplication, so a
// second click raises the open window. If the app is missing, the click opens a
// terminal that asks before running this plugin folder's install.sh.
BarWidget {
  id: root
  moduleName: "io.github.scottwater.omakeeb"

  readonly property string launch: "b=$(command -v omakeeb || echo \"$HOME/.local/bin/omakeeb\"); "
    + "if [ -x \"$b\" ]; then exec uwsm-app -- \"$b\"; "
    + "else exec omarchy-launch-floating-terminal-with-presentation "
    + "\"read -rp 'Omakeeb is not installed. Press Enter to install it, or Ctrl-C to cancel. ' "
    + "&& bash ~/.config/omarchy/plugins/io.github.scottwater.omakeeb/install.sh\"; fi"

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
