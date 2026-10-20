#!/usr/bin/env bash
# Apply the SPICEBridge fixes this workflow depends on to an installed
# spicebridge package (tested against 1.3.0).
#
#   ./patches/install.sh            apply (idempotent: already-applied hunks are skipped)
#   ./patches/install.sh --check    report which patches would apply
#
# Patches live in patches/ as unified diffs relative to site-packages,
# so `patch -p1 -d <site-packages>` consumes them directly.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PKG_DIR="$(python3 -c 'import spicebridge, os; print(os.path.dirname(spicebridge.__file__))')" \
  || { echo "spicebridge is not installed (pip install spicebridge)"; exit 1; }
VERSION="$(python3 -c 'from importlib.metadata import version; print(version("spicebridge"))')"
SITE="$(dirname "$PKG_DIR")"

echo "spicebridge $VERSION at $PKG_DIR"
if [ "$VERSION" != "1.3.0" ]; then
  echo "WARNING: patches were generated against 1.3.0; expecting conflicts on $VERSION"
fi

status=0
for p in "$HERE"/*.patch; do
  name="$(basename "$p")"
  if [ "${1:-}" = "--check" ]; then
    if patch -N --dry-run -p1 -d "$SITE" < "$p" >/dev/null 2>&1; then
      echo "  would apply: $name"
    elif grep -q "^--- " "$p" && patch -N -R --dry-run -p1 -d "$SITE" < "$p" >/dev/null 2>&1; then
      echo "  already applied: $name"
    else
      echo "  CONFLICT: $name"; status=1
    fi
  else
    if patch -N -p1 -d "$SITE" < "$p" >/dev/null 2>&1; then
      echo "  applied: $name"
    elif patch -N -R --dry-run -p1 -d "$SITE" < "$p" >/dev/null 2>&1; then
      echo "  already applied: $name"
    else
      echo "  FAILED: $name - apply by hand"; status=1
    fi
  fi
done

if [ "$status" = 0 ] && [ "${1:-}" != "--check" ]; then
  echo "done. Restart the spicebridge MCP server (/mcp reload) to pick the fixes up."
fi
exit $status
