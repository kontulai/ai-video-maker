import type { PhaseProps } from "../pages/ProjectPage";

export default function TalkTrackPhase({ project }: PhaseProps) {
  return <div className="panel"><h2>TalkTrackPhase</h2><p className="muted">Coming next for {project.name}.</p></div>;
}
