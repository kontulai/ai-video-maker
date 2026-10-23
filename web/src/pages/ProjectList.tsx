import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { createProject, deleteProject, errorText, listProjects, patchProject } from "../api";
import { PHASES, PHASE_TITLES, type Project } from "../types";

export default function ProjectList() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const load = () => listProjects().then(setProjects).catch((e) => setError(errorText(e)));
  useEffect(() => { void load(); }, []);

  const create = async () => {
    if (!name.trim()) return;
    try {
      await createProject(name.trim());
      setName("");
      await load();
    } catch (e) { setError(errorText(e)); }
  };
  const rename = async (p: Project) => {
    const next = window.prompt("Project name", p.name);
    if (next && next.trim() && next !== p.name) {
      await patchProject(p.id, { name: next.trim() });
      await load();
    }
  };
  const remove = async (p: Project) => {
    if (!window.confirm(`Delete "${p.name}" and every file it made?`)) return;
    await deleteProject(p.id);
    await load();
  };

  return (
    <div className="page">
      <h1>Video projects</h1>
      <div className="panel row">
        <input type="text" placeholder="New project name" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} style={{ flex: 1 }} />
        <button className="primary" onClick={create} disabled={!name.trim()}>Create</button>
      </div>
      {error && <p className="error">{error}</p>}
      {projects === null ? <p className="muted">Loading...</p> : projects.length === 0 ? <p className="muted">No projects yet. Create one above.</p> : (
        <div className="cards">
          {projects.map((p) => (
            <div className="card" key={p.id}>
              <Link to={`/projects/${p.id}`} className="title">{p.name}</Link>
              <div className="dots" title={PHASES.map((ph) => `${PHASE_TITLES[ph]}: ${p.phases[ph].status}`).join("\n")}>
                {PHASES.map((ph) => <span key={ph} className={p.phases[ph].status} />)}
              </div>
              <div className="muted" style={{ fontSize: 12 }}>
                {p.job?.status === "running" ? `Running: ${p.job.phase}` : `Updated ${p.updated.replace("T", " ").slice(0, 16)}`}
              </div>
              <div className="row" style={{ marginTop: 8 }}>
                <Link to={`/projects/${p.id}`}><button className="small">Open</button></Link>
                <button className="small" onClick={() => rename(p)}>Rename</button>
                <span className="spacer" />
                <button className="small danger" onClick={() => remove(p)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
