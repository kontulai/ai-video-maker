import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { project, videoView } from "../test/fixtures";
import VideoPhase from "./VideoPhase";

vi.mock("../api", () => ({
  getVideo: vi.fn(),
  runJob: vi.fn(),
  fileUrl: (_id: string, name: string) => name,
  errorText: (e: unknown) => String(e),
}));
import { getVideo, runJob } from "../api";

const noop = () => undefined;
const props = (p = project()) => ({ project: p, reload: noop, setProject: noop, goTo: noop });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getVideo).mockResolvedValue(videoView());
  vi.mocked(runJob).mockResolvedValue({ phase: "video", status: "running", log: [], error: "", started: "", finished: null, scenes: null });
});

describe("VideoPhase scenes", () => {
  it("shows each scene as one video, not its clips", async () => {
    const { container } = render(<VideoPhase {...props()} />);
    await screen.findAllByText("Regenerate");
    const sources = Array.from(container.querySelectorAll("video")).map((v) => v.getAttribute("src"));
    expect(sources).toEqual(["clips/scene_01.mp4", "clips/scene_02.mp4", "rough_cut.mp4"]);
    expect(screen.getAllByText(/2 steps/).length).toBe(2);
  });

  it("says when a scene has no video yet", async () => {
    vi.mocked(getVideo).mockResolvedValue(videoView("missing"));
    const { container } = render(<VideoPhase {...props()} />);
    await screen.findAllByText("Generate");
    expect(container.querySelectorAll("video").length).toBe(0);
    expect(screen.getAllByText(/No video yet/).length).toBe(2);
  });
});

describe("VideoPhase regeneration feedback", () => {
  it("asks what to change before regenerating a scene and sends the answer", async () => {
    render(<VideoPhase {...props()} />);
    const buttons = await screen.findAllByText("Regenerate");
    fireEvent.click(buttons[1]);
    expect(runJob).not.toHaveBeenCalled();
    const box = screen.getByPlaceholderText(/What was wrong/);
    fireEvent.change(box, { target: { value: "make Ana smile" } });
    fireEvent.click(screen.getByText("Apply and regenerate"));
    await waitFor(() => expect(runJob).toHaveBeenCalledWith("p1", "video", { scenes: [2], feedback: "make Ana smile" }));
    await waitFor(() => expect(screen.queryByPlaceholderText(/What was wrong/)).toBeNull());
  });

  it("regenerates as is when nothing is written", async () => {
    render(<VideoPhase {...props()} />);
    fireEvent.click((await screen.findAllByText("Regenerate"))[0]);
    fireEvent.click(screen.getByText("Regenerate as is"));
    await waitFor(() => expect(runJob).toHaveBeenCalledWith("p1", "video", { scenes: [1], feedback: "" }));
  });

  it("can be cancelled", async () => {
    render(<VideoPhase {...props()} />);
    fireEvent.click((await screen.findAllByText("Regenerate"))[0]);
    fireEvent.click(screen.getByText("Cancel"));
    expect(screen.queryByPlaceholderText(/What was wrong/)).toBeNull();
    expect(runJob).not.toHaveBeenCalled();
  });

  it("generates a missing scene at once, with nothing to ask", async () => {
    vi.mocked(getVideo).mockResolvedValue(videoView("missing"));
    render(<VideoPhase {...props()} />);
    fireEvent.click((await screen.findAllByText("Generate"))[0]);
    await waitFor(() => expect(runJob).toHaveBeenCalledWith("p1", "video", { scenes: [1] }));
    expect(screen.queryByPlaceholderText(/What was wrong/)).toBeNull();
  });

  it("regenerating the whole video asks too; feedback goes to every scene and the bible", async () => {
    render(<VideoPhase {...props()} />);
    fireEvent.click(await screen.findByText("Regenerate all"));
    expect(screen.getByText(/production bible/)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(/What was wrong/), { target: { value: "colder light" } });
    fireEvent.click(screen.getByText("Apply and regenerate"));
    await waitFor(() => expect(runJob).toHaveBeenCalledWith("p1", "video", { scenes: null, feedback: "colder light" }));
  });

  it("regenerating the whole video as is redoes every scene", async () => {
    render(<VideoPhase {...props()} />);
    fireEvent.click(await screen.findByText("Regenerate all"));
    fireEvent.click(screen.getByText("Regenerate as is"));
    await waitFor(() => expect(runJob).toHaveBeenCalledWith("p1", "video", { scenes: [1, 2], feedback: "" }));
  });

  it("generating the remaining scenes runs at once", async () => {
    vi.mocked(getVideo).mockResolvedValue({ ...videoView(), rough_cut: false, scenes: [videoView().scenes[0], videoView("missing").scenes[1]] });
    render(<VideoPhase {...props()} />);
    fireEvent.click(await screen.findByText("Generate 1 remaining"));
    await waitFor(() => expect(runJob).toHaveBeenCalledWith("p1", "video", undefined));
  });
});
