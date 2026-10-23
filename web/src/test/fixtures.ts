import type { Job, PlanView, Project } from "../types";

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
