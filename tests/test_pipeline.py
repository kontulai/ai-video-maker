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
    assert target_length(1.0) == 3
    assert target_length(9.1) == 10
    assert target_length(10.0) == 10


def test_segments_cover_audio_and_use_omni_durations():
    words = words_from_alignment(fake_alignment(SCRIPT))
    duration = words[-1].end + 0.2
    segs = plan_segments(words, duration)
    assert segs[0].start == 0
    assert segs[-1].end >= duration and segs[-1].end - duration < 1
    assert all(3 <= s.duration <= 10 for s in segs)
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
    assert set(cuts) <= {6, 9, 12, 15}, "cuts should land in the clean gaps"


def test_prefers_sentence_ends():
    # Sentences end at ~3.9s and ~7.9s; a cut at 4 and 8 is ideal.
    words = [Word("a", 0.0, 1.0), Word("b.", 1.1, 3.9), Word("c", 4.1, 5.0),
             Word("d.", 5.1, 7.9), Word("e", 8.1, 9.0), Word("f.", 9.1, 11.5)]
    segs = plan_segments(words, 11.7)
    assert [s.end for s in segs][:-1] in ([4], [8])  # 2 steps, cut at a sentence end


def _sentences(n: int, every: int = 8) -> list[Word]:
    """n sentences of steady speech, each ending in a pause around a multiple of `every` s."""
    words = []
    for k in range(n):
        t = k * every + 0.2
        while t + 0.5 < (k + 1) * every - 0.2:
            words.append(Word("w", t, t + 0.5)); t += 0.53
        words[-1].text = "w."
    return words


def test_long_talk_tracks_are_split_into_scenes():
    words = _sentences(12)   # 96 s, sentence ends at 8, 16, 24, ...
    segs = plan_segments(words, words[-1].end + 0.2, max_scene=40)
    scenes = sorted({s.scene for s in segs})
    assert len(scenes) == 3 and scenes == [1, 2, 3]
    for n in scenes:
        part = [s for s in segs if s.scene == n]
        assert sum(s.duration for s in part) <= 40
        assert [s.step for s in part] == list(range(len(part)))
    # A new take starts between sentences, not mid-sentence.
    for a, b in zip(segs, segs[1:]):
        if b.scene != a.scene:
            assert a.end % 8 == 0 and a.text.endswith(".")


def test_short_scene_limit_makes_more_scenes():
    words = words_from_alignment(fake_alignment(SCRIPT))
    one = plan_segments(words, words[-1].end + 0.2)
    many = plan_segments(words, words[-1].end + 0.2, max_scene=10)
    assert one[-1].scene == 1 and many[-1].scene > 1
    assert all(s.duration <= 10 for s in many)


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
    p = mv.build_clip_prompt(plan, plan.shots[0], seg, lip_sync=True)
    for token in ("STYLE", "SETTING", "LIGHT", "APPEAR", "WARD", "CAM", "ACT", "CONT", '"Hi there."'):
        assert token in p
    assert p.index("CAMERA") < p.index("SETTING"), "shot details should come before the bible"
    p2 = mv.build_clip_prompt(plan, plan.shots[0], seg, lip_sync=False)
    assert '"Hi there."' not in p2


def test_prompts_say_new_take_or_extension_and_ban_slop():
    plan = _plan()
    first = mv.Segment(1, 0, 6, 6, "a", scene=1, step=0)
    ext = mv.Segment(2, 6, 14, 8, "b", scene=1, step=1)
    new_scene = mv.Segment(3, 14, 20, 6, "c", scene=2, step=0)
    p1, p2, p3 = (mv.build_clip_prompt(plan, plan.shots[0], s, lip_sync=False)
                  for s in (first, ext, new_scene))
    assert p1.startswith("Single continuous 6-second shot") and "first frame" not in p1
    assert p2.startswith("Extend this video by 8 seconds")
    assert "first frame" in p3.splitlines()[0]
    for p in (p1, p2, p3):
        assert "AVOID:" in p and "objects suddenly appearing" in p
    assert mv.as_fresh_take(p2, 8).startswith("Single continuous 8-second shot")


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


def test_long_prompts_are_condensed_to_fit():
    plan = _plan(appearance="very detailed " * 400, n_shots=2)
    segs = plan_segments(words_from_alignment(fake_alignment(SCRIPT)), 18.4)[:2]
    client = _CondensingClient()
    items = mv.assemble_prompts(client, plan, segs, lip_sync=True)
    assert client.calls == 1
    assert all(len(i["prompt"]) <= mv.MAX_PROMPT_CHARS for i in items)
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


def test_keep_new_part_handles_whole_scene_and_continuation(tmp_path):
    whole = tmp_path / "whole.mp4"   # extension returned the full 6 + 4 s scene
    _make_clip(whole, 10, "red")
    part = tmp_path / "part.mp4"
    assert abs(mv.keep_new_part(whole, part, scene_len=6.0, duration=4) - 10) < 0.1
    assert abs(mv.media_duration(part) - 4) < 0.1

    new_only = tmp_path / "new.mp4"  # extension returned only the new 4 s
    _make_clip(new_only, 4, "blue")
    assert abs(mv.keep_new_part(new_only, part, scene_len=6.0, duration=4) - 10) < 0.1
    assert abs(mv.media_duration(part) - 4) < 0.1

    short = tmp_path / "short.mp4"   # came back short: padded to keep sync
    _make_clip(short, 3, "green")
    mv.keep_new_part(short, part, scene_len=0.0, duration=5)
    assert abs(mv.media_duration(part) - 5) < 0.1


def test_video_requests(tmp_path):
    frame = tmp_path / "f.png"
    mv.ffmpeg("-f", "lavfi", "-i", "color=c=red:s=32x32", "-frames:v", "1", str(frame))
    item = {"index": 2, "duration": 7, "step": 1,
            "prompt": "Extend this video by 7 seconds as the same continuous shot; no cut.\nACTION: x"}
    ext = mv.video_request(item, "16:9", "720p", "int-1", None)
    assert ext["previous_interaction_id"] == "int-1" and ext["input"] == item["prompt"]
    assert ext["response_format"]["duration"] == "7s"
    assert "generation_config" not in ext  # Omni rejects a task with previous_interaction_id
    fresh = mv.video_request(item, "16:9", "720p", None, frame)
    assert "previous_interaction_id" not in fresh
    assert fresh["generation_config"]["video_config"]["task"] == "image_to_video"
    image, text = fresh["input"]
    assert image["type"] == "image" and text["text"].startswith("Single continuous 7-second shot")
    t2v = mv.video_request({**item, "step": 0}, "9:16", "720p", None, None)
    assert t2v["generation_config"]["video_config"]["task"] == "text_to_video"
