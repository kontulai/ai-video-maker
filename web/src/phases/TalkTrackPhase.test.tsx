import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { job, project } from "../test/fixtures";
import TalkTrackPhase from "./TalkTrackPhase";

vi.mock("../api", () => ({
  patchProject: vi.fn(),
  listVoices: vi.fn(async () => []),
  draftTalkTrack: vi.fn(),
  runJob: vi.fn(),
  fileUrl: (_id: string, name: string) => name,
  errorText: (e: unknown) => String(e),
}));
import { draftTalkTrack } from "../api";

const noop = () => undefined;
const props = (p = project()) => ({ project: p, reload: vi.fn(), setProject: vi.fn(), goTo: noop });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(draftTalkTrack).mockResolvedValue(job("draft"));
});

describe("TalkTrackPhase drafting from a PDF", () => {
  it("sends the chosen PDF and the notes, then reloads so the job shows", async () => {
    const p = props();
    render(<TalkTrackPhase {...p} />);
    const button = screen.getByText("Draft from the PDF") as HTMLButtonElement;
    expect(button.disabled).toBe(true); // no file chosen yet
    const file = new File([new Uint8Array([1])], "guide.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/source document/i), { target: { files: [file] } });
    fireEvent.change(screen.getByPlaceholderText(/about one minute/i), { target: { value: "two minutes, formal" } });
    fireEvent.click(button);
    await waitFor(() => expect(draftTalkTrack).toHaveBeenCalledWith("p1", file, "two minutes, formal"));
    await waitFor(() => expect(p.reload).toHaveBeenCalled());
  });

  it("warns that the draft replaces the text when there is one, and is quiet while a job runs", () => {
    render(<TalkTrackPhase {...props(project({ talk_track: "words", job: job("draft") }))} />);
    expect(screen.getByText(/replaces the talk track/i)).toBeTruthy();
    expect(screen.getByText(/drafting the talk track/i)).toBeTruthy();
    expect((screen.getByText("Draft from the PDF") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("TalkTrackPhase narrator voice", () => {
  it("shows the voice picker next to the voice button", async () => {
    render(<TalkTrackPhase {...props(project({ talk_track: "words" }))} />);
    expect(await screen.findByLabelText(/narrator voice/i)).toBeTruthy();
  });
});
