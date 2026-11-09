#!/bin/bash
# Install or update Omakeeb for the current user on Omarchy.
#
#   curl -fsSL https://raw.githubusercontent.com/scottwater/omakeeb/master/install.sh | bash
#
# Arguments go to scripts/install.py, e.g. `| bash -s -- uninstall`.
# Set OMAKEEB_REF to install a tag, branch or commit instead of master.
set -euo pipefail

if [[ ${1:-} != uninstall ]]; then
  omarchy-pkg-add python gtk4 python-gobject python-cairo hidapi
fi

# Run from a checkout (or the Omarchy plugin folder) when there is one;
# otherwise fetch the source. install.py copies it, so the download is temporary.
src=${BASH_SOURCE[0]:+$(dirname "${BASH_SOURCE[0]}")}
if [[ -z $src || ! -f $src/scripts/install.py ]]; then
  src=$(mktemp -d)
  trap 'rm -rf "$src"' EXIT
  curl -fsSL "https://github.com/scottwater/omakeeb/archive/${OMAKEEB_REF:-master}.tar.gz" |
    tar -xz --strip-components=1 -C "$src"
fi

python3 "$src/scripts/install.py" "$@"
