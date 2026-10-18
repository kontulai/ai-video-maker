"""Infomercial-style keyword graphics burned on top of the video.

Gemini picks a few moments worth emphasising (at most one per clip, most clips
get none), a placement that avoids the action, and an accent colour taken from
the film's own palette. Each graphic is timed to the exact spoken words using
the ElevenLabs word timings and rendered as animated motion graphics (an ASS
file drawn by libass), so it reads as part of the edit rather than a subtitle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from pydantic import BaseModel, Field

from segmentation import Segment, Word

# Roughly how many seconds of video per graphic, by density setting.
DENSITY_SECONDS = {"low": 15, "medium": 9, "high": 5}

STYLES = ("slam", "swipe", "reveal")
POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right", "center")

# Plain-language area names used to tell Veo where to leave room.
POSITION_AREA = {
    "top-left": "upper-left third", "top-right": "upper-right third",
    "bottom-left": "lower-left third", "bottom-right": "lower-right third",
    "center": "centre",
}

MIN_SHOW = 1.8     # seconds a graphic stays up at minimum
HOLD_AFTER = 1.2   # seconds it lingers after the anchor words are spoken
LEAD_IN = 0.15     # appear just before the words are spoken
GAP = 0.2          # space between two consecutive graphics
DEFAULT_ACCENT = "#FFC400"


class Callout(BaseModel):
    segment_index: int = Field(description="Index of the segment this graphic belongs to.")
    text: str = Field(description=(
        "The on-screen text: 1 to 4 words, at most 22 characters, punchy. May rephrase "
        "the narration: numbers as digits, prices, percentages (e.g. '50% OFF', "
        "'FREE SHIPPING', '3 EASY STEPS')."))
    anchor: str = Field(description=(
        "1 to 3 consecutive words copied exactly from that segment's narration, "
        "spoken at the moment the graphic should appear."))
    style: str = Field(description=(
        "'slam': huge text that crashes in; best for 1-2 words, numbers, prices, offers. "
        "'swipe': label on a colour bar that wipes in, like a TV lower-third; best for "
        "product names, features, calls to action. "
        "'reveal': letters appear one by one; best for a key idea or benefit."))
    position: str = Field(description=(
        "One of 'top-left', 'top-right', 'bottom-left', 'bottom-right', 'center'. Choose "
        "the area of the frame least likely to hold a face or the main action, based on "
        "the shot description. Use 'center' only for 'slam' over a calm moment."))


class CalloutPlan(BaseModel):
    accent_color: str = Field(description=(
        "One hex colour like '#FFC400' taken from the film's palette (wardrobe, props, "
        "brand) that will stand out against the scene. Used for every graphic."))
    callouts: list[Callout]


INSTRUCTIONS = """\
You are the motion-graphics editor of a slick, modern infomercial. Choose a few
animated text graphics that visualise the most important keywords. They must
feel designed into the film, not like subtitles.

Rules:
- Be selective. Most segments get no graphic. Aim for about one graphic every
  {every} seconds of video ({target} in total for this {total}s video), and
  avoid graphics in two consecutive segments unless both are essential.
- At most one graphic per segment.
- Pick what a viewer should remember: product names, numbers, prices, offers,
  key benefits, the call to action. Never transcribe sentences.
- Text is 1 to 4 words, at most 22 characters. Rephrase for impact; write
  numbers as digits.
- The anchor must be copied exactly from that segment's narration.
- Vary the placement and style across the video so it feels edited, and keep
  graphics away from faces.
