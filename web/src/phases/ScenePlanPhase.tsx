import type { PhaseProps } from "../pages/ProjectPage";

export default function ScenePlanPhase({ project }: PhaseProps) {
  return <div className="panel"><h2>ScenePlanPhase</h2><p className="muted">Coming next for {project.name}.</p></div>;
}
