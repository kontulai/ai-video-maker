import { useCallback, useEffect, useRef, useState } from "react";
import { errorText, fileUrl, getOverlays, runJob } from "../../api";
import type { PhaseProps } from "../../pages/ProjectPage";
import type { Overlay, OverlaysView } from "../../types";
import { RunButton, isRunning } from "../common";
import Editor from "./Editor";
import "./overlays.css";

export default function OverlaysPhase({ project, reload, goTo }: PhaseProps) {
  const [view, setView] = useState<OverlaysView | null>(null);
  const [error, setError] = useState("");
  const [gen, setGen] = useState(0);
  const [local, setLocal] = useState<Overlay[]>([]);
  const triggered = useRef(false);
  const wasRunning = useRef(false);
  const running = isRunning(project);
  const hasCut = project.files.rough_cut;

  const load = useCallback(async () => {
    try {
      const v = await getOverlays(project.id);
      setView(v);
      setLocal(v.overlays);
      setError("");
    } catch (e) {
      setError(errorText(e));
    }
  }, [project.id]);

  // First load, and a fresh editor after every overlays job (suggestions or preview render).
  useEffect(() => {
    if (!hasCut) return;
    if (wasRunning.current && !running && project.job?.phase === "overlays") {
      void load().then(() => setGen((g) => g + 1));
    } else if (!view) {
      void load();
    }
    wasRunning.current = running;
  }, [hasCut, running, project.job?.phase, load, view]);

  // On the first open with no suggestions yet, ask Gemini once.
  useEffect(() => {
    if (triggered.current || !hasCut || running || project.overlays.version !== 0) return;
    triggered.current = true;
    runJob(project.id, "overlays", { replace_edited: false }).then(() => reload()).catch((e) => setError(errorText(e)));
  }, [hasCut, running, project.overlays.version, project.id, reload]);

  const anyEdited = local.some((o) => o.edited);
  const suggesting = running && project.job?.phase === "overlays";

  return (
    <>
      <div className="panel">
        <div className="row">
          <h2 style={{ margin: 0 }}>5. Overlays</h2>
          <span className="muted">{local.length} overlay{local.length === 1 ? "" : "s"}</span>
          <span className="spacer" />
          {anyEdited ? (
            <>
              <RunButton project={project} kind="overlays" body={{ replace_edited: false }} label="Regenerate untouched suggestions" reload={reload} disabled={!hasCut} />
              <RunButton project={project} kind="overlays" body={{ replace_edited: true }} label="Regenerate all" reload={reload} disabled={!hasCut}
                confirmText="Replace every overlay, including the ones you edited, with new suggestions from Gemini?" />
            </>
          ) : (
            <RunButton project={project} kind="overlays" body={{ replace_edited: false }} label="Regenerate suggestions" reload={reload} disabled={!hasCut} />
          )}
          <RunButton project={project} kind="preview-render" label="Render preview" reload={reload} disabled={!hasCut} />
          <button className="primary" disabled={!hasCut || running} onClick={() => goTo("final")}>Continue to final &rarr;</button>
        </div>
        {!hasCut && <p className="warn" style={{ marginTop: 8 }}>Generate the video first. Overlays are placed on the glued-together picture.</p>}
        {suggesting && <p className="muted" style={{ marginTop: 8 }}>Gemini is suggesting overlays from the narration and shots. The editor opens when it finishes.</p>}
        {error && <p className="error">{error}</p>}
      </div>
      {hasCut && view && !suggesting && <Editor key={gen} project={project} view={view} onOverlays={setLocal} />}
      {project.files.preview_render && (
        <div className="panel">
          <h2>Rendered preview <span className="muted" style={{ fontWeight: "normal", fontSize: 12 }}>the overlays burnt in exactly as the final render will draw them, no voice</span></h2>
          <video controls preload="metadata" src={fileUrl(project.id, "overlay_preview.mp4", project.job?.finished ?? undefined)} style={{ width: "100%" }} />
        </div>
      )}
    </>
  );
}
