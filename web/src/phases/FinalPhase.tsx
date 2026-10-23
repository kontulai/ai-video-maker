import type { PhaseProps } from "../pages/ProjectPage";

export default function FinalPhase({ project }: PhaseProps) {
  return <div className="panel"><h2>FinalPhase</h2><p className="muted">Coming next for {project.name}.</p></div>;
}
