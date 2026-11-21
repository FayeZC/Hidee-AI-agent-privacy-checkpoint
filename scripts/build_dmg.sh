#!/bin/sh
# Build Hidee.app and release/Hidee-<version>-macos-<arch>.dmg (unsigned) with PyInstaller, in its own environment.
# Run from the repository root on a Mac. The app runs on the architecture it was built on.
set -eu
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
ARCH=$(uname -m)
BUILD=build/dmg
rm -rf "$BUILD" && mkdir -p "$BUILD" release

# The app icon, from the 1024 px PNG
ICONSET="$BUILD/Hidee.iconset"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s hidee/webui/static/brand/hidee-app-icon.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) hidee/webui/static/brand/hidee-app-icon.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o packaging/macos/Hidee.icns

# A clean environment with Hidee and PyInstaller only
uv venv -q "$BUILD/venv"
uv pip install -q --python "$BUILD/venv/bin/python" . pyinstaller
HIDEE_VERSION="$VERSION" "$BUILD/venv/bin/pyinstaller" --noconfirm --clean \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" packaging/macos/Hidee.spec >"$BUILD/pyinstaller.log" 2>&1 \
  || { tail -30 "$BUILD/pyinstaller.log"; exit 1; }

# The disk image: the app and a link to Applications, to drag it across
STAGE="$BUILD/stage"; mkdir -p "$STAGE"
cp -R "$BUILD/dist/Hidee.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="release/Hidee-$VERSION-macos-$ARCH.dmg"
rm -f "$DMG"
hdiutil create -quiet -volname "Hidee $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
echo "Built $DMG ($(du -h "$DMG" | cut -f1))"
shasum -a 256 "$DMG"
