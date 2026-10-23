import { useCallback, useEffect, useRef, useState } from "react";
import { errorText, getPlan, putPlan, putPrompt } from "../api";
import type { PhaseProps } from "../pages/ProjectPage";
import type { PlanView, ScenePlan, Segment, VideoPlan } from "../types";
import { RunButton, Stale, fmtTime } from "./common";

const BIBLE_FIELDS: { key: "title" | "visual_style" | "setting" | "lighting" | "layout"; label: string }[] = [
  { key: "title", label: "Title" },
  { key: "visual_style", label: "Visual style" },
  { key: "setting", label: "Setting" },
  { key: "lighting", label: "Lighting" },
  { key: "layout", label: "Layout (where people and things are in the frame)" },
];

/** Scenes as groups of segments, from the plan's scene breaks. */
function groupScenes(plan: VideoPlan, segments: Segment[]): { scene: ScenePlan | null; segments: Segment[] }[] {
  const starts = new Map(plan.scenes.map((s) => [s.first_segment, s]));
  const out: { scene: ScenePlan | null; segments: Segment[] }[] = [];
  for (const seg of segments) {
    if (out.length === 0 || starts.has(seg.index)) out.push({ scene: starts.get(seg.index) ?? null, segments: [] });
    out[out.length - 1].segments.push(seg);
  }
  return out;
}

