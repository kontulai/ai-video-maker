import json
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_video as mv
from segmentation import Word, plan_segments, target_length, words_from_alignment


def fake_alignment(text: str, wps: float = 2.6) -> dict:
    """Character timings for text spoken at ~wps words/sec, with longer pauses after punctuation."""
    chars, starts, ends, t = [], [], [], 0.2
    per_char = 1 / (wps * 6)
    for ch in text:
        dur = per_char
        if ch in ".!?":
            dur += 0.45
        elif ch == ",":
            dur += 0.2
        chars.append(ch); starts.append(t); ends.append(t + dur); t += dur
    return {"characters": chars, "character_start_times_seconds": starts,
            "character_end_times_seconds": ends}


SCRIPT = ("Welcome to the workshop. Today we build a small wooden bird feeder, "
          "start to finish, with only hand tools. First, measure twice and cut once. "
          "Then sand every edge until it feels smooth. Finally, hang it where you can watch the birds.")


def test_words_from_alignment():
    words = words_from_alignment(fake_alignment("Hello there, friend."))
    assert [w.text for w in words] == ["Hello", "there,", "friend."]
    assert all(w.start < w.end for w in words)


def test_target_length():
    assert target_length(1.0) == 4
    assert target_length(9.1) == 10
    assert target_length(10.0) == 10


def test_segments_cover_audio_and_use_veo_durations():
    words = words_from_alignment(fake_alignment(SCRIPT))
    duration = words[-1].end + 0.2
    segs = plan_segments(words, duration)
    assert segs[0].start == 0
    assert segs[-1].end >= duration and segs[-1].end - duration < 2
    assert all(s.duration in (4, 6, 8) for s in segs)
    assert all(a.end == b.start for a, b in zip(segs, segs[1:]))
    # Every word lands in exactly one segment, in order.
    assert " ".join(s.text for s in segs).split() == [w.text for w in words]


def test_cuts_avoid_middle_of_words_when_possible():
    # Continuous speech except for pauses around 6 s and 12 s.
    words, t = [], 0.0
    for i in range(30):
        start, end = t, t + 0.5
        if any(start < g < end for g in (6, 12)):
            start = int(start) + 0.3 if start % 1 > 0.5 else start
        words.append(Word(f"w{i}", start, end))
        t = end + 0.03
    # Force clean gaps at 6 and 12.
    words = [w for w in words if not (w.start < 6 < w.end or w.start < 12 < w.end)]
    segs = plan_segments(words, words[-1].end + 0.2)
    cuts = [s.end for s in segs[:-1]]
    for c in cuts:
        assert not any(w.start < c < w.end for w in words), f"cut at {c}s splits a word"
    assert 6 in cuts and 12 in cuts


def test_prefers_sentence_ends():
    # Sentences end at ~3.9s and ~7.9s; a cut at 4 and 8 is ideal.
    words = [Word("a", 0.0, 1.0), Word("b.", 1.1, 3.9), Word("c", 4.1, 5.0),
             Word("d.", 5.1, 7.9), Word("e", 8.1, 9.0), Word("f.", 9.1, 11.5)]
    segs = plan_segments(words, 11.7)
    assert [s.end for s in segs][:-1] in ([4, 8], [8])


def _plan(appearance="APPEAR", n_shots=1):
    return mv.VideoPlan(
        title="t", visual_style="STYLE", setting="SETTING", lighting="LIGHT",
        characters=[mv.Character(name="Ana", appearance=appearance, wardrobe="WARD")],
        shots=[mv.Shot(index=i + 1, camera="CAM", action="ACT", expression="EXP",
                       speaker_on_screen=True, speaker_name="Ana", continuity="CONT")
               for i in range(n_shots)],
    )


def test_build_prompt_repeats_bible_and_dialogue():
    plan = _plan()
    seg = plan_segments(words_from_alignment(fake_alignment("Hi there.")), 1.0)[0]
    p = mv.build_veo_prompt(plan, plan.shots[0], seg, lip_sync=True)
    for token in ("STYLE", "SETTING", "LIGHT", "APPEAR", "WARD", "CAM", "ACT", "CONT", '"Hi there."'):
        assert token in p
    assert p.index("CAMERA") < p.index("SETTING"), "shot details should come before the bible"
    p2 = mv.build_veo_prompt(plan, plan.shots[0], seg, lip_sync=False)
    assert '"Hi there."' not in p2


class _CondensingClient:
    """Fake Gemini client whose condense call returns a short bible."""
    def __init__(self):
        self.calls = 0
        outer = self

        class Models:
            def generate_content(self, model, contents, config):
                outer.calls += 1
                assert config.response_schema is mv.Bible
                short = mv.Bible(visual_style="STYLE", setting="SETTING", lighting="LIGHT",
                                 characters=[mv.Character(name="Ana", appearance="short", wardrobe="WARD")])
                return SimpleNamespace(parsed=short, text=short.model_dump_json())
        self.models = Models()


def test_long_prompts_are_condensed_to_fit_veo():
    plan = _plan(appearance="very detailed " * 400, n_shots=2)
    segs = plan_segments(words_from_alignment(fake_alignment(SCRIPT)), 18.4)[:2]
    client = _CondensingClient()
    items = mv.assemble_prompts(client, plan, segs, lip_sync=True)
    assert client.calls == 1
    assert all(len(i["prompt"]) <= mv.VEO_MAX_PROMPT_CHARS for i in items)
    # Both clips still share the exact same character description.
    assert all("- Ana: short Wearing: WARD" in i["prompt"] for i in items)


def test_short_prompts_are_left_alone():
    plan = _plan(n_shots=1)
    segs = plan_segments(words_from_alignment(fake_alignment("Hi there.")), 1.0)
    client = _CondensingClient()
    mv.assemble_prompts(client, plan, segs, lip_sync=True)
    assert client.calls == 0


def _make_clip(path: Path, seconds: int, color: str):
    mv.ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=24:d={seconds}",
              "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))


def test_concat_and_voice(tmp_path):
    clips = []
    for i, (d, c) in enumerate([(4, "red"), (6, "green"), (8, "blue")], 1):
        p = tmp_path / f"clip_{i:02d}.mp4"
        _make_clip(p, d, c)
        clips.append(p)
    voice = tmp_path / "voice.mp3"
    mv.ffmpeg("-f", "lavfi", "-i", "sine=frequency=500:duration=16.3", str(voice))

    frame = mv.extract_last_frame(clips[0], tmp_path / "last.png")
    assert frame.exists() and frame.stat().st_size > 0

    silent = mv.concatenate(clips, tmp_path)
    assert abs(mv.media_duration(silent) - 18) < 0.2
    final = mv.add_voice(silent, voice, tmp_path)
    assert abs(mv.media_duration(final) - 16.8) < 0.2
