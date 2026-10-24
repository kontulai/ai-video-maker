import { useRef, useState } from "react";
import { deleteReference, errorText, fileUrl, patchProject, uploadReferences } from "../api";
import type { PhaseProps } from "../pages/ProjectPage";
import { RunButton, SavedTextarea } from "./common";

export default function SettingPhase({ project, reload, setProject, goTo }: PhaseProps) {
  const [refError, setRefError] = useState("");
  const [uploading, setUploading] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const save = async (description: string) => setProject(await patchProject(project.id, { description }));
  const pick = async (name: string) => setProject(await patchProject(project.id, { current_preview: name }));
  const attach = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setRefError("");
    try {
      setProject(await uploadReferences(project.id, Array.from(files)));
    } catch (e) {
      setRefError(errorText(e));
    } finally {
      setUploading(false);
      if (picker.current) picker.current.value = "";
    }
  };
  const remove = async (name: string) => {
    setRefError("");
    try {
      setProject(await deleteReference(project.id, name));
    } catch (e) {
      setRefError(errorText(e));
    }
  };
  return (
    <>
      <div className="panel">
        <h2>1. Describe the video</h2>
        <p className="muted">Who is in it, where it happens, what it looks like. The preview image becomes the first frame of the video and tells the planner who and what to show.</p>
        <SavedTextarea value={project.description} onSave={save} placeholder="A bright open-plan office. Ana, a product manager in her thirties, shows a new colleague how to file an expense claim on her monitor..." />
        <label htmlFor="refs-input">Attach pictures <span className="muted" style={{ fontWeight: "normal" }}>of the people, the place or the look you want; the preview and the planner match them</span></label>
        <input id="refs-input" ref={picker} type="file" accept="image/png,image/jpeg,image/webp" multiple disabled={uploading} onChange={(e) => void attach(e.target.files)} />
        {project.references.length > 0 && (
          <div className="refs">
            {project.references.map((name) => (
              <figure key={name}>
                <img src={fileUrl(project.id, name)} alt={name} title={name} />
                <button className="small danger" onClick={() => void remove(name)}>Remove</button>
              </figure>
            ))}
          </div>
        )}
        {uploading && <p className="muted">Uploading...</p>}
        {refError && <p className="error">{refError}</p>}
        <div className="row">
          <RunButton project={project} kind="preview" label={project.previews.length ? "Generate another preview" : "Generate preview"} primary reload={reload} disabled={!project.description.trim()} />
          {!project.description.trim() && <span className="muted">Write a description first.</span>}
        </div>
      </div>
      {project.previews.length > 0 && (
        <div className="panel">
          <h2>Previews <span className="muted" style={{ fontWeight: "normal", fontSize: 12 }}>click one to make it the first frame</span></h2>
          <div className="strip">
            {project.previews.map((name) => (
              <img key={name} src={fileUrl(project.id, name)} alt={name} className={name === project.current_preview ? "current" : ""} onClick={() => pick(name)} title={name} />
            ))}
          </div>
          {project.current_preview && (
            <div style={{ marginTop: 12 }}>
              <img src={fileUrl(project.id, project.current_preview)} alt="current preview" style={{ maxWidth: "100%", borderRadius: 6 }} />
              <div className="row" style={{ marginTop: 8 }}>
                <span className="muted">Current: {project.current_preview}</span>
                <span className="spacer" />
                <button className="primary" onClick={() => goTo("voice")}>Continue to talk track &rarr;</button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
