import { useCallback, useEffect, useState } from "react";
import { errorText, fileUrl, getVideo } from "../api";
import type { PhaseProps } from "../pages/ProjectPage";
import type { SceneView, VideoView } from "../types";
import { RunButton, fmtTime, isRunning } from "./common";

const STATUS: Record<SceneView["status"], string> = {
  missing: "not generated",
  stale: "stale: prompts changed",
  older_footage: "continued from older footage",
  ready: "ready",
};

export default function VideoPhase({ project, reload, goTo }: PhaseProps) {
  const [view, setView] = useState<VideoView | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => getVideo(project.id).then(setView).catch((e) => setError(errorText(e))), [project.id]);
  useEffect(() => { void load(); }, [load, project.video.version, project.job?.status, project.plan.version]);

  const job = project.job?.status === "running" && project.job.phase === "video" ? project.job : null;
  const pending = view?.scenes.filter((s) => s.status !== "ready").length ?? 0;
  const v = `${project.video.version}-${project.job?.finished ?? ""}`;

  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="panel">
          <h2>4. Video</h2>
          <p className="muted">Each scene is one take plus extensions from the video model, reviewed for lettering, continuing from the previous scene's last frame or cutting. Regenerate any scene until it looks good.</p>
          {error && <p className="error">{error}</p>}
        </div>
        {view?.scenes.map((scene) => {
          const busy = job && (job.scenes === null || job.scenes.includes(scene.number));
          return (
            <div className="scene-card" key={scene.number}>
              <div className="row">
                <b>Scene {scene.number}</b>
                <span className="muted">{fmtTime(scene.start)} to {fmtTime(scene.end)} · {scene.steps.length} clip{scene.steps.length === 1 ? "" : "s"} · {scene.number > 1 ? (scene.cut ? "cut" : "continued") : "first frame"}</span>
                <span className={`status ${scene.status}`}>{busy ? "generating..." : STATUS[scene.status]}</span>
                <span className="spacer" />
                <RunButton project={project} kind="video" body={{ scenes: [scene.number] }} label={scene.status === "missing" ? "Generate" : "Regenerate"} reload={reload} />
              </div>
              <div className="clips">
                {scene.steps.map((st) => st.clip ? (
                  <div key={st.index}>
                    <video controls preload="metadata" src={fileUrl(project.id, st.clip, v)} />
                    <div className="muted" style={{ fontSize: 12 }}>Step {st.index} · {st.duration} s</div>
                  </div>
                ) : (
                  <div key={st.index} className="muted" style={{ fontSize: 12, padding: 8, border: "1px dashed var(--line)", borderRadius: 6 }}>Step {st.index} · {st.duration} s · no clip yet</div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ flex: "0 0 300px" }}>
        <div className="panel">
          <h2>Whole video</h2>
          {view?.rough_cut ? (
            <video controls preload="metadata" src={fileUrl(project.id, "rough_cut.mp4", v)} style={{ width: "100%" }} />
          ) : (
            <p className="muted">The rough cut appears when every scene has its clips.</p>
          )}
          <div className="row" style={{ marginTop: 8 }}>
            <RunButton project={project} kind="video" label={pending && pending < (view?.scenes.length ?? 0) ? `Generate ${pending} remaining` : "Generate all"} primary reload={reload} disabled={!view?.scenes.length || (!pending && !!view?.rough_cut)} />
          </div>
          {view && !pending && view.rough_cut && <p className="muted" style={{ fontSize: 12 }}>Every scene is up to date. Regenerate a scene above to change it.</p>}
        </div>
        <div className="panel">
          <button className="primary" disabled={!view?.rough_cut || isRunning(project)} onClick={() => goTo("overlays")}>Continue to overlays &rarr;</button>
          {project.phases.video.status === "stale" && <p className="warn" style={{ marginTop: 8 }}>The plan changed since this video was made ({project.phases.video.reason}).</p>}
        </div>
      </div>
    </div>
  );
}
