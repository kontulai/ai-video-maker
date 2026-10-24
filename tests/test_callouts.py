"""Keyword graphics: anchor timing, overlap handling and the ASS output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import callouts as fx
from segmentation import Segment, Word


def _words(text, start=0.0, step=0.5):
    return [Word(t, start + i * step, start + i * step + 0.4) for i, t in enumerate(text.split())]


WORDS = _words("Get fifty percent off today. Free shipping on every single order, guaranteed now.")
SEGS = [Segment(1, 0, 4, 4, ""), Segment(2, 4, 8, 4, "")]


def _c(seg, anchor, text="X", style="slam", position="center", list_name=""):
    return fx.Callout(segment_index=seg, text=text, anchor=anchor, style=style,
                      position=position, list_name=list_name)


def test_starts_just_before_anchor_words_and_lingers():
    [t] = fx.time_callouts([_c(1, "fifty percent,")], WORDS, SEGS, 12)
    assert abs(t.start - (0.5 - fx.LEAD_IN)) < 1e-6
    assert abs(t.end - (1.4 + fx.HOLD_AFTER)) < 1e-6   # stays up well after the words


def test_stays_up_until_the_next_graphic_needs_its_area():
    # Same half of the frame: the first clears just before the second appears.
    a, b = fx.time_callouts([_c(1, "Get", position="top-left"),
                             _c(2, "guaranteed", position="top-right")], WORDS, SEGS, 12)
    assert abs(a.end - (b.start - fx.GAP)) < 1e-6
    # Other half: a headline stays up over the point below it.
    a, b = fx.time_callouts([_c(1, "Get", position="top-left"),
                             _c(2, "guaranteed", position="bottom-left")], WORDS, SEGS, 12)
    assert a.end > b.start + 1
    # The centre shares the screen with nothing.
    a, b = fx.time_callouts([_c(1, "Get", position="top-left"),
                             _c(2, "guaranteed", position="center")], WORDS, SEGS, 12)
    assert abs(a.end - (b.start - fx.GAP)) < 1e-6


def test_headline_stays_over_successive_points():
    a, b, c = fx.time_callouts([_c(1, "Get", position="top-left"),
                                _c(1, "today.", position="bottom-left"),
                                _c(2, "every", position="bottom-right")], WORDS, SEGS, 12)
    assert a.end > c.start + 1                         # the headline outlasts both points
    assert abs(b.end - (c.start - fx.GAP)) < 1e-6      # same half: replaced
    assert abs(a.end - (0.4 + fx.HOLD_AFTER)) < 1e-6   # until its hold runs out


def test_long_list_is_not_cut_by_graphics_in_the_other_half():
    words = _words("one two three four", step=4.0)
    segs = [Segment(1, 0, 16, 16, "")]
    timed = fx.time_callouts([_c(1, "one", style="bullet", position="bottom-left", list_name="t"),
                              _c(1, "two", style="reveal", position="top-left"),
                              _c(1, "three", style="bullet", position="bottom-left", list_name="t"),
                              _c(1, "four", style="reveal", position="top-right")], words, segs, 20)
    rows = [t for t in timed if t.style == "bullet"]
    assert [(t.row, t.rows) for t in rows] == [(0, 2), (1, 2)]
    assert rows[0].end == rows[1].end >= 12


def test_prefers_match_inside_its_segment_and_falls_back():
    words = _words("order now order now order now order now order now")  # repeats every second
    [t] = fx.time_callouts([_c(2, "order now")], words, SEGS, 8)
    assert t.start >= 4 - fx.LEAD_IN
    [t] = fx.time_callouts([_c(2, "not spoken")], WORDS, SEGS, 8)
    assert abs(t.start - (4 + 0.3 - fx.LEAD_IN)) < 1e-6


def test_no_overlap_and_clipped_to_video_end():
    timed = fx.time_callouts([_c(1, "Get"), _c(1, "fifty"), _c(2, "guaranteed")], WORDS, SEGS, 6.5)
    for a, b in zip(timed, timed[1:]):
        assert a.end <= b.start - fx.GAP + 1e-9
    assert timed[-1].end <= 6.5


def test_bullets_stack_and_stay_up_together():
    cs = [_c(1, "fifty", "Half price", "bullet", "top-left"),
          _c(1, "shipping", "Free shipping", "bullet", "bottom-left"),
          _c(2, "guaranteed", "Guaranteed", "bullet", "top-left"),
          _c(2, "now.", "ORDER NOW", "slam", "center")]
    words = _words("Get fifty percent off. Free shipping too. And guaranteed. "
                   "So what are you waiting for? Order now.")
    timed = fx.time_callouts(cs, words, SEGS, 10)
    bullets = [t for t in timed if t.style == "bullet"]
    assert [(t.row, t.rows) for t in bullets] == [(0, 3), (1, 3), (2, 3)]
    assert {t.position for t in bullets} == {"top-left"}      # the list shares one place
    assert len({t.end for t in bullets}) == 1                # and leaves together
    assert bullets[0].start < bullets[1].start < bullets[2].start
    slam = timed[-1]
    assert bullets[-1].end <= slam.start - fx.GAP + 1e-9     # cleared before the next graphic


def test_far_apart_bullets_are_separate_lists_unless_named():
    words = _words("one " + "pad " * 20 + "two")
    segs = [Segment(1, 0, 8, 8, ""), Segment(2, 8, 12, 4, "")]
    timed = fx.time_callouts([_c(1, "one", style="bullet"), _c(2, "two", style="bullet")],
                             words, segs, 12)
    assert [t.rows for t in timed] == [1, 1]
    timed = fx.time_callouts([_c(1, "one", style="bullet", list_name="factors"),
                              _c(2, "two", style="bullet", list_name="factors")], words, segs, 12)
    assert [(t.row, t.rows) for t in timed] == [(0, 2), (1, 2)]   # one list across segments
    close = _words("one two", step=3.0)
    timed = fx.time_callouts([_c(1, "one", style="bullet", list_name="factors"),
                              _c(1, "two", style="bullet", list_name="tiers")], close, segs, 12)
    assert [t.rows for t in timed] == [1, 1]                      # different lists never join
    timed = fx.time_callouts([_c(1, "one", style="bullet"), _c(1, "two", style="bullet")],
                             close, segs, 12)
    assert [t.rows for t in timed] == [2, 2]                      # unnamed, close: one list


def test_items_of_a_named_list_share_the_first_items_look():
    out = fx.enforce_rules([
        _c(2, "a", "3 KEY FACTORS", "slam", "top-left"),
        _c(2, "b", "Sensitivity", "reveal", "bottom-left", "Key Factors"),
        _c(3, "c", "Business value", "bullet", "top-left", "key factors"),
        _c(3, "d", "Regulations", "swipe", "center", "key factors"),
        _c(4, "e", "Public", "bullet", "bottom-right", "tiers"),
        _c(5, "f", "Internal", "slam", "top-right", "tiers"),
    ], {2, 3, 4, 5})
    assert [(c.style, c.position, c.list_name) for c in out[1:4]] == \
        [("reveal", "bottom-left", "key factors")] * 3
    assert [(c.style, c.position) for c in out[4:]] == [("bullet", "bottom-left")] * 2
    assert (out[0].style, out[0].position, out[0].list_name) == ("slam", "top-left", "")


def test_layout_hint_names_every_area():
    hint = fx.layout_hint([_c(1, "a", position="top-left"), _c(1, "b", position="top-left"),
                           _c(1, "c", position="bottom-right")])
    assert "upper-left third and the lower-right third" in hint and " are " in hint


def test_ass_has_every_style_and_accent_color():
    timed = [fx.TimedCallout("50% OFF", "slam", "center", 0, 2),
             fx.TimedCallout("Free {shipping}", "swipe", "bottom-left", 2.5, 4),
             fx.TimedCallout("Feel it", "reveal", "top-right", 4.5, 6),
             fx.TimedCallout("Fast", "bullet", "top-left", 6.5, 8, 0, 2),
             fx.TimedCallout("Quiet", "bullet", "top-left", 7, 8, 1, 2)]
    ass = fx.write_ass(timed, 1280, 720, "#FFC400", "Avenir Next Bold", "Avenir Next Heavy")
    assert "PlayResX: 1280" in ass and "PlayResY: 720" in ass
    assert "&H00C4FF&" in ass                         # accent as BGR
    assert "{shipping}" not in ass.split("[Events]")[1].replace("{\\", "")  # braces escaped
    assert ",box," in ass                             # swipe bar uses the box style
    assert "\\p1" in ass                              # reveal underline is a drawing
    assert ass.count("Dialogue:") == 7 + 2 * 3
    assert "\\fnAvenir Next Heavy" in ass and "\\b1" not in ass  # exact faces, no faux bold
    assert "0:00:02.50" in ass


def test_scale_and_rotation_change_the_text_tags():
    plain = fx.write_ass([fx.TimedCallout("Free", "swipe", "bottom-left", 0, 2)], 1280, 720, "#FFC400")
    big = fx.write_ass([fx.TimedCallout("Free", "swipe", "bottom-left", 0, 2, scale=2.0)], 1280, 720, "#FFC400")
    assert f"\\fs{round(720 * 0.072)}" in plain and f"\\fs{round(720 * 0.072 * 2)}" in big
    turned = fx.write_ass([fx.TimedCallout("Free", "swipe", "bottom-left", 0, 2, rotation=10)], 1280, 720, "#FFC400")
    assert "\\frz-11" in turned and "\\frz-1" in plain          # the style's own tilt plus the user's turn
    reveal = fx.write_ass([fx.TimedCallout("Feel it", "reveal", "top-right", 0, 2, rotation=-20)], 1280, 720, "#FFC400")
    events = [l for l in reveal.splitlines() if l.startswith("Dialogue:")]
    assert all("\\frz20" in l and "\\org(" in l for l in events)  # text, shadow and underline turn together
    bullets = fx.write_ass([fx.TimedCallout("Fast", "bullet", "top-left", 0, 2, rotation=5)], 1280, 720, "#FFC400")
    assert bullets.count("\\org(") == 3


def test_free_placement_puts_the_anchor_at_the_given_point():
    # x and y are fractions of the frame; the preset keeps saying which corner of the text sits there
    free = fx.write_ass([fx.TimedCallout("Free", "swipe", "top-right", 0, 2, x=0.5, y=0.25)], 1280, 720, "#FFC400")
    assert "\\pos(640,180)" in free and "\\an9" in free
    slam = fx.write_ass([fx.TimedCallout("Go", "slam", "bottom-left", 0, 2, x=0.1, y=0.9, rotation=5)], 1280, 720, "#FFC400")
    assert "\\pos(128,648)" in slam and "\\org(128,648)" in slam
    bullets = fx.write_ass([fx.TimedCallout("Fast", "bullet", "top-left", 0, 2, 0, 2, x=0.4, y=0.5),
                            fx.TimedCallout("Quiet", "bullet", "top-left", 0, 2, 1, 2, x=0.4, y=0.5)], 1280, 720, "#FFC400")
    size = round(720 * 0.07)
    assert f"\\pos({512 + round(size * 0.42) // 2},{360 + size // 2})" in bullets        # first marker at the point
    assert f",{360 + size // 2 + round(size * 1.5)})" in bullets                            # second row below it
    preset = fx.write_ass([fx.TimedCallout("Free", "swipe", "top-right", 0, 2)], 1280, 720, "#FFC400")
    assert "\\pos(1203,65)" in preset                                                     # unchanged without a point


def test_stacked_bullets_take_the_first_items_point():
    timed = fx.stack_bullets([fx.TimedCallout("A", "bullet", "top-left", 0, 3, list_name="l", x=0.3, y=0.2),
                              fx.TimedCallout("B", "bullet", "bottom-left", 1, 3, list_name="l", x=0.8, y=0.9)])
    assert [(t.row, t.position, t.x, t.y) for t in timed] == [(0, "top-left", 0.3, 0.2), (1, "top-left", 0.3, 0.2)]


def test_image_filter_graph_places_scales_rotates_and_times_pictures():
    imgs = [fx.TimedImage(Path("a.png"), "top-right", 1.0, 4.0),
            fx.TimedImage(Path("b.png"), "center", 2.0, 5.5, scale=0.5, rotation=90)]
    graph = fx.image_filter_graph(imgs, 1280, 720, ass="callouts.ass")
    chains = graph.split(";")
    assert chains[0].startswith("[2:v]") and "scale=384:-1" in chains[0]        # 30 % of the width
    assert "[3:v]" in graph and "scale=192:-1" in graph                           # half of that
    assert "rotate=" not in chains[0] and f"rotate={fx._rad(90)}" in graph        # straight pictures are not rotated
    assert "between(t,1.0,4.0)" in graph and "between(t,2.0,5.5)" in graph
    assert "x=W-77-w:y=65" in graph                                               # top-right, inside the margins
    assert "x=(W-w)/2:y=(H-h)/2" in graph
    free = fx.image_filter_graph([fx.TimedImage(Path("a.png"), "bottom-right", 0, 1, x=0.75, y=0.5),
                                  fx.TimedImage(Path("b.png"), "center", 0, 1, x=0.1, y=0.2)], 1280, 720, ass=None)
    assert "x=960-w:y=360-h" in free and "x=128-w/2:y=144-h/2" in free
    assert graph.rstrip().endswith("ass=callouts.ass[v]")
    assert fx.image_filter_graph(imgs[:1], 1280, 720, ass=None).rstrip().endswith("[v]") and "ass=" not in fx.image_filter_graph(imgs[:1], 1280, 720, ass=None)
