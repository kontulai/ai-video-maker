"""A fake Gemini Omni `interactions` API that renders real (test-pattern) mp4s."""
import base64
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

import make_video as mv


class FakeInteractions:
    """Records each request body. With whole_scene=True an extension returns the whole
    scene so far plus the new part (the other way Omni might answer)."""

    def __init__(self, whole_scene: bool = False, size: str = "320x180"):
        self.bodies, self.whole_scene, self.size = [], whole_scene, size
        self.scene_len: dict[str, float] = {}  # interaction id -> scene length so far
        self._tmp = Path(tempfile.mkdtemp())

    def create(self, **body):
        self.bodies.append(body)
        new = int(re.fullmatch(r"(\d+)s", body["response_format"]["duration"]).group(1))
        prev = body.get("previous_interaction_id")
        scene = self.scene_len.get(prev, 0.0) + new if prev else float(new)
        length = scene if (prev and self.whole_scene) else new
        out = self._tmp / f"take_{len(self.bodies)}.mp4"
        mv.ffmpeg("-f", "lavfi", "-i", f"testsrc=s={self.size}:r=24:d={length}",
                  "-f", "lavfi", "-i", f"sine=frequency=200:duration={length}",
                  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out))
        iid = f"int-{len(self.bodies)}"
        self.scene_len[iid] = scene
        video = SimpleNamespace(data=base64.b64encode(out.read_bytes()).decode(), uri=None)
        # First answer is "in progress" so the polling path runs too.
        self._done = SimpleNamespace(id=iid, status="completed", output_video=video)
        return SimpleNamespace(id=iid, status="in_progress", output_video=None)

    def get(self, id):
        assert id == self._done.id
        return self._done

    def task(self, i: int) -> str:
        return self.bodies[i]["generation_config"]["video_config"]["task"]
