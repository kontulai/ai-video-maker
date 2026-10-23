import { fileUrl, patchProject } from "../api";
import type { PhaseProps } from "../pages/ProjectPage";
import { RunButton, SavedTextarea, Stale } from "./common";

export default function TalkTrackPhase({ project, reload, setProject, goTo }: PhaseProps) {
  const save = async (talk_track: string) => setProject(await patchProject(project.id, { talk_track }));
  const words = project.talk_track.trim() ? project.talk_track.trim().split(/\s+/).length : 0;
  return (
    <>
      <div className="panel">
        <h2>2. Talk track</h2>
        <p className="muted">The words the narrator speaks, exactly as they should be said. About 2.5 words a second: {words} words is roughly {Math.round(words / 2.5)} seconds.</p>
        <SavedTextarea value={project.talk_track} onSave={save} rows={10} placeholder="Welcome to the workshop. Today we..." />
        <div className="row">
          <RunButton project={project} kind="voice" label={project.files.voice ? "Regenerate voice" : "Generate voice"} primary reload={reload} disabled={!project.talk_track.trim()} />
          {project.files.voice && <span className="muted">Regenerating makes the plan, video, overlays and final out of date.</span>}
        </div>
      </div>
      {project.files.voice && (
        <div className="panel">
          <h2>Voice track {project.voice_seconds ? <span className="muted" style={{ fontWeight: "normal", fontSize: 12 }}>{project.voice_seconds.toFixed(1)} s</span> : null}</h2>
          <Stale project={project} phase="voice" />
          <audio controls src={fileUrl(project.id, "voice.mp3", project.voice.version)} style={{ width: "100%" }} />
          <div className="row" style={{ marginTop: 8 }}>
            <span className="spacer" />
            <button className="primary" onClick={() => goTo("plan")}>Continue to scene plan &rarr;</button>
          </div>
        </div>
      )}
    </>
  );
}
