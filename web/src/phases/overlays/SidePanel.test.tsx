import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Overlay } from "../../types";
import SidePanel from "./SidePanel";

vi.mock("../../api", () => ({ fileUrl: (_id: string, name: string) => `/files/${name}` }));

const text: Overlay = {
  id: "a", kind: "text", text: "Free", style: "swipe", position: "top-left", list_name: "", start: 1, end: 3,
  anchor: "", segment_index: null, edited: false, scale: 1, rotation: 0, image: "",
};
const picture: Overlay = { ...text, id: "b", kind: "image", text: "", image: "overlays/img_1.png", scale: 2, rotation: 15 };
const props = (overlay: Overlay, onChange = vi.fn()) => ({
  overlay, words: [], duration: 10, accent: "#ffc400", projectId: "p1",
  onChange, onAccent: vi.fn(), onDuplicate: vi.fn(), onDelete: vi.fn(),
});

describe("SidePanel", () => {
  it("edits the size as a percentage and the rotation in degrees", () => {
    const onChange = vi.fn();
    render(<SidePanel {...props(text, onChange)} />);
    const size = screen.getByLabelText("Size (%)") as HTMLInputElement;
    expect(size.value).toBe("100");
    fireEvent.change(size, { target: { value: "150" } });
    expect(onChange).toHaveBeenLastCalledWith({ scale: 1.5 });
    fireEvent.change(screen.getByLabelText("Rotation (°)"), { target: { value: "-8" } });
    expect(onChange).toHaveBeenLastCalledWith({ rotation: -8 });
    fireEvent.change(size, { target: { value: "0" } });   // out of range: ignored
    expect(onChange).toHaveBeenCalledTimes(2);
  });

  it("shows the picture instead of the text fields for an image overlay", () => {
    render(<SidePanel {...props(picture)} />);
    expect(screen.queryByText("Text")).toBeNull();
    expect(screen.queryByText("Style")).toBeNull();
    expect(screen.queryByText(/List name/)).toBeNull();
    expect((screen.getByAltText("overlay picture") as HTMLImageElement).src).toContain("/files/overlays/img_1.png");
    expect((screen.getByLabelText("Size (%)") as HTMLInputElement).value).toBe("200");
    expect((screen.getByLabelText("Rotation (°)") as HTMLInputElement).value).toBe("15");
    expect(screen.getByText("Position")).toBeTruthy();
  });
});
