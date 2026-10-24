"""Infomercial-style keyword graphics burned on top of the video.

Gemini picks a few moments worth emphasising (most clips get none; a clip that
lists several points can get a stacked bullet list), a placement that avoids the
action, and an accent colour taken from the film's own palette. Each graphic is timed to the exact spoken words using
the ElevenLabs word timings and rendered as animated motion graphics (an ASS
file drawn by libass), so it reads as part of the edit rather than a subtitle.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict

from pydantic import BaseModel, Field

from segmentation import Segment, Word

# Roughly how many seconds of video per graphic, by density setting.
DENSITY_SECONDS = {"low": 9, "medium": 5, "high": 3.5}

STYLES = ("slam", "swipe", "reveal", "bullet")
MAX_PER_SEGMENT = 4
MAX_LIST = 4      # lines in one stacked list
BULLET_POSITIONS = ("top-left", "bottom-left", "center")  # lists stack from the left
POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right", "center")

# Plain-language area names used to tell the video model where to leave room.
POSITION_AREA = {
    "top-left": "upper-left third", "top-right": "upper-right third",
    "bottom-left": "lower-left third", "bottom-right": "lower-right third",
    "center": "centre",
}

# A graphic stays up until the next one needs its part of the frame, at most
# HOLD_AFTER seconds past its words, so with the default density some text is on
# screen nearly all the time. Two graphics may share the screen when one is in
# the upper half and the other in the lower half (a headline over a list).
MIN_SHOW = 3.0     # seconds a graphic stays up at minimum
HOLD_AFTER = 6.0   # seconds it lingers after the anchor words are spoken, at most
LEAD_IN = 0.15     # appear just before the words are spoken
GAP = 0.2          # space between two consecutive graphics in the same area
BULLET_HOLD = 6.0  # a finished list stays up this long after its last point, at most
BULLET_JOIN = 6.0  # unnamed bullets less than this many seconds apart form one list
LIST_JOIN = 20.0   # bullets of the same named list join across this much time
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
        "'reveal': letters appear one by one; best for a key idea or benefit. "
        "'bullet': one point of a list; when the narration lists 2-4 points, give each its "
        "own 'bullet' callout (same position, in spoken order) and they stack up one by "
        "one as they are spoken, then stay on screen together."))
    position: str = Field(description=(
        "One of 'top-left', 'top-right', 'bottom-left', 'bottom-right', 'center'. Choose "
        "the area of the frame least likely to hold a face or the main action, based on "
        "the shot description. Use 'center' only for 'slam' or 'bullet' over a calm moment. "
        "'bullet' lists only use 'top-left', 'bottom-left' or 'center'."))
    list_name: str = Field(default="", description=(
        "When the narration enumerates items ('three things', 'four tiers', 'first... "
        "second... third'), every item is a callout with the same list_name (e.g. 'key "
        "factors'), the same style and the same position, even when the items fall in "
        "different segments. Empty for a graphic that is not part of a list."))


class CalloutPlan(BaseModel):
    accent_color: str = Field(description=(
        "One hex colour like '#FFC400' taken from the film's palette (wardrobe, props, "
        "brand) that will stand out against the scene. Used for every graphic."))
    callouts: list[Callout]


INSTRUCTIONS = """\
You are the motion-graphics editor of a slick, modern infomercial. Choose
animated text graphics that visualise the most important keywords. They must
feel designed into the film, not like subtitles.

Rules:
- Keep text on screen nearly all the time. Each graphic stays up until the next
  one replaces it, so aim for about one graphic every {every} seconds of video
  (about {target} for this {total}s video). Nearly every segment gets one; a
  segment goes without only when nothing in it is worth remembering.
