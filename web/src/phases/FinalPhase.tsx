import { fileUrl } from "../api";
import type { PhaseProps } from "../pages/ProjectPage";
import { RunButton, Stale } from "./common";

export default function FinalPhase({ project, reload }: PhaseProps) {
  const url = fileUrl(project.id, "final.mp4", project.final.version);
  return (
    <>
      <div className="panel">
        <h2>6. Final render</h2>
        <p className="muted">Burns the overlays onto the rough cut and lays the voice track over it. No model calls; this takes seconds.</p>
        <Stale project={project} phase="final" />
        <div className="row">
          <RunButton project={project} kind="final" label={project.files.final ? "Render again" : "Render final"} primary reload={reload} disabled={!project.files.rough_cut} />
          {!project.files.rough_cut && <span className="muted">Generate the video first.</span>}
        </div>
      </div>
      {project.files.final && (
        <div className="panel">
          <h2>final.mp4</h2>
          <video controls preload="metadata" src={url} style={{ width: "100%" }} />
          <div className="row" style={{ marginTop: 8 }}>
            <a href={url} download={`${project.name.replace(/[^\w.-]+/g, "_")}.mp4`}><button className="primary">Download</button></a>
          </div>
        </div>
      )}
    </>
  );
}
