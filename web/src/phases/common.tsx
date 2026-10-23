import { useEffect, useState } from "react";
import { errorText, runJob, type JobKind } from "../api";
import type { Project } from "../types";

export const isRunning = (p: Project) => p.job?.status === "running";

export function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${r.toFixed(1).padStart(4, "0")}`;
}

/** A button that starts a job and reloads the project so the log and stepper follow. */
export function RunButton({ project, kind, body, label, primary, confirmText, reload, disabled, before }: {
  project: Project; kind: JobKind; body?: unknown; label: string; primary?: boolean; confirmText?: string;
  reload: () => Promise<void> | void; disabled?: boolean;
  /** Runs before the job is started, e.g. to save pending edits the job will read. */
  before?: () => Promise<void>;
}) {
  const [error, setError] = useState("");
  const start = async () => {
    if (confirmText && !window.confirm(confirmText)) return;
    try {
      setError("");
      if (before) await before();
      await runJob(project.id, kind, body);
      await reload();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <>
      <button className={primary ? "primary" : ""} onClick={start} disabled={disabled || isRunning(project)}>{label}</button>
      {error && <span className="error">{error}</span>}
    </>
  );
}

/** A textarea whose value is saved when it loses focus. */
export function SavedTextarea({ value, onSave, placeholder, rows }: { value: string; onSave: (v: string) => Promise<void>; placeholder?: string; rows?: number }) {
  const [text, setText] = useState(value);
  const [state, setState] = useState<"" | "saving" | "saved" | "error">("");
  useEffect(() => setText(value), [value]);
  const save = async () => {
    if (text === value) return;
    setState("saving");
    try {
      await onSave(text);
      setState("saved");
    } catch {
      setState("error");
    }
  };
  return (
    <div>
      <textarea value={text} rows={rows ?? 6} placeholder={placeholder} onChange={(e) => setText(e.target.value)} onBlur={save} />
      <div className="muted" style={{ fontSize: 12, minHeight: 16 }}>
        {text !== value && state !== "saving" ? "Unsaved: click outside the box to save." : state === "saving" ? "Saving..." : state === "saved" ? "Saved." : state === "error" ? <span className="error">Could not save.</span> : ""}
      </div>
    </div>
  );
}

export function Stale({ project, phase }: { project: Project; phase: keyof Project["phases"] }) {
  const st = project.phases[phase];
  if (st.status !== "stale") return null;
  return <p className="warn">This output is out of date ({st.reason}). It is kept until you regenerate it.</p>;
}