- Use more than one in a segment when it makes sense: a headline ('3 KEY
  FACTORS') and then the points, or when the narration lists several points
  (features, steps, benefits), one 'bullet' per point (2 to {max_list}, each
  anchored on the words that name that point). Never more than {max_per} per
  segment.
- Items of one list look alike. Give every item of an enumeration the same
  list_name, the same style and the same position, even when the items are in
  different segments. Items spoken within about 15 seconds of each other are
  'bullet's that stack up; items further apart are 'swipe's numbered in the
  text ('1. CONTENT-BASED', '2. CONTEXT-BASED').
- Pick what a viewer should remember: product names, numbers, prices, offers,
  key benefits, the call to action. Never transcribe sentences.
- Text is 1 to 4 words, at most 22 characters. Rephrase for impact; write
  numbers as digits.
- The anchor must be copied exactly from that segment's narration.
- Vary the placement and style across the video so it feels edited, and keep
  graphics away from faces. A headline in the upper half of the frame can stay
  up while points appear in the lower half.
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
            system_instruction=INSTRUCTIONS.format(every=every, target=target, total=total,
                                                   max_per=MAX_PER_SEGMENT, max_list=MAX_LIST),
            response_mime_type="application/json",
            response_schema=CalloutPlan,
        ),
    )
    plan = resp.parsed or CalloutPlan.model_validate_json(resp.text)
    plan = CalloutPlan.model_validate(plan.model_dump())
    accent = plan.accent_color if re.fullmatch(r"#?[0-9a-fA-F]{6}", plan.accent_color or "") \
        else DEFAULT_ACCENT
    return "#" + accent.lstrip("#").upper(), enforce_rules(plan.callouts, {s.index for s in segments})


def enforce_rules(callouts: list[Callout], valid_idx: set[int]) -> list[Callout]:
    """The planner's rules, applied in code too: a per-segment cap, no duplicates,
    valid style and position, and every item of a named list in the look of its
    first item."""
    per_seg: dict[int, int] = {}
    seen: set[tuple[int, str]] = set()
    first_of: dict[str, Callout] = {}
    out = []
    for c in sorted(callouts, key=lambda c: c.segment_index):  # stable: keeps spoken order
        key = (c.segment_index, c.text.strip().lower())
        if c.segment_index not in valid_idx or not c.text.strip() or key in seen \
                or per_seg.get(c.segment_index, 0) >= MAX_PER_SEGMENT:
            continue
        seen.add(key)
        per_seg[c.segment_index] = per_seg.get(c.segment_index, 0) + 1
        c.list_name = (c.list_name or "").strip().lower()
        if c.list_name in first_of:
            lead = first_of[c.list_name]
            c.style, c.position = lead.style, lead.position
        else:
            c.style = c.style if c.style in STYLES else "swipe"
            c.position = c.position if c.position in POSITIONS else "bottom-left"
            if c.style == "bullet" and c.position not in BULLET_POSITIONS:
                c.position = c.position.replace("right", "left")
            if c.list_name:
                first_of[c.list_name] = c
        out.append(c)
    return out


def layout_hint(callouts: list[Callout]) -> str:
    """Sentence for the clip prompt so the shot leaves room for the graphics."""
    areas = list(dict.fromkeys(POSITION_AREA[c.position] for c in callouts))
    where = " and the ".join(areas)
    return (f"Compose the shot so the {where} of the frame "
            f"{'is' if len(areas) == 1 else 'are'} calm, uncluttered background with no "
            "faces or important action.")


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
    row: int = 0    # bullet lists: this item's line, and how many lines the list has
    rows: int = 1
    list_name: str = ""
    scale: float = 1.0      # multiplies the style's size
    rotation: float = 0.0   # degrees, clockwise like CSS (ASS \frz counts the other way)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TimedImage:
    """A picture laid over the video by ffmpeg: `scale` 1 spans IMAGE_WIDTH of the frame."""
    path: Path
    position: str
    start: float
    end: float
    scale: float = 1.0
    rotation: float = 0.0   # degrees, clockwise


IMAGE_WIDTH = 0.30
IMAGE_FADE = 0.2


def stack_bullets(timed: list[TimedCallout]) -> list[TimedCallout]:
    """Bullets of one list (same non-empty list_name) whose spans overlap become one list:
    rows in start order, every item in the first item's position. Times never change.
    Used by the web app, where the times in the file are the truth."""
    ordered = sorted(timed, key=lambda t: (t.start, t.end))
    out: list[TimedCallout] = []
    groups: dict[str, list[TimedCallout]] = {}   # open group per list name
    closed: list[list[TimedCallout]] = []
    for t in ordered:
        if t.style != "bullet" or not t.list_name:
            out.append(TimedCallout(**{**asdict(t), "row": 0, "rows": 1}))
            continue
        group = groups.get(t.list_name)
        if group is None or t.start >= max(g.end for g in group):
            group = groups[t.list_name] = []
            closed.append(group)
        item = TimedCallout(**{**asdict(t), "position": (group[0].position if group else t.position),
                               "row": len(group)})
        group.append(item)
        out.append(item)
    for group in closed:
        for item in group:
            item.rows = len(group)
    return sorted(out, key=lambda t: (t.start, t.end))


