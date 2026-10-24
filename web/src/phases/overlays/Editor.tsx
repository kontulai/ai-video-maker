import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type ChangeEvent } from "react";
import { errorText, fileUrl, putOverlays, uploadOverlayImage } from "../../api";
import type { Overlay, OverlaysView, Project } from "../../types";
import { fmtTime } from "../common";
import OverlayLayer from "./OverlayLayer";
import SidePanel from "./SidePanel";
import Timeline from "./Timeline";
import { applyPatch, newId, newImageOverlay, newOverlay, round3 } from "./overlayModel";

type SaveState = "saved" | "dirty" | "saving" | "error";

export interface EditorHandle {
  /** Saves pending edits now; resolves when they are on the server (or the save failed). */
  flush: () => Promise<void>;
}

/** The overlay editor: the rough cut with the overlays drawn on top, a timeline and a side form.
 * Every change is saved to callouts.json 400 ms after the last edit. While `locked` (a job is
 * reading callouts.json) edits are kept and saved when the lock lifts. */
const Editor = forwardRef<EditorHandle, { project: Project; view: OverlaysView; onOverlays: (o: Overlay[]) => void; locked?: boolean }>(
function Editor({ project, view, onOverlays, locked = false }, ref) {
  const [overlays, setOverlays] = useState<Overlay[]>(view.overlays);
  const [accent, setAccent] = useState(view.accent_color);
  const [selected, setSelected] = useState<string | null>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [save, setSave] = useState<SaveState>("saved");
  const [saveError, setSaveError] = useState("");
  const [pictureError, setPictureError] = useState("");
  const video = useRef<HTMLVideoElement>(null);
  const timer = useRef<number>();
  const latest = useRef({ accent, overlays });
  latest.current = { accent, overlays };
  const lockedRef = useRef(locked);
  lockedRef.current = locked;
  const pending = useRef(false);
  useEffect(() => onOverlays(overlays), [overlays, onOverlays]);

  const flush = useCallback(async () => {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    if (lockedRef.current) return; // kept as dirty; saved when the lock lifts
    pending.current = false;
    setSave("saving");
    try {
      await putOverlays(project.id, latest.current.accent, latest.current.overlays);
      setSave("saved");
    } catch (e) {
      setSave("error");
      setSaveError(errorText(e));
    }
  }, [project.id]);
  const schedule = useCallback(() => {
    pending.current = true;
    setSave("dirty");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void flush(), 400);
  }, [flush]);
  useImperativeHandle(ref, () => ({ flush: async () => { if (pending.current) await flush(); } }), [flush]);
  useEffect(() => {
    if (!locked && pending.current && timer.current === undefined) void flush();
  }, [locked, flush]);
  useEffect(() => () => {
    if (pending.current) {
      window.clearTimeout(timer.current);
      void putOverlays(project.id, latest.current.accent, latest.current.overlays).catch(() => undefined);
    }
  }, [project.id]);

  const update = (id: string, patch: Partial<Overlay>) => {
    setOverlays((os) => applyPatch(os, id, patch));
    schedule();
  };
  const place = (o: Overlay) => {
    setOverlays((os) => [...os, o].sort((a, b) => a.start - b.start));
    setSelected(o.id);
    schedule();
  };
  const spanAt = () => Math.max(1, Math.min(4, (view.duration || t + 4) - t));
  const add = () => place(newOverlay(t, spanAt()));
  const addPicture = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setPictureError("");
    try {
      const { image } = await uploadOverlayImage(project.id, file);
      place(newImageOverlay(t, spanAt(), image));
    } catch (err) {
      setPictureError(errorText(err));
    }
  };
  const duplicate = () => {
    const src = overlays.find((o) => o.id === selected);
    if (!src) return;
    const o: Overlay = { ...src, id: newId(), start: round3(src.start + 2), end: round3(src.end + 2), edited: true };
    setOverlays((os) => [...os, o].sort((a, b) => a.start - b.start));
    setSelected(o.id);
    schedule();
  };
  const remove = () => {
    if (!selected) return;
    setOverlays((os) => os.filter((o) => o.id !== selected));
    setSelected(null);
    schedule();
  };
  const changeAccent = (c: string) => {
    setAccent(c);
    schedule();
  };

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      if (video.current) setT(video.current.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);
  const seek = (x: number) => {
    const c = Math.max(0, Math.min(view.duration || x, x));
    if (video.current) video.current.currentTime = c;
    setT(c);
  };
  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) void v.play(); else v.pause();
  };

  const current = overlays.find((o) => o.id === selected) ?? null;
  const saveText = save === "saved" ? "All changes saved." : save === "dirty" ? (locked ? "Unsaved changes, saved when the job finishes." : "Unsaved changes...") : save === "saving" ? "Saving..." : `Could not save: ${saveError}`;

  return (
    <div className="editor">
      <div className="editor-main">
        <div className="stage">
          <video
            ref={video}
            src={fileUrl(project.id, "rough_cut.mp4", project.video.version)}
            preload="auto"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onTimeUpdate={(e) => { if (!playing) setT(e.currentTarget.currentTime); }}
            onClick={toggle}
          />
          <OverlayLayer overlays={overlays} t={t} accent={accent} selected={selected} projectId={project.id} onSelect={setSelected} onChange={update} />
        </div>
        <div className="row transport">
          <button onClick={toggle}>{playing ? "Pause" : "Play"}</button>
          <button className="small" onClick={() => seek(t - 1)} title="Back one second">−1 s</button>
          <button className="small" onClick={() => seek(t + 1)} title="Forward one second">+1 s</button>
          <span className="time">{fmtTime(t)} / {fmtTime(view.duration)}</span>
          <button className="primary" onClick={add}>Add overlay at {fmtTime(t)}</button>
          <label className="file-button" title="Lay a PNG, JPEG or WebP picture over the video at the playhead">
            Add picture
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => void addPicture(e)} />
          </label>
          {pictureError && <span className="error" style={{ fontSize: 12 }}>{pictureError}</span>}
          <span className="spacer" />
          <span className={save === "error" ? "error" : "muted"} style={{ fontSize: 12 }}>{saveText}</span>
          {save === "error" && <button className="small" onClick={() => void flush()}>Retry</button>}
        </div>
        <Timeline duration={view.duration} t={t} words={view.words} segments={view.segments} overlays={overlays} selected={selected}
          onSeek={seek} onSelect={setSelected} onRetime={(id, start, end) => update(id, { start, end })} />
      </div>
      <SidePanel overlay={current} words={view.words} duration={view.duration} accent={accent} projectId={project.id}
        onChange={(patch) => current && update(current.id, patch)} onAccent={changeAccent} onDuplicate={duplicate} onDelete={remove} />
    </div>
  );
});

export default Editor;
