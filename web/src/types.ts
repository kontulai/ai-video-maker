// Shapes returned by the FastAPI server (server/app.py).

export const PHASES = ["setting", "voice", "plan", "video", "overlays", "final"] as const;
export type Phase = (typeof PHASES)[number];

export const PHASE_TITLES: Record<Phase, string> = {
  setting: "Setting",
  voice: "Talk track",
  plan: "Scene plan",
  video: "Video",
  overlays: "Overlays",
  final: "Final",
};

export interface Settings {
  aspect: "16:9" | "9:16";
  resolution: "360p" | "720p" | "1080p" | "4k";
  max_scene: number;
  density: "off" | "low" | "medium" | "high";
  text_retries: number;
}

export interface PhaseOutput {
  version: number;
  inputs: Record<string, number>;
}

export interface PhaseState {
  status: "empty" | "ready" | "stale";
  reason: string;
  summary: string;
}

export interface Job {
  phase: string;
  status: "running" | "done" | "failed";
  log: string[];
  error: string;
  started: string;
  finished: string | null;
  scenes: number[] | null;
}

export interface SceneState {
  fingerprint: string;
  generation: number;
  previous_generation: number | null;
}

export interface Project {
  id: string;
  name: string;
  created: string;
  updated: string;
  settings: Settings;
  description: string;
  talk_track: string;
  voice_seconds: number | null;
  previews: string[];
  current_preview: string | null;
  references: string[];
  setting: PhaseOutput;
  voice: PhaseOutput;
  plan: PhaseOutput;
  video: PhaseOutput;
  overlays: PhaseOutput;
  final: PhaseOutput;
  scenes: Record<string, SceneState>;
  phases: Record<Phase, PhaseState>;
  job: Job | null;
  files: { voice: boolean; rough_cut: boolean; final: boolean; preview_render: boolean };
}

// -- scene plan ------------------------------------------------------------------------------
export interface Character {
  name: string;
  appearance: string;
  wardrobe: string;
}

export interface Bible {
  visual_style: string;
  setting: string;
  lighting: string;
  characters: Character[];
  layout: string;
}

export interface Shot {
  index: number;
  camera: string;
  action: string;
  expression: string;
  continuity: string;
}

export interface ScenePlan {
  first_segment: number;
  cut: boolean;
  setting: string;
  layout: string;
  reason: string;
}

export interface VideoPlan extends Bible {
  title: string;
  scenes: ScenePlan[];
  shots: Shot[];
}

export interface Segment {
  index: number;
  start: number;
  end: number;
  duration: number;
  text: string;
  scene: number;
  step: number;
  cut: boolean;
}

export interface PromptItem extends Segment {
  prompt: string;
  bible: string;
  source_prompt?: string;
  pinned_for?: string;
}

export interface PlanView {
  plan: VideoPlan | null;
  segments: Segment[];
  items: PromptItem[];
  too_long: number[];
}

// -- video -------------------------------------------------------------------------------------
export interface SceneStep {
  index: number;
  duration: number;
  clip: string | null;
}

export interface SceneView {
  number: number;
  start: number;
  end: number;
  cut: boolean;
  steps: SceneStep[];
  status: "missing" | "stale" | "older_footage" | "ready";
  video: string | null; // the scene's clips stitched into one file, when they all exist
}

export interface VideoView {
  scenes: SceneView[];
  rough_cut: boolean;
}

// -- overlays ------------------------------------------------------------------------------------
export const STYLES = ["slam", "swipe", "reveal", "bullet"] as const;
export type Style = (typeof STYLES)[number];
export const POSITIONS = ["top-left", "top-right", "bottom-left", "bottom-right", "center"] as const;
export type Position = (typeof POSITIONS)[number];

export type OverlayKind = "text" | "image";

export interface Overlay {
  id: string;
  kind: OverlayKind;
  text: string;
  style: Style;
  position: Position;
  list_name: string;
  start: number;
  end: number;
  anchor: string;
  segment_index: number | null;
  edited: boolean;
  /** Text: multiplies the style's size. Image: 1 spans IMAGE_WIDTH of the frame. 0.1 to 5. */
  scale: number;
  /** Degrees clockwise, -180 to 180. */
  rotation: number;
  /** Image overlays: the picture's file in the project folder, overlays/img_1.png. */
  image: string;
  /** A free point as fractions of the frame, 0 to 1; `position` then names the corner of the
   * graphic that sits there. null: the preset's own spot near that corner. */
  x: number | null;
  y: number | null;
}

export const MIN_SCALE = 0.1, MAX_SCALE = 5;
/** An image overlay at scale 1 spans this share of the frame width, like callouts.IMAGE_WIDTH. */
export const IMAGE_WIDTH = 0.3;

export interface Word {
  text: string;
  start: number;
  end: number;
}

export interface OverlaysView {
  accent_color: string;
  overlays: Overlay[];
  words: Word[];
  segments: Segment[];
  duration: number;
}
