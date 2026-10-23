import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { job, planView, project } from "../test/fixtures";
import ScenePlanPhase from "./ScenePlanPhase";

vi.mock("../api", () => ({
  getPlan: vi.fn(),
  putPlan: vi.fn(),
  putPrompt: vi.fn(),
  runJob: vi.fn(),
  errorText: (e: unknown) => String(e),
}));
import { getPlan, putPlan, putPrompt } from "../api";

const noop = () => undefined;
const props = (p = project()) => ({ project: p, reload: noop, setProject: noop, goTo: noop });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getPlan).mockResolvedValue(planView());
  vi.mocked(putPlan).mockResolvedValue(planView());
  vi.mocked(putPrompt).mockResolvedValue(planView());
});

describe("ScenePlanPhase", () => {
  it("keeps unsaved edits when the project record changes under it", async () => {
    const { rerender } = render(<ScenePlanPhase {...props()} />);
    const lighting = await screen.findByDisplayValue("Soft");
    fireEvent.change(lighting, { target: { value: "Harsh" } });
    expect(screen.getByText("Save plan")).toBeTruthy();

    // A job elsewhere finishes: the page polls and the project record arrives with a new job status.
    rerender(<ScenePlanPhase {...props(project({ job: job("video", "done") }))} />);
    await new Promise((r) => setTimeout(r, 20));
    expect((screen.getByDisplayValue("Harsh") as HTMLTextAreaElement).value).toBe("Harsh");
    expect(screen.getByText("Save plan")).toBeTruthy();
  });

  it("reloads the plan when its version changes and nothing is dirty", async () => {
    const { rerender } = render(<ScenePlanPhase {...props()} />);
    await screen.findByDisplayValue("Soft");
    expect(getPlan).toHaveBeenCalledTimes(1);
    rerender(<ScenePlanPhase {...props(project({ plan: { version: 2, inputs: {} } }))} />);
    await waitFor(() => expect(getPlan).toHaveBeenCalledTimes(2));
  });

  it("saving a prompt while the plan is dirty keeps the dirty plan", async () => {
    const { rerender } = render(<ScenePlanPhase {...props()} />);
    const lighting = await screen.findByDisplayValue("Soft");
    fireEvent.change(lighting, { target: { value: "Harsh" } });
    fireEvent.click(screen.getByLabelText(/Show prompts/));
    const prompt = screen.getByDisplayValue("wide shot");
    fireEvent.change(prompt, { target: { value: "close shot" } });
    const saved = planView();
    saved.items[0].prompt = "close shot";
    saved.items[0].pinned_for = "abc";
    vi.mocked(putPrompt).mockResolvedValue(saved);
    fireEvent.click(screen.getByText("Save prompt"));
    await waitFor(() => expect(putPrompt).toHaveBeenCalledWith("p1", 1, "close shot"));
    // pinning bumps the plan version; the page reloads the project record
    rerender(<ScenePlanPhase {...props(project({ plan: { version: 2, inputs: {} } }))} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText(/pinned/)).toBeTruthy();
    expect((screen.getByDisplayValue("Harsh") as HTMLTextAreaElement).value).toBe("Harsh");
    expect(screen.getByText("Save plan")).toBeTruthy();
  });

  it("disables saving while a plan or video job runs", async () => {
    for (const phase of ["plan", "video"]) {
      const r = render(<ScenePlanPhase {...props(project({ job: job(phase) }))} />);
      const lighting = await screen.findByDisplayValue("Soft");
      fireEvent.change(lighting, { target: { value: "Harsh" } });
      expect((screen.getByText("Save plan") as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(screen.getByLabelText(/Show prompts/));
      expect((screen.getByText("Save prompt") as HTMLButtonElement).disabled).toBe(true);
      r.unmount();
    }
    render(<ScenePlanPhase {...props(project({ job: job("final") }))} />);
    const lighting = await screen.findByDisplayValue("Soft");
    fireEvent.change(lighting, { target: { value: "Harsh" } });
    expect((screen.getByText("Save plan") as HTMLButtonElement).disabled).toBe(false);
  });
});