def _band(position: str) -> str:
    return position.partition("-")[0]   # top, bottom or center


def _conflicts(a: list[TimedCallout], b: list[TimedCallout]) -> bool:
    """Two groups can't share the screen: same half of the frame, or one in the centre."""
    return _band(a[0].position) == _band(b[0].position) \
        or "center" in (a[0].position, b[0].position)


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
        timed.append(TimedCallout(c.text.strip(), c.style, c.position, start, end,
                                  list_name=c.list_name))
    timed.sort(key=lambda t: t.start)

    # Bullets become one list: each line appears when spoken, and the whole list
    # stays up together until a while after the last point. Bullets of one named
    # list join across segments, even with other graphics in between; unnamed
    # consecutive bullets join when spoken close together.
    groups: list[list[TimedCallout]] = []
    for t in timed:
        g = None
        if t.style == "bullet" and t.list_name:
            g = next((g for g in reversed(groups) if g[0].list_name == t.list_name
                      and g[0].style == "bullet"), None)
            if g and (len(g) >= MAX_LIST or t.start - g[-1].start > LIST_JOIN):
                g = None
        elif t.style == "bullet" and groups:
            g = groups[-1]
            if (g[-1].style != "bullet" or g[-1].list_name or len(g) >= MAX_LIST
                    or t.start - g[-1].start > BULLET_JOIN):
                g = None
        if g:
            t.position = g[0].position
            g.append(t)
        else:
            groups.append([t])
    for g in groups:
        if g[0].style == "bullet":
            end = max(g[-1].end - HOLD_AFTER + BULLET_HOLD, g[-1].start + MIN_SHOW)
            for row, t in enumerate(g):
                t.row, t.rows, t.end = row, len(g), end

    # A group stays up until the next group that needs its half of the frame. As
    # there are only two halves, at most two groups are ever up at once.
    for i, cur in enumerate(groups):
        nxt = next((g for g in groups[i + 1:] if _conflicts(cur, g)), None)
        if nxt:
            for t in cur:
                t.end = min(t.end, nxt[0].start - GAP)
    out = []
    for g in groups:
        for t in g:
            t.end = min(t.end, video_length)
        if g[-1].end - g[-1].start >= 0.6 or g[0].style == "bullet" and g[0].end - g[0].start >= 0.6:
            out.extend(t for t in g if t.end - t.start >= 0.4)
    return out


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


def _num(x: float) -> str:
    return f"{x:g}"


def _rad(deg: float) -> str:
    return f"{math.radians(deg):.4f}"


def image_filter_graph(images: list[TimedImage], width: int, height: int, ass: str | None,
                       first_input: int = 2) -> str:
    """An ffmpeg -filter_complex graph that lays each picture (ffmpeg inputs `first_input`
    onwards, in order) over [0:v] at its position, size, angle and time, then burns the
    subtitle file `ass` (a path relative to the working directory) on top; output [v]."""
    mx, my = round(width * 0.06), round(height * 0.09)
    place = {
        "top-left": f"x={mx}:y={my}", "top-right": f"x=W-{mx}-w:y={my}",
        "bottom-left": f"x={mx}:y=H-{my}-h", "bottom-right": f"x=W-{mx}-w:y=H-{my}-h",
        "center": "x=(W-w)/2:y=(H-h)/2",
    }
    chains, last = [], "[0:v]"
    for i, im in enumerate(images):
        w = max(1, round(width * IMAGE_WIDTH * im.scale))
        steps = ["format=rgba", f"scale={w}:-1"]
        if im.rotation:
            r = _rad(im.rotation)
            steps.append(f"rotate={r}:ow=rotw({r}):oh=roth({r}):c=none")
        fade = min(IMAGE_FADE, max(0.0, (im.end - im.start) / 3))
        steps += [f"fade=t=in:st={im.start}:d={fade:g}:alpha=1",
                  f"fade=t=out:st={max(im.start, im.end - fade):g}:d={fade:g}:alpha=1"]
        chains.append(f"[{first_input + i}:v]{','.join(steps)}[i{i}]")
        out = "[v]" if ass is None and i == len(images) - 1 else f"[v{i + 1}]"
        chains.append(f"{last}[i{i}]overlay={place[im.position]}:enable='between(t,{im.start},{im.end})'{out}")
        last = out
    if ass is not None:
        chains.append(f"{last}ass={ass}[v]")
    return ";".join(chains)


