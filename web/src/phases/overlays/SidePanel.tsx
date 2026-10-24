import { fileUrl } from "../../api";
import { MAX_SCALE, MIN_SCALE, POSITIONS, STYLES, type Overlay, type Position, type Style, type Word } from "../../types";
import { BULLET_POSITIONS, MIN_LENGTH, anchorOf, round3, snapToWord, wordAt } from "./overlayModel";

const STYLE_HELP: Record<Style, string> = {
  slam: "huge text that crashes in; 1 to 2 words",
  swipe: "label on a colour bar, like a lower third",
  reveal: "letters appear one by one, underlined",
  bullet: "one point of a list; same list name stacks",
};

export default function SidePanel({ overlay, words, duration, accent, projectId, onChange, onAccent, onDuplicate, onDelete }: {
  overlay: Overlay | null; words: Word[]; duration: number; accent: string; projectId: string;
  onChange: (patch: Partial<Overlay>) => void; onAccent: (c: string) => void; onDuplicate: () => void; onDelete: () => void;
}) {
  return (
    <div className="side-panel">
      <div className="panel">
        <label>Accent colour for the whole video</label>
        <div className="row">
          <input type="color" value={accent} onChange={(e) => onAccent(e.target.value)} />
          <span className="muted">{accent}</span>
        </div>
      </div>
      {overlay ? <Fields overlay={overlay} words={words} duration={duration} projectId={projectId} onChange={onChange} onDuplicate={onDuplicate} onDelete={onDelete} /> : (
        <div className="panel muted">Click an overlay in the picture or on the timeline to edit it, or add one at the playhead. Drag the selected overlay anywhere in the picture, its corner handle to resize it and the handle above it to turn it.</div>
      )}
    </div>
  );
}

function Fields({ overlay: o, words, duration, projectId, onChange, onDuplicate, onDelete }: {
  overlay: Overlay; words: Word[]; duration: number; projectId: string;
  onChange: (patch: Partial<Overlay>) => void; onDuplicate: () => void; onDelete: () => void;
}) {
  const word = wordAt(o.start, words);
  const setStart = (v: number) => {
    if (!Number.isFinite(v)) return;
    const start = Math.max(0, Math.min(o.end - MIN_LENGTH, v));
    onChange({ start: round3(start) });
  };
  const setEnd = (v: number) => {
    if (!Number.isFinite(v)) return;
    onChange({ end: round3(Math.max(o.start + MIN_LENGTH, Math.min(duration || v, v))) });
  };
  const setScale = (percent: number) => {
    if (!Number.isFinite(percent) || percent < MIN_SCALE * 100 || percent > MAX_SCALE * 100) return;
    onChange({ scale: Math.round(percent) / 100 });
  };
  const setRotation = (deg: number) => {
    if (!Number.isFinite(deg) || deg < -180 || deg > 180) return;
    onChange({ rotation: Math.round(deg * 10) / 10 });
  };
  const anchor = anchorOf(o);
  const free = o.x !== null && o.y !== null;
  const setPoint = (axis: "x" | "y", percent: number) => {
    if (!Number.isFinite(percent) || percent < 0 || percent > 100) return;
    onChange({ ...anchor, [axis]: Math.round(percent * 10) / 1000 });
  };
  const picture = o.kind === "image";
  const positions: readonly Position[] = !picture && o.style === "bullet" ? BULLET_POSITIONS : POSITIONS;
  return (
    <div className="panel">
      {picture ? (
        <>
          <label>Picture</label>
          <img className="side-picture" src={fileUrl(projectId, o.image)} alt="overlay picture" />
        </>
      ) : (
        <>
          <label>Text</label>
          <textarea rows={2} value={o.text} onChange={(e) => onChange({ text: e.target.value })} />
        </>
      )}
      <div className="grid2">
        <div>
          <label>Start (s)</label>
          <input type="number" step={0.1} min={0} value={o.start} onChange={(e) => setStart(parseFloat(e.target.value))} />
        </div>
        <div>
          <label>End (s)</label>
          <input type="number" step={0.1} min={0} value={o.end} onChange={(e) => setEnd(parseFloat(e.target.value))} />
        </div>
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
        {word ? <>Appears on “{word.text}” · {(o.end - o.start).toFixed(1)} s long · </> : null}
        <button className="small" onClick={() => setStart(snapToWord(o.start, words).time)} disabled={!words.length}>Snap start to a word</button>
      </div>
      <div className="grid2">
        <div>
          <label htmlFor={`ov-size-${o.id}`}>Size (%)</label>
          <input id={`ov-size-${o.id}`} type="number" step={5} min={MIN_SCALE * 100} max={MAX_SCALE * 100} value={Math.round(o.scale * 100)} onChange={(e) => setScale(parseFloat(e.target.value))} />
        </div>
        <div>
          <label htmlFor={`ov-rot-${o.id}`}>Rotation (°)</label>
          <input id={`ov-rot-${o.id}`} type="number" step={1} min={-180} max={180} value={o.rotation} onChange={(e) => setRotation(parseFloat(e.target.value))} />
        </div>
      </div>
      {!picture && (
        <>
          <label>Style</label>
          <div className="choices">
            {STYLES.map((s) => (
              <button key={s} className={`small ${o.style === s ? "on" : ""}`} title={STYLE_HELP[s]}
                onClick={() => onChange({ style: s, position: s === "bullet" && !BULLET_POSITIONS.includes(o.position) ? "top-left" : o.position })}>{s}</button>
            ))}
          </div>
        </>
      )}
      <label>Position {!picture && o.style === "bullet" && <span>(lists stack from the left)</span>}</label>
      <div className="choices">
        {POSITIONS.map((p) => (
          <button key={p} className={`small ${o.position === p && !free ? "on" : ""}`} disabled={!positions.includes(p)} onClick={() => onChange({ position: p, x: null, y: null })}>{p}</button>
        ))}
      </div>
      <div className="grid2">
        <div>
          <label htmlFor={`ov-x-${o.id}`}>X (%)</label>
          <input id={`ov-x-${o.id}`} type="number" step={1} min={0} max={100} value={Math.round(anchor.x * 100)} onChange={(e) => setPoint("x", parseFloat(e.target.value))} />
        </div>
        <div>
          <label htmlFor={`ov-y-${o.id}`}>Y (%)</label>
          <input id={`ov-y-${o.id}`} type="number" step={1} min={0} max={100} value={Math.round(anchor.y * 100)} onChange={(e) => setPoint("y", parseFloat(e.target.value))} />
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
        {free ? `Placed freely: the ${o.position === "center" ? "centre" : o.position.replace("-", " ")} of the ${picture ? "picture" : "text"} sits at this point. A preset above puts it back.` : "The preset's spot. Drag the overlay or type a point to place it anywhere."}
      </p>
      {!picture && (
        <>
          <label>List name {o.style !== "bullet" && <span>(bullets only)</span>}</label>
          <input type="text" value={o.list_name} placeholder="bullets with the same name stack into one list" onChange={(e) => onChange({ list_name: e.target.value })} disabled={o.style !== "bullet"} />
        </>
      )}
      {o.anchor && <p className="muted" style={{ fontSize: 12 }}>Suggested for the words “{o.anchor}”{o.edited ? ", edited by you" : ""}.</p>}
      <div className="row" style={{ marginTop: 12 }}>
        <button onClick={onDuplicate}>Duplicate (+2 s)</button>
        <span className="spacer" />
        <button className="danger" onClick={onDelete}>Delete</button>
      </div>
    </div>
  );
}
