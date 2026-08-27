"""
Festival theme for the automated report emails — added 2026-08-27, redesigned
2026-08-27 (v2) so a colorful decorative motif sits INSIDE the header's own
orange gradient band (not a separate box below it), redesigned again same
day (v3) to bring back the small "Happy Onam!"-style greeting card below
the header too — so the email now carries BOTH: a colorful icon inside the
gradient header itself, and the original small text card underneath it.

No CSS/GIF animation: corporate recipients read these mostly in Outlook,
which doesn't run CSS @keyframes, and some strict clients strip <style>
tags entirely (which can leak raw CSS as visible text — worse than no
animation). So instead of animation, the header icon is full-color and
detailed rather than a flat faded watermark — "attractive" achieved
through color and detail, which renders identically everywhere.

SVG, not emoji: emoji render inconsistently across mail clients (different
glyph sets per platform, can look unprofessional in Outlook).

Injected once, centrally, in services/email_service.py's shared_context —
applies to every automated send regardless of frequency/level, as long as
the template's header contains the {{#if Has_Festival_Banner}} blocks for
{{Festival_Header_Motif}} (header's own decorative column) and
{{Festival_Card_HTML}} (the small greeting card below the header) — added
to all 7 templates alongside this.

DATE ACCURACY NOTE: fixed-date entries (New Year, Republic Day, Makar
Sankranti/Pongal, Independence Day, Gandhi Jayanti, Christmas) are exact.
Onam and Raksha Bandhan were confirmed via web search on 2026-08-26
(multiple independent sources) — Onam runs Atham (Aug 16) to Thiruvonam
(Aug 26); Raksha Bandhan is Aug 28. The remaining lunar-calendar entries
(Holi, Eid al-Fitr, Eid al-Adha, Ganesh Chaturthi, Navratri/Dussehra,
Diwali) are still 2026 BEST-ESTIMATE dates and should be confirmed the
same way before relying on them — getting one of these wrong (wrong day,
or missed entirely) is a real reputational risk in front of real bank
recipients, not just a cosmetic bug.

ICON QUALITY: two header motifs (Onam's boat, Raksha Bandhan's rakhi) are
dedicated, browser-verified illustrations built from simple, safely
recognisable shapes. The rest still fall back to a generic small icon
scaled up (_header_motif's default path) — good enough as a placeholder,
but each one should get the same dedicated-icon-plus-browser-verification
treatment as its festival approaches, rather than trusting an unverified
scaled-up icon to read correctly on the day.
"""
from __future__ import annotations

import re
from datetime import date


def _star(fg: str) -> str:
    return (f'<svg width="18" height="18" viewBox="0 0 24 24" style="vertical-align:middle">'
            f'<path d="M12 2L14 10L22 12L14 14L12 22L10 14L2 12L10 10Z" fill="{fg}"/></svg>')


def _kite(fg: str) -> str:
    return (f'<svg width="18" height="18" viewBox="0 0 24 24" style="vertical-align:middle">'
            f'<path d="M12 2L20 12L12 22L4 12Z" fill="{fg}"/>'
            f'<path d="M12 22L14 24M12 22L10 24" stroke="{fg}" stroke-width="1.3" fill="none" stroke-linecap="round"/>'
            f'<line x1="12" y1="2" x2="12" y2="22" stroke="#ffffff" stroke-width="0.6" opacity="0.5"/></svg>')


def _flag(_fg: str) -> str:
    # Authentic tricolor — fixed regardless of theme.
    return ('<svg width="22" height="16" viewBox="0 0 32 24" style="vertical-align:middle">'
            '<rect x="1" y="1" width="30" height="6.67" fill="#FF9933"/>'
            '<rect x="1" y="7.67" width="30" height="6.67" fill="#FFFFFF"/>'
            '<rect x="1" y="14.33" width="30" height="6.67" fill="#128807"/>'
            '<rect x="1" y="1" width="30" height="20" fill="none" stroke="#ddd4c6" stroke-width="0.6"/>'
            '<circle cx="16" cy="11" r="2.1" fill="none" stroke="#2452c0" stroke-width="0.7"/>'
            '</svg>')


