#!/usr/bin/env bash
# install-fl.sh - one-click setup for the MicroServiceFL localizer (Linux/macOS).
#
#   ./install-fl.sh            # uses `python3` on PATH
#   PYTHON=/path/python ./install-fl.sh
#
# Sets up the Python runtime everything needs. JDK is only required to build the
# endpoint index / decompile; Maven only to build jars from source — both are
# checked (and warned about) but not installed here.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
VENV="$ROOT/.venv"
VENV_PY="$VENV/bin/python"

echo "== MicroServiceFL install =="

# 1. venv
if [ ! -x "$VENV_PY" ]; then
  echo "creating venv ..."
  "$PYTHON" -m venv "$VENV"
fi

# 2. install the package + fl extra
echo "installing package (.[fl]) ..."
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -e "$ROOT[fl]"

# 3. CFR decompiler (optional; grey-box root-cause refinement)
CFR="$HOME/tools/cfr-0.152.jar"
if [ ! -f "$CFR" ]; then
  echo "downloading CFR decompiler ..."
  mkdir -p "$(dirname "$CFR")"
  curl -fsSL -o "$CFR" \
    "https://repo1.maven.org/maven2/org/benf/cfr/0.152/cfr-0.152.jar" \
    || echo "  CFR download failed (optional) - decompile will degrade gracefully"
fi

# 4. environment check
echo
echo "== fl doctor =="
"$VENV_PY" -m microservice_fl doctor || true

echo
echo "Done. Next:"
echo "  # onboard your system (build the endpoint index from jars):"
echo "  $VENV_PY -m microservice_fl build-index --jars <your-jars-dir>"
echo "  # then run (offline via DuckDB, or live via OH_FL_DATASOURCE=skywalking)"
