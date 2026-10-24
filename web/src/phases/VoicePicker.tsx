import { useEffect, useState } from "react";
import { errorText, listVoices, patchProject } from "../api";
import type { Project, Voice } from "../types";

const DEFAULT_LABEL = "Default voice (ELEVENLABS_VOICE_ID in the server's .env)";

/** Picks the ElevenLabs narrator voice for the project. The voices come from the account's
 * library; when that cannot be read, a plain voice id can be typed instead. Changing the
 * voice marks the voice track stale until it is regenerated. */
export default function VoicePicker({ project, setProject, disabled }: { project: Project; setProject: (p: Project) => void; disabled: boolean }) {
  const [voices, setVoices] = useState<Voice[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const { voice_id, voice_name } = project.settings;
  const [typed, setTyped] = useState(voice_id);
  useEffect(() => setTyped(voice_id), [voice_id]);
  useEffect(() => {
    let live = true;
    listVoices().then((v) => { if (live) setVoices(v); }, (e) => { if (live) setLoadError(errorText(e)); });
    return () => { live = false; };
  }, []);

  const save = async (id: string, name: string) => {
    if (id === voice_id && name === voice_name) return;
    try {
      setProject(await patchProject(project.id, { settings: { voice_id: id, voice_name: name } }));
      setSaveError("");
    } catch (e) {
      setSaveError(errorText(e));
    }
  };
  const current = voices?.find((v) => v.id === voice_id);
  const missing = voices !== null && voice_id !== "" && !current;

  return (
    <div style={{ marginBottom: 8 }}>
      {loadError ? (
        <>
          <label htmlFor="voice-id">Voice id <span className="muted" style={{ fontWeight: "normal" }}>the voice list could not be loaded: {loadError}</span></label>
          <input id="voice-id" type="text" value={typed} disabled={disabled} placeholder="ElevenLabs voice id; empty for the default"
            onChange={(e) => setTyped(e.target.value)} onBlur={() => void save(typed.trim(), "")} onKeyDown={(e) => { if (e.key === "Enter") void save(typed.trim(), ""); }} />
        </>
      ) : (
        <>
          <label htmlFor="voice-select">Narrator voice</label>
          <div className="row">
            <select id="voice-select" value={voice_id} disabled={disabled || voices === null} style={{ maxWidth: "100%" }}
              onChange={(e) => void save(e.target.value, voices?.find((v) => v.id === e.target.value)?.name ?? "")}>
              <option value="">{DEFAULT_LABEL}</option>
              {missing && <option value={voice_id}>{voice_name || voice_id} (not in your library)</option>}
              {voices?.map((v) => <option key={v.id} value={v.id}>{v.name}{v.description ? ` · ${v.description}` : ""}</option>)}
            </select>
            {voices === null && <span className="muted">Loading voices...</span>}
            {current?.preview_url && <audio controls preload="none" src={current.preview_url} title={`Preview of ${current.name}`} style={{ height: 32 }} />}
          </div>
        </>
      )}
      {saveError && <p className="error">{saveError}</p>}
    </div>
  );
}
