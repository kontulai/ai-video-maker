import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { project } from "../test/fixtures";
import SettingPhase from "./SettingPhase";

vi.mock("../api", () => ({
  patchProject: vi.fn(),
  uploadReferences: vi.fn(),
  deleteReference: vi.fn(),
  runJob: vi.fn(),
  fileUrl: (_id: string, name: string) => name,
  errorText: (e: unknown) => String(e),
}));
import { deleteReference, uploadReferences } from "../api";

const noop = () => undefined;
const props = (p = project()) => ({ project: p, reload: noop, setProject: vi.fn(), goTo: noop });

beforeEach(() => vi.clearAllMocks());

describe("SettingPhase reference pictures", () => {
  it("shows the attached pictures with a remove button each", () => {
    const { container } = render(<SettingPhase {...props(project({ references: ["refs/ref_1.png", "refs/ref_2.jpg"] }))} />);
    const imgs = Array.from(container.querySelectorAll(".refs img")).map((i) => i.getAttribute("src"));
    expect(imgs).toEqual(["refs/ref_1.png", "refs/ref_2.jpg"]);
    expect(screen.getAllByText("Remove").length).toBe(2);
  });

  it("uploads the chosen files and shows the project the server returns", async () => {
    const p = props();
    const next = project({ references: ["refs/ref_1.png"] });
    vi.mocked(uploadReferences).mockResolvedValue(next);
    render(<SettingPhase {...p} />);
    const input = screen.getByLabelText(/attach pictures/i) as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3])], "ana.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(uploadReferences).toHaveBeenCalledWith("p1", [file]));
    expect(p.setProject).toHaveBeenCalledWith(next);
  });

  it("removes a picture", async () => {
    const p = props(project({ references: ["refs/ref_1.png"] }));
    vi.mocked(deleteReference).mockResolvedValue(project());
    render(<SettingPhase {...p} />);
    fireEvent.click(screen.getByText("Remove"));
    await waitFor(() => expect(deleteReference).toHaveBeenCalledWith("p1", "refs/ref_1.png"));
    expect(p.setProject).toHaveBeenCalledWith(project());
  });
});
