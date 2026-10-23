import { PHASES, PHASE_TITLES, type Phase, type Project } from "../types";

const UPSTREAM: Record<Phase, Phase[]> = {
  setting: [],
  voice: [],
  plan: ["setting", "voice"],
  video: ["plan", "setting"],
  overlays: ["voice", "video"],
  final: ["video", "overlays", "voice"],
};

/** A phase can be opened when every phase it builds on has produced something. */
export function canOpen(project: Project, phase: Phase): boolean {
  return UPSTREAM[phase].every((up) => project.phases[up].status !== "empty");
}

export default function Stepper({ project, current, onSelect }: { project: Project; current: Phase; onSelect: (p: Phase) => void }) {
  const job = project.job?.status === "running" ? project.job : null;
  return (
    <nav className="steps">
      {PHASES.map((phase, i) => {
        const state = project.phases[phase];
        const running = job?.phase === phase || (job?.phase === "overlays" && phase === "overlays");
        const cls = [phase === current ? "current" : state.status, running ? "running" : ""].join(" ");
        const summary = running ? "running..." : state.status === "stale" ? `stale: ${state.reason}` : state.summary || (state.status === "empty" ? "not yet" : "done");
        return (
          <button key={phase} className={cls} disabled={!canOpen(project, phase)} onClick={() => onSelect(phase)} title={state.reason}>
            {i + 1} {PHASE_TITLES[phase]}
            <small>{summary}</small>
          </button>
        );
      })}
    </nav>
  );
}
