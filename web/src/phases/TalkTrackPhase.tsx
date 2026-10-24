import { useRef, useState } from "react";
import { draftTalkTrack, errorText, fileUrl, patchProject } from "../api";
import type { PhaseProps } from "../pages/ProjectPage";
import { RunButton, SavedTextarea, Stale, isRunning } from "./common";

export default function TalkTrackPhase({ project, reload, setProject, goTo }: PhaseProps) {
  const [pdf, setPdf] = useState<File | null>(null);
  const [notes, setNotes] = useState("");
  const [draftError, setDraftError] = useState("");
  const picker = useRef<HTMLInputElement>(null);
  const save = async (talk_track: string) => setProject(await patchProject(project.id, { talk_track }));
  const words = project.talk_track.trim() ? project.talk_track.trim().split(/\s+/).length : 0;
  const running = isRunning(project);
  const drafting = running && project.job?.phase === "draft";
  const draft = async () => {
    if (!pdf) return;
    setDraftError("");
    try {
      await draftTalkTrack(project.id, pdf, notes);
      setPdf(null);
      if (picker.current) picker.current.value = "";
      await reload();
    } catch (e) {
      setDraftError(errorText(e));
    }
  };
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
      <div className="panel">
        <h2>Draft it from a document <span className="muted" style={{ fontWeight: "normal", fontSize: 12 }}>Gemini writes the narration from a PDF; edit it above afterwards</span></h2>
        <label htmlFor="pdf-input">Source document (PDF)</label>
        <input id="pdf-input" ref={picker} type="file" accept="application/pdf,.pdf" disabled={running} onChange={(e) => setPdf(e.target.files?.[0] ?? null)} />
        <label htmlFor="draft-notes">Notes for the writer</label>
        <input id="draft-notes" type="text" value={notes} disabled={running} placeholder="about one minute, friendly, only the refund part..." onChange={(e) => setNotes(e.target.value)} style={{ width: "100%" }} />
        <div className="row" style={{ marginTop: 8 }}>
          <button className="primary" disabled={!pdf || running} onClick={() => void draft()}>Draft from the PDF</button>
          {project.talk_track.trim() && <span className="muted">The draft replaces the talk track above.</span>}
          {drafting && <span className="muted">Drafting the talk track from the PDF...</span>}
        </div>
        {draftError && <p className="error">{draftError}</p>}
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
