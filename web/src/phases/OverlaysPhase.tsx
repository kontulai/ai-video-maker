import type { PhaseProps } from "../pages/ProjectPage";

export default function OverlaysPhase({ project }: PhaseProps) {
  return <div className="panel"><h2>OverlaysPhase</h2><p className="muted">Coming next for {project.name}.</p></div>;
}