def _splash(_fg: str) -> str:
    return ('<svg width="20" height="18" viewBox="0 0 26 22" style="vertical-align:middle">'
            '<circle cx="7" cy="8" r="4.2" fill="#e5670f"/>'
            '<circle cx="17" cy="6.5" r="3.6" fill="#2452c0"/>'
            '<circle cx="13" cy="15" r="4.6" fill="#127a38"/>'
            '<circle cx="20" cy="15" r="3" fill="#b98811"/></svg>')


def _crescent(fg: str) -> str:
    return (f'<svg width="18" height="18" viewBox="0 0 24 24" style="vertical-align:middle">'
            f'<path d="M15.5 3.5a9 9 0 100 17 7.2 7.2 0 010-17z" fill="{fg}"/>'
            f'<path d="M20 5.2l0.8 1.7 1.8.8-1.8.8-.8 1.7-.8-1.7-1.8-.8 1.8-.8Z" fill="{fg}"/></svg>')


def _boat(fg: str) -> str:
    return (f'<svg width="24" height="16" viewBox="0 0 34 22" style="vertical-align:middle">'
            f'<path d="M2 13 Q17 2 32 13 Q17 21 2 13Z" fill="{fg}"/>'
            f'<line x1="17" y1="2" x2="17" y2="13" stroke="{fg}" stroke-width="1.3"/>'
            f'<path d="M17 3 L23 6 L17 8Z" fill="#ffffff" opacity="0.7"/></svg>')


def _boat_colorful(_fg: str) -> str:
    """A large, clearly-readable decorated Onam boat — the header's own
    decorative motif. A plain trapezoid hull (the simplest shape that
    unambiguously reads as "boat", browser-verified before shipping —
    an earlier elongated/dotted version looked like an abstract blob,
    not a boat) with an upswept gold prow (snake-boat silhouette), a
    ceremonial red-and-gold canopy at the centre, and two small flags on
    poles either side, sitting on water ripples. Fixed festive palette
    regardless of theme."""
    return (
        '<svg width="120" height="60" viewBox="0 0 140 70" style="vertical-align:middle">'
        '<path d="M2 58 Q35 53 70 58 T138 58" stroke="#ffd9a8" stroke-width="2.2" fill="none" opacity="0.6"/>'
        '<path d="M4 64 Q37 60 72 64 T138 64" stroke="#ffe9cc" stroke-width="1.8" fill="none" opacity="0.45"/>'
        '<path d="M118 40 Q132 32 128 16" stroke="#f0c04a" stroke-width="4" fill="none" stroke-linecap="round"/>'
        '<circle cx="128" cy="14" r="4" fill="#f0c04a"/>'
        '<path d="M8 40 Q0 36 3 28" stroke="#f0c04a" stroke-width="3" fill="none" stroke-linecap="round"/>'
        '<path d="M8 40 L118 40 L108 54 L20 54 Z" fill="#7a2e1a"/>'
        '<path d="M12 40 L112 40 L104 50 L22 50 Z" fill="#a83f24"/>'
        '<path d="M8 40 L118 40" stroke="#f0c04a" stroke-width="1.6"/>'
        '<line x1="30" y1="40" x2="30" y2="20" stroke="#5c2313" stroke-width="2"/>'
        '<path d="M30 20 L42 25 L30 30Z" fill="#2c9a54"/>'
        '<line x1="96" y1="40" x2="96" y2="22" stroke="#5c2313" stroke-width="2"/>'
        '<path d="M96 22 L108 27 L96 32Z" fill="#3a6fd8"/>'
        '<rect x="52" y="24" width="24" height="16" rx="2" fill="#c1352a"/>'
        '<path d="M50 24 Q64 8 78 24Z" fill="#f0c04a"/>'
        '</svg>'
    )


def _rakhi(fg: str) -> str:
    return (f'<svg width="18" height="18" viewBox="0 0 24 24" style="vertical-align:middle">'
            f'<circle cx="12" cy="9" r="6" fill="none" stroke="{fg}" stroke-width="1.6"/>'
            f'<circle cx="12" cy="9" r="2" fill="{fg}"/>'
            f'<path d="M9 14.5 Q8 18 9 22" stroke="{fg}" stroke-width="1.2" fill="none" stroke-linecap="round"/>'
            f'<path d="M15 14.5 Q16 18 15 22" stroke="{fg}" stroke-width="1.2" fill="none" stroke-linecap="round"/></svg>')


