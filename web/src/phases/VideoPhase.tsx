import { useCallback, useEffect, useState } from "react";
import { errorText, fileUrl, getVideo } from "../api";
import type { PhaseProps } from "../pages/ProjectPage";
import type { Project, SceneView, VideoView } from "../types";
import { RunButton, fmtTime, isRunning } from "./common";

const STATUS: Record<SceneView["status"], string> = {
  missing: "not generated",
  stale: "stale: prompts changed",
  older_footage: "continued from older footage",
  ready: "ready",
};

/** Asks what was wrong or should change before a scene (or the whole video) is regenerated.
 * The answer goes with the job: Gemini works it into the shots of the scenes being redone
 * (and into the production bible when the whole video is redone) before the clips are made.
 * An empty answer regenerates with the same prompts. */
function FeedbackForm({ project, scenes, whole, reload, onClose }: {
  project: Project; scenes: number[]; whole: boolean; reload: () => Promise<void> | void; onClose: () => void;
}) {
  const [text, setText] = useState("");
  const feedback = text.trim();
  // With feedback the whole video is sent as "every scene" so the bible may change too;
  // without it the scenes are named so every one is redone even though nothing changed.
  const body = { scenes: whole && feedback ? null : scenes, feedback };
  return (
    <div className="feedback" style={{ marginTop: 8 }}>
      <label>What was wrong, or what should change?{whole ? " Every scene is regenerated." : ` Scene ${scenes[0]} is regenerated.`}</label>
      <textarea rows={3} autoFocus value={text} onChange={(e) => setText(e.target.value)}
        placeholder="What was wrong, or what should change? For example: she looks grim, make her smile. Leave empty to roll again with the same prompts." />
      <p className="muted" style={{ fontSize: 12, margin: "4px 0 8px" }}>
        Gemini writes the change into the shots of {whole ? "every scene" : "this scene"} (you can see and edit the result on the Scene plan page)
        {whole ? ", and into the production bible when it is about the look, the setting, the light or a character" : ""}, then the clips are made again.
      </p>
      <div className="row">
        <RunButton project={project} kind="video" body={body} label={feedback ? "Apply and regenerate" : "Regenerate as is"} primary
          reload={async () => { onClose(); await reload(); }} />
        <button onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

export default function VideoPhase({ project, reload, goTo }: PhaseProps) {
  const [view, setView] = useState<VideoView | null>(null);
  const [error, setError] = useState("");
  const [asking, setAsking] = useState<number | "all" | null>(null);
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
                {scene.status === "missing" ? (
                  <RunButton project={project} kind="video" body={{ scenes: [scene.number] }} label="Generate" reload={reload} />
                ) : (
                  <button disabled={isRunning(project) || asking === scene.number} onClick={() => setAsking(scene.number)}>Regenerate</button>
                )}
              </div>
              {asking === scene.number && <FeedbackForm project={project} scenes={[scene.number]} whole={false} reload={reload} onClose={() => setAsking(null)} />}
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
            {view && !pending && view.rough_cut ? (
              <button className="primary" disabled={isRunning(project) || asking === "all"} onClick={() => setAsking("all")}>Regenerate all</button>
            ) : (
              <RunButton project={project} kind="video" label={pending && pending < (view?.scenes.length ?? 0) ? `Generate ${pending} remaining` : "Generate all"} primary reload={reload} disabled={!view?.scenes.length} />
            )}
          </div>
          {asking === "all" && view && <FeedbackForm project={project} scenes={view.scenes.map((s) => s.number)} whole reload={reload} onClose={() => setAsking(null)} />}
          {view && !pending && view.rough_cut && asking !== "all" && <p className="muted" style={{ fontSize: 12 }}>Every scene is up to date. Regenerate a scene above to change it, or the whole video here.</p>}
        </div>
        <div className="panel">
          <button className="primary" disabled={!view?.rough_cut || isRunning(project)} onClick={() => goTo("overlays")}>Continue to overlays &rarr;</button>
          {project.phases.video.status === "stale" && <p className="warn" style={{ marginTop: 8 }}>The plan changed since this video was made ({project.phases.video.reason}).</p>}
        </div>
      </div>
    </div>
  );
}
