#!/bin/sh
# Build the wheel offered on hidee.net/download into ./release, with its SHA-256, and sign it with the release key
# (scripts/release_key.py) so `hidee update` accepts it. Without the key the wheel is built but not signed.
set -eu
cd "$(dirname "$0")/.."
rm -rf release build *.egg-info && mkdir -p release
uv build --wheel --out-dir release >/dev/null 2>&1
# What's new: the bullets of this version's CHANGELOG section (or Unreleased), first line of each.
.venv/bin/python - <<'PY'
import json, pathlib, re
wheel = next(pathlib.Path("release").glob("hidee-*-py3-none-any.whl"))
version = wheel.name.split("-")[1]
text = pathlib.Path("CHANGELOG.md").read_text(encoding="utf-8")
parts = re.split(r"^## ", text, flags=re.M)
section = next((p for p in parts if p.startswith(version + " ")), None) or next((p for p in parts if p.startswith("Unreleased")), "")
notes = []
for line in section.splitlines():
    if line.startswith("- "):
        notes.append(line[2:].strip())
    elif line.startswith("  ") and notes:          # a bullet's continuation line
        notes[-1] += " " + line.strip()
(wheel.parent / (wheel.name + ".notes.json")).write_text(json.dumps(notes[:6], ensure_ascii=False))
PY
if .venv/bin/python scripts/release_key.py public >/dev/null 2>&1; then
  .venv/bin/python scripts/release_key.py sign release/hidee-*.whl
else
  echo "No release key: the wheel is not signed, so installed copies will not accept it as an update." >&2
fi
cd release && shasum -a 256 hidee-*.whl > SHA256SUMS && cat SHA256SUMS