def _rakhi_colorful(_fg: str) -> str:
    """An ornate rakhi (sacred thread bracelet) — the header's own
    decorative motif for Raksha Bandhan, modelled on a real jewelled
    rakhi (concentric medallion with gem-tone accents, a beaded chain
    instead of a plain line, a tassel fringe at each end) rather than a
    plain flower-and-thread sketch. Built entirely from circles, small
    diamond "gems", and short line segments — simple, predictable
    primitives, chosen deliberately after the first Onam boat draft
    (built from complex freehand curves) misrendered as an abstract
    blob. Fixed festive palette regardless of theme."""
    gem_colors = ["#2c9a54", "#3a6fd8", "#f0c04a", "#e5670f"] * 2
    gems = "".join(
        f'<path d="M50 6 L53.2 10 L50 14 L46.8 10Z" fill="{c}" transform="rotate({a} 50 32)"/>'
        for a, c in zip(range(0, 360, 45), gem_colors)
    )

    def _chain(points, bead_colors):
        segs = "".join(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#f0c04a" stroke-width="1.6" stroke-linecap="round"/>'
            for (x1, y1), (x2, y2) in zip(points, points[1:])
        )
        beads = "".join(
            f'<circle cx="{x}" cy="{y}" r="2.1" fill="{c}"/>'
            for (x, y), c in zip(points[1:-1], bead_colors)
        )
        return segs + beads

    def _fringe(tip, dxs):
        tx, ty = tip
        return "".join(
            f'<line x1="{tx}" y1="{ty}" x2="{tx + dx}" y2="{ty + 9}" stroke="#c1352a" stroke-width="1.6" stroke-linecap="round"/>'
            for dx in dxs
        )

    left_pts = [(40, 52), (34, 60), (42, 68), (34, 76), (41, 84)]
    right_pts = [(60, 52), (66, 60), (58, 68), (66, 76), (59, 84)]
    left_beads = ["#c1352a", "#f0c04a", "#c1352a"]
    right_beads = ["#f0c04a", "#c1352a", "#f0c04a"]

    return (
        '<svg width="80" height="84" viewBox="0 0 100 105" style="vertical-align:middle">'
        '<circle cx="50" cy="32" r="23" fill="none" stroke="#c1352a" stroke-width="4"/>'
        '<circle cx="50" cy="32" r="19" fill="none" stroke="#f0c04a" stroke-width="1.2" stroke-dasharray="2 2.5"/>'
        f'{gems}'
        '<circle cx="50" cy="32" r="13" fill="#7a2618"/>'
        '<circle cx="50" cy="32" r="8.5" fill="#2c5fc0"/>'
        '<circle cx="50" cy="32" r="4.5" fill="#f0c04a"/>'
        '<circle cx="50" cy="32" r="2" fill="#c1352a"/>'
        f'{_chain(left_pts, left_beads)}'
        f'{_chain(right_pts, right_beads)}'
        f'{_fringe(left_pts[-1], (-5, -1.5, 2, 5.5))}'
        f'{_fringe(right_pts[-1], (-5.5, -2, 1, 5))}'
        '<circle cx="41" cy="84" r="2.4" fill="#f0c04a"/>'
        '<circle cx="59" cy="84" r="2.4" fill="#f0c04a"/>'
        '</svg>'
    )


def _modak(fg: str) -> str:
    return (f'<svg width="16" height="18" viewBox="0 0 20 24" style="vertical-align:middle">'
            f'<path d="M10 2C10 2 2 12 2 17a8 8 0 0016 0c0-5-8-15-8-15Z" fill="{fg}"/>'
            f'<path d="M10 6l-1.6 3.2h3.2Z" fill="#ffffff" opacity="0.55"/></svg>')


def _charkha(fg: str) -> str:
    return (f'<svg width="18" height="18" viewBox="0 0 24 24" style="vertical-align:middle">'
            f'<circle cx="12" cy="12" r="9" fill="none" stroke="{fg}" stroke-width="1.4"/>'
            f'<circle cx="12" cy="12" r="1.4" fill="{fg}"/>'
            f'<line x1="12" y1="3.3" x2="12" y2="20.7" stroke="{fg}" stroke-width="0.9"/>'
            f'<line x1="3.3" y1="12" x2="20.7" y2="12" stroke="{fg}" stroke-width="0.9"/>'
            f'<line x1="5.7" y1="5.7" x2="18.3" y2="18.3" stroke="{fg}" stroke-width="0.9"/>'
            f'<line x1="18.3" y1="5.7" x2="5.7" y2="18.3" stroke="{fg}" stroke-width="0.9"/></svg>')


