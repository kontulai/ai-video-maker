import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OverlaysView } from "../../types";
import { job, project } from "../../test/fixtures";
import OverlaysPhase from "./OverlaysPhase";

vi.mock("../../api", () => ({
  getOverlays: vi.fn(),
  putOverlays: vi.fn(),
  runJob: vi.fn(),
  fileUrl: () => "about:blank",
  errorText: (e: unknown) => String(e),
}));
import { getOverlays, putOverlays, runJob } from "../../api";

const view = (): OverlaysView => ({ accent_color: "#00ff00", overlays: [], words: [], segments: [], duration: 10 });
const noop = () => undefined;
const props = (p = project()) => ({ project: p, reload: noop, setProject: noop, goTo: noop });
const calls: string[] = [];

beforeEach(() => {
  vi.clearAllMocks();
  calls.length = 0;
  vi.mocked(getOverlays).mockResolvedValue(view());
  vi.mocked(putOverlays).mockImplementation(async () => { calls.push("put"); return view(); });
  vi.mocked(runJob).mockImplementation(async (_id, kind) => { calls.push(`run:${kind}`); return job(kind, "running"); });
});

describe("OverlaysPhase", () => {
  it("saves pending edits before starting a suggestions or preview-render job", async () => {
    render(<OverlaysPhase {...props()} />);
    fireEvent.click(await screen.findByText(/Add overlay at/));
    fireEvent.click(screen.getByText("Regenerate untouched suggestions"));
    await waitFor(() => expect(calls).toContain("run:overlays"));
    expect(calls.indexOf("put")).toBeGreaterThanOrEqual(0);
    expect(calls.indexOf("put")).toBeLessThan(calls.indexOf("run:overlays"));
  });

  it("keeps the editor open and defers saving while the preview render runs", async () => {
    const { rerender } = render(<OverlaysPhase {...props(project({ job: job("preview-render") }))} />);
    const add = await screen.findByText(/Add overlay at/);
    expect(screen.queryByText(/Gemini is suggesting/)).toBeNull();
    fireEvent.click(add);
    await new Promise((r) => setTimeout(r, 600));
    expect(putOverlays).not.toHaveBeenCalled();
    rerender(<OverlaysPhase {...props(project({ job: job("preview-render", "done") }))} />);
    await waitFor(() => expect(putOverlays).toHaveBeenCalledTimes(1));
    // the editor was not remounted: the added overlay is still there
    expect(screen.getByText("1 overlay")).toBeTruthy();
  });

  it("hides the editor only while suggestions are being made", async () => {
    render(<OverlaysPhase {...props(project({ job: job("overlays") }))} />);
    await screen.findByText(/Gemini is suggesting/);
    expect(screen.queryByText(/Add overlay at/)).toBeNull();
  });
});
