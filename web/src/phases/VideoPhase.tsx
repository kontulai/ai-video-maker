import type { PhaseProps } from "../pages/ProjectPage";

export default function VideoPhase({ project }: PhaseProps) {
  return <div className="panel"><h2>VideoPhase</h2><p className="muted">Coming next for {project.name}.</p></div>;
}
