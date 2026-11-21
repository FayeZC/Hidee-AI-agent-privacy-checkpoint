"""Hidee in its own window: the workspace in a native app window (pywebview) instead of a browser tab.

The interface is the same local web interface; only the frame changes. On macOS the window uses the system web
view, shows "Hidee" with Hidee's icon in the Dock and menu bar, and closing it quits Hidee. Pages that the
workspace opens beside itself (Behind the agent, a step's picture) open as more Hidee windows, links to other
sites open in the system browser, and downloads (evidence exports, timelines) are saved where the user chooses.
Without pywebview, or without a window system, `available()` is False and Hidee opens the browser instead.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ICON = Path(__file__).parent / "webui" / "static" / "brand" / "hidee-app-icon.png"

# Same-origin links that ask for another window or tab open as another Hidee window instead.
OPEN_IN_HIDEE = """
(() => {
  if (window.__hideeLinks) return;
  window.__hideeLinks = true;
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[target]");
    if (!a || !window.pywebview || !window.pywebview.api) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return;            // other sites: the system browser
    e.preventDefault();
    window.pywebview.api.open(url.href, (a.textContent || "Hidee").replace("↗", "").trim());
  }, true);
})();
"""


def available() -> bool:
    """True if a native window can be opened here."""
    try:
        import webview  # noqa: F401
    except Exception:            # not installed, or no GUI toolkit (some Linux servers)
        return False
    return sys.platform == "darwin" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _brand_macos() -> None:
    """Show "Hidee" and its icon in the Dock and the menu bar, instead of "Python"."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
    except Exception:
        return
    info = NSBundle.mainBundle().infoDictionary()
    if info is not None:
        info["CFBundleName"] = "Hidee"
    if ICON.exists():
        image = NSImage.alloc().initWithContentsOfFile_(str(ICON))
        if image is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(image)


def _bring_to_front() -> None:
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


class Api:
    """What the page may ask of the app: open another Hidee window."""

    def __init__(self, webview_module) -> None:
        self._webview = webview_module

    def open(self, url: str, title: str = "Hidee") -> None:
        window = self._webview.create_window(f"Hidee · {title}" if title and title != "Hidee" else "Hidee", url,
                                             width=1000, height=900, js_api=self)
        window.events.loaded += lambda: window.evaluate_js(OPEN_IN_HIDEE)


def open_window(url: str) -> None:
    """Open the workspace in a Hidee window and return when the user closes it. Runs on the main thread."""
    import webview
    if sys.platform == "darwin":
        _brand_macos()
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    api = Api(webview)
    window = webview.create_window("Hidee", url, width=1440, height=900, min_size=(1000, 640), js_api=api)
    window.events.loaded += lambda: window.evaluate_js(OPEN_IN_HIDEE)
    if sys.platform == "darwin":
        window.events.shown += _bring_to_front     # started from a terminal, macOS would leave it behind
    webview.start(icon=str(ICON) if ICON.exists() else None)
