import type { Job, OverlaysView, Overlay, PlanView, Project, VideoPlan, VideoView } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* no json body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const base = "/api/projects";

export const listProjects = () => request<Project[]>("GET", base);
export const createProject = (name: string) => request<Project>("POST", base, { name });
export const getProject = (id: string) => request<Project>("GET", `${base}/${id}`);
export const patchProject = (id: string, patch: Partial<Pick<Project, "name" | "description" | "talk_track" | "current_preview">> & { settings?: Partial<Project["settings"]> }) =>
  request<Project>("PATCH", `${base}/${id}`, patch);
export const deleteProject = (id: string) => request<void>("DELETE", `${base}/${id}`);

export type JobKind = "preview" | "voice" | "plan" | "video" | "overlays" | "preview-render" | "final";
export const runJob = (id: string, kind: JobKind, body?: unknown) => request<Job>("POST", `${base}/${id}/run/${kind}`, body ?? {});
export const getJob = (id: string) => request<Job | null>("GET", `${base}/${id}/job`);
export const jobStreamUrl = (id: string) => `${base}/${id}/job/stream`;

export const fileUrl = (id: string, name: string, version?: number | string) =>
  `${base}/${id}/files/${name}${version !== undefined ? `?v=${version}` : ""}`;

export const getPlan = (id: string) => request<PlanView>("GET", `${base}/${id}/plan`);
export const putPlan = (id: string, plan: VideoPlan) => request<PlanView>("PUT", `${base}/${id}/plan`, { plan });
export const putPrompt = (id: string, index: number, prompt: string) => request<PlanView>("PUT", `${base}/${id}/prompts/${index}`, { prompt });

export const getVideo = (id: string) => request<VideoView>("GET", `${base}/${id}/video`);

export const getOverlays = (id: string) => request<OverlaysView>("GET", `${base}/${id}/overlays`);
export const putOverlays = (id: string, accent_color: string, overlays: Overlay[]) =>
  request<OverlaysView>("PUT", `${base}/${id}/overlays`, { accent_color, overlays });

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return String(e);
}
