import { useState } from "react";
import { errorText, patchProject } from "../api";
import type { Project, Settings } from "../types";

const FIELDS: { key: keyof Settings; label: string; options?: string[]; min?: number; max?: number; help: string }[] = [
  { key: "aspect", label: "Aspect ratio", options: ["16:9", "9:16"], help: "Of the preview image and every clip." },
  { key: "resolution", label: "Resolution", options: ["360p", "720p", "1080p", "4k"], help: "Of the generated clips." },
  { key: "max_scene", label: "Longest scene (s)", min: 10, max: 30, help: "A scene is one take plus extensions, up to this many seconds." },
  { key: "density", label: "Overlay density", options: ["off", "low", "medium", "high"], help: "How many keyword graphics Gemini suggests." },
  { key: "text_retries", label: "Text-check retries", min: 0, max: 10, help: "Retakes of a clip in which lettering was spotted." },
];

/** The CLI's knobs, kept out of the phases. Each change is saved at once. */
export default function AdvancedDrawer({ project, onChange, onClose }: { project: Project; onChange: (p: Project) => void; onClose: () => void }) {
  const [error, setError] = useState("");
  const save = async (key: keyof Settings, raw: string) => {
    const value = FIELDS.find((f) => f.key === key)?.options ? raw : Number(raw);
    try {
      onChange(await patchProject(project.id, { settings: { [key]: value } }));
      setError("");
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <aside className="drawer">
      <div className="row">
        <h2>Advanced settings</h2>
        <span className="spacer" />
        <button className="small" onClick={onClose}>Close</button>
      </div>
      <p className="muted">Changing these does not regenerate anything until you press a generate button.</p>
      {FIELDS.map((f) => (
        <div key={f.key}>
          <label>{f.label}</label>
          {f.options ? (
            <select value={String(project.settings[f.key])} onChange={(e) => save(f.key, e.target.value)}>
              {f.options.map((o) => <option key={o}>{o}</option>)}
            </select>
          ) : (
            <input type="number" min={f.min} max={f.max} defaultValue={project.settings[f.key]} key={String(project.settings[f.key])} onBlur={(e) => save(f.key, e.target.value)} />
          )}
          <div className="muted" style={{ fontSize: 12 }}>{f.help}</div>
        </div>
      ))}
      {error && <p className="error">{error}</p>}
    </aside>
  );
}