"""


def plan_callouts(client, model: str, segments: list[Segment], shots: dict[int, str],
                  visual_style: str, density: str) -> tuple[str, list[Callout]]:
    """Returns (accent colour, callouts)."""
    from google.genai import types

    total = segments[-1].end
    every = DENSITY_SECONDS[density]
    target = max(1, round(total / every))
    seg_list = "\n".join(
        f"Segment {s.index} ({s.start}-{s.end}s). Shot: {shots.get(s.index, 'n/a')}\n"
        f"  Narration: \"{s.text}\""
        for s in segments
    )
    resp = client.models.generate_content(
        model=model,
        contents=f"FILM LOOK: {visual_style}\n\nSEGMENTS:\n{seg_list}",
        config=types.GenerateContentConfig(
            system_instruction=INSTRUCTIONS.format(every=every, target=target, total=total),
            response_mime_type="application/json",
            response_schema=CalloutPlan,
        ),
    )
    plan = resp.parsed or CalloutPlan.model_validate_json(resp.text)
    plan = CalloutPlan.model_validate(plan.model_dump())
    accent = plan.accent_color if re.fullmatch(r"#?[0-9a-fA-F]{6}", plan.accent_color or "") \
        else DEFAULT_ACCENT

    # Enforce the rules in code too: one per segment, valid style/position.
    seen: set[int] = set()
    valid_idx = {s.index for s in segments}
    out = []
    for c in sorted(plan.callouts, key=lambda c: c.segment_index):
        if c.segment_index in seen or c.segment_index not in valid_idx or not c.text.strip():
            continue
        seen.add(c.segment_index)
        c.style = c.style if c.style in STYLES else "swipe"
        c.position = c.position if c.position in POSITIONS else "bottom-left"
        out.append(c)
    return "#" + accent.lstrip("#").upper(), out


def layout_hint(callout: Callout) -> str:
    """Sentence for the Veo prompt so the shot leaves room for the graphic."""
    return (f"Compose the shot so the {POSITION_AREA[callout.position]} of the frame is "
            "calm, uncluttered background with no faces or important action.")


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

@dataclass
class TimedCallout:
    text: str
    style: str
    position: str
    start: float
    end: float

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(token: str) -> str:
    return re.sub(r"[^\w%$€£]", "", token.lower())


def _find_anchor(anchor: str, words: list[Word], lo: float, hi: float) -> tuple[float, float] | None:
    """Start/end time of the anchor words, preferring matches inside [lo, hi)."""
    target = [_norm(t) for t in anchor.split() if _norm(t)]
    if not target:
        return None
    normed = [_norm(w.text) for w in words]
    matches = [i for i in range(len(words) - len(target) + 1)
               if normed[i:i + len(target)] == target]
    if not matches:
        return None
    inside = [i for i in matches if lo <= words[i].start < hi]
    i = (inside or matches)[0]
    return words[i].start, words[i + len(target) - 1].end


def time_callouts(callouts: list[Callout], words: list[Word], segments: list[Segment],
                  video_length: float) -> list[TimedCallout]:
    by_index = {s.index: s for s in segments}
    timed: list[TimedCallout] = []
    for c in callouts:
        seg = by_index[c.segment_index]
        span = _find_anchor(c.anchor, words, seg.start, seg.end)
        if span is None:
            span = (seg.start + 0.3, seg.start + 0.3)  # anchor not found: early in the clip
        start = max(0.0, span[0] - LEAD_IN)
        end = max(start + MIN_SHOW, span[1] + HOLD_AFTER)
        timed.append(TimedCallout(c.text.strip(), c.style, c.position, start, end))

    timed.sort(key=lambda t: t.start)
    for cur, nxt in zip(timed, timed[1:]):
        cur.end = min(cur.end, nxt.start - GAP)
    for t in timed:
        t.end = min(t.end, video_length)
    return [t for t in timed if t.end - t.start >= 0.6]


# ---------------------------------------------------------------------------
# ASS motion graphics
# ---------------------------------------------------------------------------

def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _ass_color(hex_color: str) -> str:
    """'#RRGGBB' -> ASS '&HBBGGRR&'."""
    h = hex_color.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _text_on(hex_color: str) -> str:
    """Dark or white text, whichever reads better on the accent colour."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "&H141414&" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "&HFFFFFF&"


