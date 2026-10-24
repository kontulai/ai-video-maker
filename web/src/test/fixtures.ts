import type { Job, PlanView, Project, SceneView, VideoView } from "../types";

/** A project record as the server returns it, with every phase ready and no job. */
export function project(over: Partial<Project> = {}): Project {
  const out = { version: 1, inputs: {} };
  const ready = { status: "ready" as const, reason: "", summary: "done" };
  return {
    id: "p1", name: "Demo", created: "", updated: "",
    settings: { aspect: "16:9", resolution: "720p", max_scene: 8, density: "medium", text_retries: 1 },
    description: "", talk_track: "", voice_seconds: 10, previews: [], current_preview: null,
    setting: out, voice: out, plan: out, video: out, overlays: out, final: out, scenes: {},
    phases: { setting: ready, voice: ready, plan: ready, video: ready, overlays: ready, final: ready },
    job: null,
    files: { voice: true, rough_cut: true, final: false, preview_render: false },
    ...over,
  };
}

export function job(phase: string, status: Job["status"] = "running"): Job {
  return { phase, status, log: [], error: "", started: "", finished: status === "running" ? null : "", scenes: null };
}

export function planView(): PlanView {
  return {
    plan: { title: "T", visual_style: "", setting: "", lighting: "Soft", layout: "", characters: [], scenes: [], shots: [{ index: 1, camera: "wide", action: "a", expression: "e", continuity: "c" }] },
    segments: [{ index: 1, start: 0, end: 4, duration: 4, text: "Hello", scene: 1 }],
    items: [{ index: 1, prompt: "wide shot", pinned_for: "" }],
    too_long: [],
  } as unknown as PlanView;
}

export function videoView(status: SceneView["status"] = "ready"): VideoView {
  const step = (index: number) => ({ index, start: 0, end: 4, duration: 4, clip: status === "missing" ? null : `clips/clip_${index}.mp4` });
  return {
    scenes: [
      { number: 1, start: 0, end: 8, cut: true, steps: [step(1), step(2)], status },
      { number: 2, start: 8, end: 16, cut: false, steps: [step(3), step(4)], status },
    ],
    rough_cut: status === "ready",
  };
}
