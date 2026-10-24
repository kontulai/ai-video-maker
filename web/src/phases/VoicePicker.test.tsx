import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { project } from "../test/fixtures";
import VoicePicker from "./VoicePicker";

vi.mock("../api", () => ({
  listVoices: vi.fn(),
  patchProject: vi.fn(),
  errorText: (e: unknown) => String(e),
}));
import { listVoices, patchProject } from "../api";

const voices = [
  { id: "v1", name: "Rachel", description: "american, calm, narration", preview_url: "https://x/rachel.mp3" },
  { id: "v2", name: "Bob", description: "british, deep", preview_url: null },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listVoices).mockResolvedValue(voices);
  vi.mocked(patchProject).mockImplementation(async (_id, patch) => project({ settings: { ...project().settings, ...patch.settings } }));
});

describe("VoicePicker", () => {
  it("lists the account voices and saves the chosen one with its name", async () => {
    const setProject = vi.fn();
    render(<VoicePicker project={project()} setProject={setProject} disabled={false} />);
    const select = (await screen.findByLabelText(/narrator voice/i)) as HTMLSelectElement;
    await waitFor(() => expect(screen.getByText(/Bob/)).toBeTruthy());
    expect(select.value).toBe(""); // the default voice from .env
    fireEvent.change(select, { target: { value: "v2" } });
    await waitFor(() => expect(patchProject).toHaveBeenCalledWith("p1", { settings: { voice_id: "v2", voice_name: "Bob" } }));
    await waitFor(() => expect(setProject).toHaveBeenCalled());
  });

  it("plays a preview of the chosen voice when it has one", async () => {
    const p = project({ settings: { ...project().settings, voice_id: "v1", voice_name: "Rachel" } });
    const { container } = render(<VoicePicker project={p} setProject={vi.fn()} disabled={false} />);
    await waitFor(() => expect(container.querySelector("audio")?.getAttribute("src")).toBe("https://x/rachel.mp3"));
    expect((screen.getByLabelText(/narrator voice/i) as HTMLSelectElement).value).toBe("v1");
  });

  it("keeps a voice that is not in the library as an option, so the setting stays visible", async () => {
    const p = project({ settings: { ...project().settings, voice_id: "gone", voice_name: "Old clone" } });
    render(<VoicePicker project={p} setProject={vi.fn()} disabled={false} />);
    const select = (await screen.findByLabelText(/narrator voice/i)) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("gone"));
    expect(screen.getByText(/Old clone.*not in your library/i)).toBeTruthy();
  });

  it("falls back to a voice id field when the list cannot be loaded", async () => {
    vi.mocked(listVoices).mockRejectedValue(new Error("ElevenLabs error 401"));
    render(<VoicePicker project={project()} setProject={vi.fn()} disabled={false} />);
    await screen.findByText(/ElevenLabs error 401/);
    const input = screen.getByLabelText(/voice id/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "zzz" } });
    fireEvent.blur(input);
    await waitFor(() => expect(patchProject).toHaveBeenCalledWith("p1", { settings: { voice_id: "zzz", voice_name: "" } }));
  });
});
