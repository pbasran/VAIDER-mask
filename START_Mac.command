#!/bin/bash
# First run creates a private Python environment in your home folder (~/.vaider/envs/VAIDER-mask) and installs pydicom + numpy.
ENV="$HOME/.vaider/envs/VAIDER-mask"
cd "$(dirname "$0")/scripts" || exit 1
if [ ! -x "$ENV/bin/python" ]; then
  echo "First run: setting up environment (needs internet once)..."
  python3 -m venv "$ENV" || { echo "Could not create environment. Is Python 3 installed?"; exit 1; }
  "$ENV/bin/python" -m pip install --quiet pydicom numpy || { echo "Install failed."; rm -rf "$ENV"; exit 1; }
fi
"$ENV/bin/python" server.py
