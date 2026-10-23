import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { deleteProject, patchProject } from "../api";
import AdvancedDrawer from "../components/AdvancedDrawer";
import JobLog from "../components/JobLog";
import Stepper, { canOpen } from "../components/Stepper";
import { useProject } from "../hooks/useProject";
import { PHASES, type Phase, type Project } from "../types";
import SettingPhase from "../phases/SettingPhase";
import TalkTrackPhase from "../phases/TalkTrackPhase";
import ScenePlanPhase from "../phases/ScenePlanPhase";
import VideoPhase from "../phases/VideoPhase";
import OverlaysPhase from "../phases/OverlaysPhase";
import FinalPhase from "../phases/FinalPhase";

export interface PhaseProps {
  project: Project;
  reload: () => Promise<void> | void;
  setProject: (p: Project) => void;
  goTo: (phase: Phase) => void;
}

const PANELS: Record<Phase, (props: PhaseProps) => JSX.Element> = {
  setting: SettingPhase,
  voice: TalkTrackPhase,
  plan: ScenePlanPhase,
  video: VideoPhase,
  overlays: OverlaysPhase,
  final: FinalPhase,
};

/** The first phase that still needs work: the first empty or stale one, else the last. */
function suggestPhase(p: Project): Phase {
  return PHASES.find((ph) => p.phases[ph].status !== "ready") ?? "final";
}

export default function ProjectPage() {
  const { id, phase } = useParams();
  const navigate = useNavigate();
  const { project, error, reload, setProject } = useProject(id);
  const [renaming, setRenaming] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const current = (PHASES as readonly string[]).includes(phase ?? "") ? (phase as Phase) : null;

  useEffect(() => {
    if (project && (!current || !canOpen(project, current))) navigate(`/projects/${project.id}/${suggestPhase(project)}`, { replace: true });
  }, [project, current, navigate]);

  // while a job runs, poll the project so phase states follow even if the stream drops
  useEffect(() => {
    if (project?.job?.status !== "running") return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [project?.job?.status, reload]);

  if (error) return <div className="page"><p className="error">{error}</p><Link to="/">Back to projects</Link></div>;
  if (!project || !current) return <div className="page muted">Loading...</div>;

  const goTo = (ph: Phase) => navigate(`/projects/${project.id}/${ph}`);
  const rename = async (name: string) => {
    setRenaming(false);
    if (name.trim() && name.trim() !== project.name) setProject(await patchProject(project.id, { name: name.trim() }));
  };
  const remove = async () => {
    if (!window.confirm(`Delete "${project.name}" and every file it made?`)) return;
    await deleteProject(project.id);
    navigate("/");
  };
  const Panel = PANELS[current];

  return (
    <div>
      <header className="bar">
        <Link to="/">&larr; Projects</Link>
        {renaming ? (
          <input className="name" autoFocus defaultValue={project.name} onBlur={(e) => rename(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") rename(e.currentTarget.value); if (e.key === "Escape") setRenaming(false); }} />
        ) : (
          <span className="name" onDoubleClick={() => setRenaming(true)}>{project.name}</span>
        )}
        <button className="small" onClick={() => setRenaming(true)}>Rename</button>
        <button className="small" onClick={() => setAdvanced(true)}>Advanced</button>
        <button className="small danger" onClick={remove}>Delete</button>
      </header>
      <Stepper project={project} current={current} onSelect={goTo} />
      <div className="body">
        <div className="main">
          <Panel project={project} reload={reload} setProject={setProject} goTo={goTo} />
        </div>
        <div className="side">
          <JobLog project={project} reload={reload} />
        </div>
      </div>
      {advanced && <AdvancedDrawer project={project} onChange={setProject} onClose={() => setAdvanced(false)} />}
    </div>
  );
}
