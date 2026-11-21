# PyInstaller spec for Hidee.app (macOS). Built by scripts/build_dmg.sh; see hidee/frozen.py for the entry point.
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

version = os.environ["HIDEE_VERSION"]
here = os.path.abspath(SPECPATH)

hidden = collect_submodules("hidee") + collect_submodules("uvicorn") + ["webview.platforms.cocoa"]
datas = collect_data_files("hidee")                 # templates, CSS, JavaScript, fonts, the app icon

a = Analysis([os.path.join(here, "launcher.py")], hiddenimports=hidden, datas=datas,
             excludes=["tkinter", "pytest", "IPython", "matplotlib", "numpy"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Hidee", console=False,
          argv_emulation=False, target_arch=None, codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="Hidee")
app = BUNDLE(coll, name="Hidee.app", icon=os.path.join(here, "Hidee.icns"), bundle_identifier="net.hidee.app",
             version=version,
             info_plist={"CFBundleName": "Hidee", "CFBundleDisplayName": "Hidee",
                         "CFBundleShortVersionString": version, "CFBundleVersion": version,
                         "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0",
                         "NSHumanReadableCopyright": "Apache License 2.0"})
