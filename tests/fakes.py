"""Fakes for the model APIs: an Omni `interactions` API that renders real (test-pattern)
mp4s, and a Gemini client whose planner, clip review and prompt fitting answer offline."""
import base64
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

import callouts as fx
import make_video as mv


class FakeInteractions:
    """Records each request body. With whole_scene=True an extension returns the whole
    scene so far plus the new part (the other way Omni might answer)."""

    def __init__(self, whole_scene: bool = False, size: str = "320x180"):
        self.bodies, self.whole_scene, self.size = [], whole_scene, size
        self.scene_len: dict[str, float] = {}  # interaction id -> scene length so far
        self._tmp = Path(tempfile.mkdtemp())

    def create(self, **body):
        if body.get("previous_interaction_id") and "generation_config" in body:
            raise ValueError("previous_interaction_id is not allowed when video task is set.")
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
        if "previous_interaction_id" in self.bodies[i]:
            return "extend"
        return self.bodies[i]["generation_config"]["video_config"]["task"]


class FakeGemini:
    """Stands in for `google.genai.Client`. `plan(contents) -> VideoPlan` answers the planner,
    clip reviews report `in_frame` with no lettering, prompt fits append ", fitted" to the
    action, `callouts(contents) -> CalloutPlan` answers the overlay planner and `image`
    (png bytes) answers an image request. `omni` serves the interactions API."""

    def __init__(self, plan=None, omni: FakeInteractions | None = None, callouts=None,
                 image: bytes | None = None, in_frame: str = "Ana centre, bench frame-left",
                 seen: list | None = None, revise=None, draft=None, judge=None):
        self.plan, self.omni, self.callouts, self.image = plan, omni, callouts, image
        self.judge = judge  # judge(contents) -> evals.judge.Judgement scores a result in the evals
        self.revise = revise  # revise(contents) -> PlanRevision answers feedback on the video
        self.draft = draft  # draft(contents) -> TalkTrackDraft answers a talk track drafted from a PDF
        self.in_frame, self.seen = in_frame, [] if seen is None else seen
        self.checks = self.fits = self.images = 0
        self.image_requests: list = []  # the contents of each image request

    @property
    def client(self):
        fake = self

        class Client:
            def __init__(self, **kw):
                self.models, self.interactions = fake, fake.omni
        return Client

    def generate_content(self, model, contents, config):
        schema = getattr(config, "response_schema", None)
        if schema is mv.ClipReview:
            self.checks += 1
            return _answer(mv.ClipReview(amount="none", where="", in_frame=self.in_frame))
        if schema is mv.Shot:
            self.fits += 1
            shot = mv.Shot.model_validate_json(contents.partition("PLANNED SHOT:\n")[2])
            return _answer(shot.model_copy(update={"action": shot.action + ", fitted"}))
        if schema is fx.CalloutPlan:
            self.seen.append(contents)
            return _answer(self.callouts(contents))
        if schema is mv.VideoPlan:
            self.seen.append(contents)
            return _answer(self.plan(contents))
        if schema is mv.PlanRevision:
            self.seen.append(contents)
            return _answer(self.revise(contents))
        if schema is getattr(mv, "TalkTrackDraft", None):
            self.seen.append(contents)
            return _answer(self.draft(contents))
        if getattr(schema, "__name__", "") == "Judgement":
            self.seen.append(contents)
            return _answer(self.judge(contents))
        if "IMAGE" in (getattr(config, "response_modalities", None) or []):
            self.images += 1
            self.image_requests.append(contents)
            self.seen.append(config.image_config.aspect_ratio)
            part = SimpleNamespace(text=None, inline_data=SimpleNamespace(mime_type="image/png", data=self.image))
            return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])
        raise AssertionError(f"unexpected Gemini request: {schema} {config}")


def _answer(model):
    return SimpleNamespace(parsed=model, text=model.model_dump_json())