def _dandiya(fg: str) -> str:
    return (f'<svg width="18" height="18" viewBox="0 0 24 24" style="vertical-align:middle">'
            f'<line x1="4" y1="20" x2="20" y2="4" stroke="{fg}" stroke-width="2.2" stroke-linecap="round"/>'
            f'<line x1="4" y1="4" x2="20" y2="20" stroke="{fg}" stroke-width="2.2" stroke-linecap="round"/>'
            f'<circle cx="4" cy="20" r="1.6" fill="{fg}"/><circle cx="20" cy="4" r="1.6" fill="{fg}"/>'
            f'<circle cx="4" cy="4" r="1.6" fill="{fg}"/><circle cx="20" cy="20" r="1.6" fill="{fg}"/></svg>')


def _diya(fg: str) -> str:
    return (f'<svg width="20" height="20" viewBox="0 0 24 26" style="vertical-align:middle">'
            f'<path d="M3 16 Q12 23 21 16 Q17 20.5 12 20.5 Q7 20.5 3 16Z" fill="{fg}"/>'
            f'<path d="M12 5c-2.6 3.4-2.6 6.2 0 9 2.6-2.8 2.6-5.6 0-9Z" fill="#e5670f"/></svg>')


def _tree(fg: str) -> str:
    return (f'<svg width="16" height="20" viewBox="0 0 20 26" style="vertical-align:middle">'
            f'<path d="M10 1 L14.5 9 L12 9 L16.5 16 L13 16 L17.5 22 L2.5 22 L7 16 L3.5 16 L8 9 L5.5 9Z" fill="{fg}"/>'
            f'<rect x="8" y="22" width="4" height="2.5" fill="#8a6410"/>'
            f'<circle cx="10" cy="1.5" r="1.4" fill="#e5670f"/></svg>')


# (start_date, end_date, name, icon_fn, greeting, soft_bg, border, text_fg, header_icon_fn)
# end_date is inclusive. soft/border/fg style the small card below the
# header (icon + greeting, same trio used elsewhere in these templates).
# header_icon_fn (optional, defaults to icon_fn) is what renders inside
# the header's own gradient — usually the same icon at a bigger size in
# its own fg color; Onam gets a dedicated full-color illustration instead.
FESTIVALS: list[tuple] = [
    (date(2026, 1, 1), date(2026, 1, 1), "New Year", _star,
     "Wishing you a bright and successful start to the New Year!",
     "#e4eafa", "#c7d3f2", "#2452c0", None),
    (date(2026, 1, 14), date(2026, 1, 14), "Makar Sankranti / Pongal", _kite,
     "Happy Makar Sankranti &amp; Pongal!",
     "#fef6f1", "#facdae", "#c1520a", None),
    (date(2026, 1, 26), date(2026, 1, 26), "Republic Day", _flag,
     "Happy Republic Day!",
     "#fef6f1", "#facdae", "#c1520a", None),
    # Best-estimate lunar date — verify against a 2026 panchang.
    (date(2026, 3, 3), date(2026, 3, 4), "Holi", _splash,
     "Happy Holi! Wishing you a colorful and joyful day.",
     "#faf1dd", "#ecdcae", "#8a6410", None),
    # Best-estimate lunar date — verify against a 2026 Islamic calendar.
    (date(2026, 3, 20), date(2026, 3, 21), "Eid al-Fitr", _crescent,
     "Eid Mubarak!",
     "#e2f2e7", "#bfe0cb", "#127a38", None),
    # Best-estimate lunar date — verify against a 2026 Islamic calendar.
    (date(2026, 5, 27), date(2026, 5, 27), "Eid al-Adha", _crescent,
     "Eid Mubarak!",
     "#e2f2e7", "#bfe0cb", "#127a38", None),
    (date(2026, 8, 15), date(2026, 8, 15), "Independence Day", _flag,
     "Happy Independence Day!",
     "#fef6f1", "#facdae", "#c1520a", None),
    # Confirmed via web search (multiple sources, 2026-08-26): Onam runs
    # Atham (Aug 16) to Thiruvonam (Aug 26) — the main celebration day.
    (date(2026, 8, 16), date(2026, 8, 26), "Onam", _boat,
     "Happy Onam! Wishing you prosperity and joy.",
     "#e2f2e7", "#bfe0cb", "#127a38", _boat_colorful),
    # Confirmed via web search (multiple sources, 2026-08-26): Raksha
    # Bandhan (Shravan Purnima) is Friday, 28 August 2026.
    (date(2026, 8, 28), date(2026, 8, 28), "Raksha Bandhan", _rakhi,
     "Happy Raksha Bandhan!",
     "#fce9d9", "#f6ad79", "#c1520a", _rakhi_colorful),
    # Best-estimate lunar date — verify against a 2026 panchang.
    (date(2026, 9, 14), date(2026, 9, 14), "Ganesh Chaturthi", _modak,
     "Happy Ganesh Chaturthi!",
     "#fce9d9", "#f6ad79", "#c1520a", None),
    (date(2026, 10, 2), date(2026, 10, 2), "Gandhi Jayanti", _charkha,
     "Gandhi Jayanti — a day of remembrance.",
     "#f4f0eb", "#ddd4c6", "#4b443d", None),
    # Best-estimate lunar dates — verify against a 2026 panchang.
    (date(2026, 10, 11), date(2026, 10, 20), "Navratri &amp; Dussehra", _dandiya,
     "Happy Navratri! Wishing you strength and joy.",
     "#fce9d9", "#f6ad79", "#c1520a", None),
    (date(2026, 11, 8), date(2026, 11, 8), "Diwali", _diya,
     "Happy Diwali! Wishing you light and prosperity.",
     "#faf1dd", "#ecdcae", "#8a6410", None),
    (date(2026, 12, 25), date(2026, 12, 25), "Christmas", _tree,
     "Merry Christmas!",
     "#e2f2e7", "#bfe0cb", "#127a38", None),
]

