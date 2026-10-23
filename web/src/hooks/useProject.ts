import { useCallback, useEffect, useState } from "react";
import { getProject, errorText } from "../api";
import type { Project } from "../types";

/** Loads a project and reloads it on demand (JobLog calls `reload` when a job's stream ends). */
export function useProject(id: string | undefined) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    if (!id) return;
    try {
      setProject(await getProject(id));
      setError("");
    } catch (e) {
      setError(errorText(e));
    }
  }, [id]);

  useEffect(() => {
    setProject(null);
    void reload();
  }, [reload]);

  return { project, error, reload, setProject };
}