def _placement(position: str, w: int, h: int) -> tuple[int, int, int]:
    """(numpad alignment, x, y) for a position name."""
    mx, my = round(w * 0.06), round(h * 0.09)
    return {
        "top-left": (7, mx, my), "top-right": (9, w - mx, my),
        "bottom-left": (1, mx, h - my), "bottom-right": (3, w - mx, h - my),
        "center": (5, w // 2, h // 2),
    }[position]


def _events_for(t: TimedCallout, w: int, h: int, accent: str, font: str, heavy: str) -> list[str]:
    base = min(w, h)
    an, x, y = _placement(t.position, w, h)
    dur = int((t.end - t.start) * 1000)
    start, end = _ass_time(t.start), _ass_time(t.end)
    ac = _ass_color(accent)

    def ev(layer: int, tags: str, text: str, style: str = "fx") -> str:
        return f"Dialogue: {layer},{start},{end},{style},,0,0,0,,{{{tags}}}{text}"

    rot = -t.rotation  # the user's clockwise degrees, in ASS's counter-clockwise terms
    org = f"\\org({x},{y})" if rot else ""   # every part of a turned graphic turns about its anchor
    turn = f"{org}\\frz{_num(rot)}" if rot else ""

    if t.style == "slam":
        size = round(base * (0.21 if len(t.text) <= 8 else 0.15) * t.scale)
        text = _ass_escape(t.text.upper())
        common = (f"\\an{an}\\fn{heavy}\\b0\\fs{size}\\fsp2\\bord0{org}"
                  f"\\fscx175\\fscy175\\frz{_num(rot - 7)}\\fad(0,220)"
                  f"\\t(0,170,0.6,\\fscx94\\fscy94\\frz{_num(rot - 3)})\\t(170,280,\\fscx100\\fscy100)"
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
        size = round(base * 0.072 * t.scale)
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
        tilt = f"{org}\\frz{_num(rot - 1)}"
        common = f"\\an{an}\\fn{font}\\b0\\fs{size}\\fsp3\\shad0\\fad(0,200)"
        return [
            # Accent bar sized exactly to the text (opaque box style), wiping in.
            ev(0, f"{common}\\pos({x},{y})\\1c{ac}\\3c{ac}\\bord{pad}{tilt}"
                  f"{clip0}\\t(0,230,0.5,{clip1})", text, "box"),
            # Label sliding in on top of the bar (a turned label is not clipped to the upright bar).
            ev(1, f"{common}\\move({slide_from},{y},{x},{y},120,360)\\bord0{tilt}{clip1 if not rot else ''}"
                  f"\\1c{_text_on(accent)}\\alpha&HFF&\\t(120,260,\\alpha&H00&)", text),
        ]

    if t.style == "bullet":
        return _bullet(t, w, h, ac, font, ev)

    # reveal: letters appear one by one, the line drifts up, an accent bar grows under it.
    size = round(base * 0.09 * t.scale)
    step = max(25, min(60, 700 // max(1, len(t.text))))
    reveal_ms = step * len(t.text) + 140
    letters = "".join(
        f"{{\\alpha&HFF&\\t({i * step},{i * step + 140},\\alpha&H00&)}}{_ass_escape(ch)}"
        for i, ch in enumerate(t.text)
    )
    drift = round(h * 0.015)
    common = (f"\\an{an}\\fn{font}\\b0\\fs{size}\\fsp1\\bord0\\shad0\\fad(0,250){turn}"
              f"\\t(0,{dur},\\fscx104\\fscy104)")
    off = max(2, round(size * 0.04))
    return [
        # Soft shadow that fades in with the letters.
        ev(0, f"{common}\\move({x + off},{y + drift + off},{x + off},{y + off},0,400)"
              f"\\1c&H000000&\\alpha&HFF&\\t(0,{reveal_ms},\\alpha&H60&)"
              f"\\blur{round(size * 0.15)}", _ass_escape(t.text)),
        ev(1, f"{common}\\move({x},{y + drift},{x},{y},0,400)\\1c&HFFFFFF&", letters),
        _underline(an, x, y, size, t.text, ac, h, ev, turn),
    ]


def _bullet(t: TimedCallout, w: int, h: int, ac: str, font: str, ev) -> list[str]:
    """One line of a stacked list: an accent marker pops in, the text slides in after it."""
    base = min(w, h)
    size = round(base * 0.07 * t.scale)
    line_h = round(size * 1.5)
    mx, my = round(w * 0.06), round(h * 0.09)
    if t.position == "bottom-left":
        cy = h - my - size // 2 - (t.rows - 1 - t.row) * line_h
    elif t.position == "center":
        cy = round(h / 2 + (t.row - (t.rows - 1) / 2) * line_h)
    else:  # top-left
        cy = my + size // 2 + t.row * line_h
    mark = round(size * 0.42)
    tx = mx + mark + round(size * 0.45)
    text = _ass_escape(t.text)
    square = f"m 0 0 l {mark} 0 {mark} {mark} 0 {mark}"
    turn = f"\\org({tx},{cy})\\frz{_num(-t.rotation)}" if t.rotation else ""
    common = f"\\an4\\fn{font}\\b0\\fs{size}\\fsp1\\bord0\\shad0\\fad(0,250){turn}"
    slide = round(size * 0.8)
    off = max(2, round(size * 0.05))
    return [
        # Accent marker: pops in, spinning from 45 degrees to square.
        ev(1, f"\\an5\\pos({mx + mark // 2},{cy})\\bord0\\shad0\\1c{ac}\\fad(0,250){turn}"
              f"\\fscx0\\fscy0\\frz{_num(45 - t.rotation)}\\t(0,220,0.6,\\fscx115\\fscy115\\frz{_num(-t.rotation)})"
              f"\\t(220,320,\\fscx100\\fscy100)\\p1", f"{square}{{\\p0}}"),
        # Soft shadow, then the text, both sliding in from behind the marker.
        ev(0, f"{common}\\move({tx - slide + off},{cy + off},{tx + off},{cy + off},80,380)"
              f"\\clip({mx + mark},0,{w},{h})\\1c&H000000&\\alpha&HFF&\\t(80,380,\\alpha&H60&)"
              f"\\blur{round(size * 0.15)}", text),
        ev(1, f"{common}\\move({tx - slide},{cy},{tx},{cy},80,380)\\clip({mx + mark},0,{w},{h})"
              f"\\1c&HFFFFFF&\\alpha&HFF&\\t(80,300,\\alpha&H00&)", text),
    ]


def _underline(an: int, x: int, y: int, size: int, text: str, ac: str, h: int, ev, turn: str = "") -> str:
    """Accent bar under a 'reveal' graphic, growing from the anchored side."""
    width = round(len(text) * size * 0.5)  # approximate text width; it is a design accent
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
    return ev(1, f"\\an7\\pos({left},{top})\\bord0\\shad0\\1c{ac}\\fad(0,250){turn}"
                 f"\\clip({origin},0,{origin},{h})\\t(150,520,0.5,\\clip({left},0,{left + width},{h}))"
                 f"\\p1", f"{shape}{{\\p0}}")


def write_ass(timed: list[TimedCallout], width: int, height: int, accent: str,
              font: str = "Avenir Next Bold", heavy_font: str | None = None) -> str:
    """font: face for labels; heavy_font: face for 'slam' (defaults to font).
    Name exact faces (e.g. 'Avenir Next Bold'): asking libass for bold on a family
    name can pick the italic face on macOS."""
    heavy = heavy_font or font
    events = []
    for t in timed:
        events.extend(_events_for(t, width, height, accent, font, heavy))
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
        "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: box,{font},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,3,0,0,7,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ])