_ROOT_TAG_RE = re.compile(r'^(<svg width=")(\d+)(" height=")(\d+)(".*)$', re.DOTALL)


def _scale(svg: str, factor: float) -> str:
    m = _ROOT_TAG_RE.match(svg)
    if not m:
        return svg
    w = round(int(m.group(2)) * factor)
    h = round(int(m.group(4)) * factor)
    return f"{m.group(1)}{w}{m.group(3)}{h}{m.group(5)}"


def _card_html(greeting: str, soft: str, border: str, fg: str) -> str:
    """The small greeting card below the header — centered text only (no
    icon; the header's own motif already carries the visual), same
    soft-tint-card pattern used elsewhere in these templates."""
    return (
        '<tr><td style="padding:14px 26px 0;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{soft};border:1px solid {border};border-radius:10px;">'
        f'<tr><td align="center" style="padding:10px 16px;font-size:13px;color:{fg};font-weight:600;text-align:center;">'
        f'{greeting}'
        '</td></tr></table></td></tr>'
    )


def _header_motif(icon_fn, fg: str, already_sized: bool) -> str:
    """Colorful icon for the header's own gradient band — full color (not
    a faded watermark), sized to read as a deliberate decoration next to
    the header text, not clutter. Default icons (~16-24px) get scaled up;
    a custom header_icon_fn (like the detailed Onam boat) is already
    drawn at its final header size, so it's used as-is."""
    svg = icon_fn(fg)
    return svg if already_sized else _scale(svg, 2.6)


def get_festival_context(today: date | None = None) -> dict:
    """Context variables the card block reads: Has_Festival_Banner (bool,
    gates it) and Festival_Card_HTML (the small greeting card row below
    the header). Never raises — an unrecognised/normal day just means
    nothing renders, not an error.

    Per explicit instruction 2026-08-27: the header's own icon
    (Festival_Header_Motif, drawn into the orange gradient band itself)
    is removed — only the card below the header stays. _header_motif()
    is kept unused rather than deleted, in case the header icon comes
    back later; it's simply never called from here anymore."""
    today = today or date.today()
    for start, end, _name, icon_fn, greeting, soft, border, fg, header_icon_fn in FESTIVALS:
        if start <= today <= end:
            return {
                "Has_Festival_Banner": True,
                "Festival_Card_HTML": _card_html(greeting, soft, border, fg),
            }
    return {"Has_Festival_Banner": False, "Festival_Card_HTML": ""}
