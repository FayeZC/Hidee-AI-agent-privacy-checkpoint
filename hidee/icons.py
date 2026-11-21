"""Thin-line icons (24×24, 1.5 stroke, round caps). One drawing style for the whole interface."""
from __future__ import annotations

from markupsafe import Markup

PATHS = {
    # A friendly robot head: antenna, rounded face, two eyes, a mouth line.
    "agent": '<path d="M12 3.5v2.5"/><circle cx="12" cy="3" r="0.9"/><rect x="5" y="6" width="14" height="12" rx="4"/>'
             '<circle cx="9.5" cy="11.5" r="1.1"/><circle cx="14.5" cy="11.5" r="1.1"/><path d="M10 15h4"/>'
             '<path d="M3.5 11v3M20.5 11v3"/>',
    # Checkpoint: a shield with a check.
    "shield": '<path d="M12 3l7 2.8v5.4c0 4.4-2.9 8-7 9.8-4.1-1.8-7-5.4-7-9.8V5.8z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
    # Outside service: a building with windows.
    "service": '<path d="M4 20.5h16"/><path d="M6 20.5V6.5l6-3 6 3v14"/><path d="M9.5 9h1M13.5 9h1M9.5 12.5h1M13.5 12.5h1"/>'
               '<path d="M11 20.5v-4h2v4"/>',
    "send_message": '<rect x="3.5" y="6" width="17" height="12" rx="2"/><path d="M4 7l8 6 8-6"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
    "remember": '<path d="M7 3.5h10v17l-5-3.5-5 3.5z"/>',
    "recommend": '<path d="M9 17.5h6M10 20.5h4"/><path d="M12 3.5a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V17.5h5v-1.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3.5z"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "cross": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "scissors": '<circle cx="6.5" cy="7" r="2.5"/><circle cx="6.5" cy="17" r="2.5"/><path d="M8.6 8.4L19 17M8.6 15.6L19 7"/>',
    "refresh": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
    "arrow": '<path d="M4 12h15"/><path d="M14 7l5 5-5 5"/>',
}


def path_markup(name: str) -> str:
    return PATHS.get(name, PATHS["service"])


def icon(name: str, size: int = 22, cls: str = "icon") -> Markup:
    """An inline SVG icon for HTML templates; colour follows the surrounding text."""
    return Markup(f'<svg class="{cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
                  f'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" '
                  f'aria-hidden="true">{path_markup(name)}</svg>')


def icon_in_svg(name: str, cx: float, cy: float, size: float, color: str) -> str:
    """The same icon placed inside a larger SVG drawing, centred on (cx, cy)."""
    s = size / 24
    return (f'<g transform="translate({cx - size / 2:.1f},{cy - size / 2:.1f}) scale({s:.3f})" fill="none" '
            f'stroke="{color}" stroke-width="{1.5 / s:.2f}" stroke-linecap="round" '
            f'stroke-linejoin="round">{path_markup(name)}</g>')