def _placement(position: str, w: int, h: int) -> tuple[int, int, int]:
    """(numpad alignment, x, y) for a position name."""
    mx, my = round(w * 0.06), round(h * 0.09)
    return {
        "top-left": (7, mx, my), "top-right": (9, w - mx, my),
        "bottom-left": (1, mx, h - my), "bottom-right": (3, w - mx, h - my),
        "center": (5, w // 2, h // 2),
    }[position]


def _events_for(t: TimedCallout, w: int, h: int, accent: str, font: str) -> list[str]:
    base = min(w, h)
    an, x, y = _placement(t.position, w, h)
    dur = int((t.end - t.start) * 1000)
    start, end = _ass_time(t.start), _ass_time(t.end)
    ac = _ass_color(accent)

    def ev(layer: int, tags: str, text: str, style: str = "fx") -> str:
        return f"Dialogue: {layer},{start},{end},{style},,0,0,0,,{{{tags}}}{text}"

    if t.style == "slam":
        size = round(base * (0.21 if len(t.text) <= 8 else 0.15))
        text = _ass_escape(t.text.upper())
        common = (f"\\an{an}\\fn{font}\\b1\\fs{size}\\fsp2\\bord0"
                  f"\\fscx175\\fscy175\\frz-7\\fad(0,220)"
                  f"\\t(0,170,0.6,\\fscx94\\fscy94\\frz-3)\\t(170,280,\\fscx100\\fscy100)"
                  f"\\t(280,{dur},\\fscx105\\fscy105)")
        sx, sy = round(size * 0.05), round(size * 0.07)
        return [
            # Soft drop shadow
            ev(0, f"{common}\\pos({x + sx},{y + sy})\\1c&H000000&\\1a&H50&\\blur{round(size * 0.12)}", text),
            # Accent-coloured text, sharpening as it lands
            ev(1, f"{common}\\pos({x},{y})\\1c{ac}\\3c&H101010&\\bord{max(2, round(size * 0.035))}"
                  f"\\blur10\\t(0,170,\\blur0.6)", text),
        ]

    if t.style == "swipe":
        size = round(base * 0.072)
        pad = round(size * 0.35)
        text = _ass_escape(t.text.upper())
        # Wipe the bar open from the anchored side.
        if an in (7, 1):
            clip0, clip1 = f"\\clip({x - pad},0,{x - pad},{h})", f"\\clip({x - pad},0,{w},{h})"
            slide_from = x - round(size * 0.6)
        elif an in (9, 3):
            clip0, clip1 = f"\\clip({x + pad},0,{x + pad},{h})", f"\\clip(0,0,{x + pad},{h})"
            slide_from = x + round(size * 0.6)
        else:
            clip0, clip1 = f"\\clip({x},0,{x},{h})", f"\\clip(0,0,{w},{h})"
            slide_from = x
        common = f"\\an{an}\\fn{font}\\b1\\fs{size}\\fsp3\\shad0\\fad(0,200)"
        return [
            # Accent bar sized exactly to the text (opaque box style), wiping in.
            ev(0, f"{common}\\pos({x},{y})\\1c{ac}\\3c{ac}\\bord{pad}\\frz-1"
                  f"{clip0}\\t(0,230,0.5,{clip1})", text, "box"),
            # Label sliding in on top of the bar.
            ev(1, f"{common}\\move({slide_from},{y},{x},{y},120,360)\\bord0\\frz-1{clip1}"
                  f"\\1c{_text_on(accent)}\\alpha&HFF&\\t(120,260,\\alpha&H00&)", text),
        ]

    # reveal: letters appear one by one, the line drifts up, an accent bar grows under it.
    size = round(base * 0.09)
    step = max(25, min(60, 700 // max(1, len(t.text))))
    reveal_ms = step * len(t.text) + 140
    letters = "".join(
        f"{{\\alpha&HFF&\\t({i * step},{i * step + 140},\\alpha&H00&)}}{_ass_escape(ch)}"
        for i, ch in enumerate(t.text)
    )
    drift = round(h * 0.015)
    common = (f"\\an{an}\\fn{font}\\b1\\fs{size}\\fsp1\\bord0\\shad0\\fad(0,250)"
              f"\\t(0,{dur},\\fscx104\\fscy104)")
    off = max(2, round(size * 0.04))
    return [
        # Soft shadow that fades in with the letters.
        ev(0, f"{common}\\move({x + off},{y + drift + off},{x + off},{y + off},0,400)"
              f"\\1c&H000000&\\alpha&HFF&\\t(0,{reveal_ms},\\alpha&H60&)"
              f"\\blur{round(size * 0.15)}", _ass_escape(t.text)),
        ev(1, f"{common}\\move({x},{y + drift},{x},{y},0,400)\\1c&HFFFFFF&", letters),
        _underline(an, x, y, size, t.text, ac, h, ev),
    ]


def _underline(an: int, x: int, y: int, size: int, text: str, ac: str, h: int, ev) -> str:
    """Accent bar under a 'reveal' graphic, growing from the anchored side."""
    width = round(len(text) * size * 0.56)  # approximate text width; it is a design accent
    thick = max(4, round(size * 0.09))
    gap = round(size * 0.14)
    if an in (7, 9):
        top = y + size + gap
    elif an == 5:
        top = y + size // 2 + gap
    else:
        top = y + gap
    left = x if an in (7, 1) else (x - width if an in (9, 3) else x - width // 2)
    origin = left if an in (7, 1) else (left + width if an in (9, 3) else left + width // 2)
    shape = f"m 0 0 l {width} 0 {width} {thick} 0 {thick}"
    return ev(1, f"\\an7\\pos({left},{top})\\bord0\\shad0\\1c{ac}\\fad(0,250)"
                 f"\\clip({origin},0,{origin},{h})\\t(150,520,0.5,\\clip({left},0,{left + width},{h}))"
                 f"\\p1", f"{shape}{{\\p0}}")


def write_ass(timed: list[TimedCallout], width: int, height: int, accent: str, font: str) -> str:
    events = []
    for t in timed:
        events.extend(_events_for(t, width, height, accent, font))
    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Neutral base styles; every event sets its own look with override tags.
        # "box" uses BorderStyle 3 so the swipe bar is an opaque box fitted to the text.
        f"Style: fx,{font},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: box,{font},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,3,0,0,7,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ])
