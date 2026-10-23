import type { PhaseProps } from "../pages/ProjectPage";

export default function SettingPhase({ project }: PhaseProps) {
  return <div className="panel"><h2>SettingPhase</h2><p className="muted">Coming next for {project.name}.</p></div>;
}