export default function ScenePlanPhase({ project, reload, goTo }: PhaseProps) {
  const [view, setView] = useState<PlanView | null>(null);
  const [plan, setPlan] = useState<VideoPlan | null>(null);
  const [dirty, setDirty] = useState(false);
  const [showPrompts, setShowPrompts] = useState(false);
  const [error, setError] = useState("");
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  // The server refuses plan edits while a plan or video job reads or writes the same files.
  const locked = project.job?.status === "running" && (project.job.phase === "plan" || project.job.phase === "video");

  const load = useCallback(async () => {
    try {
      const v = await getPlan(project.id);
      setView(v);
      setPlan(v.plan);
      setDirty(false);
    } catch (e) {
      setError(errorText(e));
    }
  }, [project.id]);
  // Follow the project record (a new plan, a finished job) unless edits are pending: they stay until saved or discarded.
  useEffect(() => {
    if (!dirtyRef.current) void load();
  }, [load, project.plan.version, project.job?.status]);

  const edit = (fn: (p: VideoPlan) => void) => {
    if (!plan) return;
    const next: VideoPlan = JSON.parse(JSON.stringify(plan));
    fn(next);
    setPlan(next);
    setDirty(true);
  };
  const save = async () => {
    if (!plan) return;
    try {
      const v = await putPlan(project.id, plan);
      setView(v);
      setPlan(v.plan);
      setDirty(false);
      setError("");
      await reload();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const savePrompt = async (index: number, prompt: string) => {
    try {
      setView(await putPrompt(project.id, index, prompt));
      setError("");
      await reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const hasPlan = !!view?.plan;
  return (
    <>
      <div className="panel">
        <h2>3. Scene plan</h2>
        <p className="muted">The narration is cut into steps of up to {project.settings.max_scene} seconds and Gemini writes the production bible, the scenes and a shot for each step. Saving edits rebuilds the prompts without a model call.</p>
        <Stale project={project} phase="plan" />
        <div className="row">
          {hasPlan ? (
            <RunButton project={project} kind="plan" label="Regenerate from scratch" reload={reload} confirmText="Throw away this plan and its edits and ask Gemini again?" />
          ) : (
            <RunButton project={project} kind="plan" label="Generate plan" primary reload={reload} disabled={!project.files.voice} />
          )}
          {!project.files.voice && <span className="muted">Generate the voice track first.</span>}
          <span className="spacer" />
          {hasPlan && <button className="primary" disabled={dirty} onClick={() => goTo("video")}>Continue to video &rarr;</button>}
        </div>
        {locked && <p className="warn">The {project.job?.phase} job is running. Edits can be saved when it has finished.</p>}
        {error && <p className="error">{error}</p>}
      </div>

      {plan && view && (
        <>
          <div className="panel">
            <h2>Production bible <span className="muted" style={{ fontWeight: "normal", fontSize: 12 }}>repeated in every prompt; a change marks every scene stale</span></h2>
            {BIBLE_FIELDS.map((f) => (
              <div key={f.key}>
                <label>{f.label}</label>
                <textarea rows={2} value={plan[f.key]} onChange={(e) => edit((p) => { p[f.key] = e.target.value; })} />
              </div>
            ))}
            <label>Characters</label>
            {plan.characters.map((c, i) => (
              <div className="row" key={i} style={{ alignItems: "flex-start", marginBottom: 6 }}>
                <input type="text" style={{ flex: "0 0 120px" }} value={c.name} placeholder="Name" onChange={(e) => edit((p) => { p.characters[i].name = e.target.value; })} />
                <textarea rows={2} style={{ flex: 2, minHeight: 0 }} value={c.appearance} placeholder="Appearance" onChange={(e) => edit((p) => { p.characters[i].appearance = e.target.value; })} />
                <textarea rows={2} style={{ flex: 2, minHeight: 0 }} value={c.wardrobe} placeholder="Wardrobe" onChange={(e) => edit((p) => { p.characters[i].wardrobe = e.target.value; })} />
                <button className="small danger" onClick={() => edit((p) => { p.characters.splice(i, 1); })}>Remove</button>
              </div>
            ))}
            <button className="small" onClick={() => edit((p) => { p.characters.push({ name: "", appearance: "", wardrobe: "" }); })}>Add character</button>
          </div>

          {groupScenes(plan, view.segments).map((group, gi) => {
            const first = group.segments[0];
            const last = group.segments[group.segments.length - 1];
            const scene = group.scene;
            return (
              <div className="panel" key={first.index}>
                <h2>
                  Scene {gi + 1}
                  <span className="muted" style={{ fontWeight: "normal", fontSize: 12 }}>{fmtTime(first.start)} to {fmtTime(last.end)} · {group.segments.length} step{group.segments.length === 1 ? "" : "s"}</span>
                </h2>
                {gi > 0 && scene && (
                  <div className="row">
                    <label style={{ margin: 0 }}>Starts with</label>
                    <select value={scene.cut ? "cut" : "continue"} onChange={(e) => edit((p) => { const s = p.scenes.find((x) => x.first_segment === scene.first_segment)!; s.cut = e.target.value === "cut"; })}>
                      <option value="continue">continues from the last frame</option>
                      <option value="cut">a cut to a new setup</option>
                    </select>
                    {scene.reason && <span className="muted">{scene.reason}</span>}
                  </div>
                )}
                {gi > 0 && scene?.cut && (
                  <div className="grid2">
                    <div><label>Setting of this cut</label><textarea rows={2} value={scene.setting} onChange={(e) => edit((p) => { p.scenes.find((x) => x.first_segment === scene.first_segment)!.setting = e.target.value; })} /></div>
                    <div><label>Layout of this cut</label><textarea rows={2} value={scene.layout} onChange={(e) => edit((p) => { p.scenes.find((x) => x.first_segment === scene.first_segment)!.layout = e.target.value; })} /></div>
                  </div>
                )}
                {group.segments.map((seg, si) => {
                  const shot = plan.shots.find((s) => s.index === seg.index);
                  const item = view.items.find((it) => it.index === seg.index);
                  return (
                    <div className="step" key={seg.index}>
                      <div className="row">
                        <b>Step {seg.index}</b>
                        <span className="muted">{seg.duration} s · {fmtTime(seg.start)} to {fmtTime(seg.end)}</span>
                        <span className="spacer" />
                        {si > 0 && (
                          <button className="small" onClick={() => edit((p) => { p.scenes.push({ first_segment: seg.index, cut: true, setting: "", layout: "", reason: "" }); p.scenes.sort((a, b) => a.first_segment - b.first_segment); })}>Break scene here</button>
                        )}
                        {si === 0 && gi > 0 && (
                          <button className="small" onClick={() => edit((p) => { p.scenes = p.scenes.filter((x) => x.first_segment !== seg.index); })}>Join with previous scene</button>
                        )}
                      </div>
                      <div className="narration">“{seg.text}”</div>
                      {shot ? (
                        <div className="grid2">
                          <div><label>Camera</label><textarea rows={2} value={shot.camera} onChange={(e) => edit((p) => { p.shots.find((s) => s.index === seg.index)!.camera = e.target.value; })} /></div>
                          <div><label>Action</label><textarea rows={2} value={shot.action} onChange={(e) => edit((p) => { p.shots.find((s) => s.index === seg.index)!.action = e.target.value; })} /></div>
                          <div><label>Expression</label><textarea rows={2} value={shot.expression} onChange={(e) => edit((p) => { p.shots.find((s) => s.index === seg.index)!.expression = e.target.value; })} /></div>
                          <div><label>Continuity</label><textarea rows={2} value={shot.continuity} onChange={(e) => edit((p) => { p.shots.find((s) => s.index === seg.index)!.continuity = e.target.value; })} /></div>
                        </div>
                      ) : (
                        <p className="warn">No shot for this step. Regenerate the plan.</p>
                      )}
                      {view.too_long.includes(seg.index) && <p className="warn">This step's prompt is longer than the model accepts. Shorten the bible or the shot.</p>}
                      {showPrompts && item && <PromptEditor index={seg.index} prompt={item.prompt} pinned={!!item.pinned_for} disabled={locked} onSave={savePrompt} />}
                    </div>
                  );
                })}
              </div>
            );
          })}

          <div className="panel row" style={{ position: "sticky", bottom: 8 }}>
            <button className="primary" onClick={save} disabled={!dirty || locked}>{dirty ? "Save plan" : "Plan saved"}</button>
            {dirty && <button onClick={load}>Discard edits</button>}
            <span className="spacer" />
            <label style={{ margin: 0 }}><input type="checkbox" checked={showPrompts} onChange={(e) => setShowPrompts(e.target.checked)} /> Show prompts</label>
          </div>
        </>
      )}
    </>
  );
}

function PromptEditor({ index, prompt, pinned, disabled, onSave }: { index: number; prompt: string; pinned: boolean; disabled: boolean; onSave: (i: number, p: string) => Promise<void> }) {
  const [text, setText] = useState(prompt);
  useEffect(() => setText(prompt), [prompt]);
  return (
    <div style={{ marginTop: 8 }}>
      <label>Prompt sent to the video model {pinned && <span className="badge pinned">pinned: kept as written until its shot or the bible changes</span>}</label>
      <textarea className="prompt" rows={8} value={text} onChange={(e) => setText(e.target.value)} />
      <button className="small" disabled={text === prompt || disabled} onClick={() => onSave(index, text)}>Save prompt</button>
    </div>
  );
}
