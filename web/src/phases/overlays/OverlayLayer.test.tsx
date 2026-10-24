import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type { Overlay } from "../../types";
import OverlayLayer from "./OverlayLayer";

vi.mock("../../api", () => ({ fileUrl: (_id: string, name: string) => `/files/${name}` }));

const base: Overlay = {
  id: "a", kind: "text", text: "Free", style: "swipe", position: "top-left", list_name: "", start: 0, end: 5,
  anchor: "", segment_index: null, edited: false, scale: 1, rotation: 0, image: "", x: null, y: null,
};
const props = (overlays: Overlay[], selected: string | null = null, onChange = vi.fn()) => ({
  overlays, t: 1, accent: "#ffc400", selected, projectId: "p1", onSelect: vi.fn(), onChange,
});

beforeAll(() => {
  // jsdom lays nothing out: pretend the layer is 640 x 360 and let pointer capture be a no-op
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 640 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 360 });
  HTMLElement.prototype.setPointerCapture = () => undefined;
});

describe("OverlayLayer", () => {
  it("scales the text size and turns it like the renderer does", () => {
    render(<OverlayLayer {...props([{ ...base, scale: 2, rotation: 10 }])} />);
    const el = screen.getByTitle("Free");
    expect(el.style.fontSize).toBe(`${360 * 0.072 * 2}px`);
    expect(el.style.transform).toContain("rotate(11deg)");   // the swipe's own 1 degree tilt plus 10
  });

  it("draws a picture overlay as an image sized from the frame width", () => {
    render(<OverlayLayer {...props([{ ...base, kind: "image", text: "", image: "overlays/img_1.png", scale: 0.5, rotation: 90 }])} />);
    const img = screen.getByAltText("overlay picture") as HTMLImageElement;
    expect(img.src).toContain("/files/overlays/img_1.png");
    expect(img.style.width).toBe(`${640 * 0.3 * 0.5}px`);
    expect(img.closest(".ovl")!.getAttribute("style")).toContain("rotate(90deg)");
  });

  it("shows resize and rotate handles on the selected overlay and reports a drag as a patch", () => {
    const onChange = vi.fn();
    const { container } = render(<OverlayLayer {...props([base], "a", onChange)} />);
    const resize = container.querySelector(".ovl-handle.resize")!;
    const rotate = container.querySelector(".ovl-handle.rotate")!;
    expect(resize && rotate).toBeTruthy();
    // the overlay's box is at 0,0 in jsdom, so distances are measured from the origin
    fireEvent.pointerDown(resize, { pointerId: 1, clientX: 100, clientY: 0 });
    fireEvent.pointerMove(resize, { pointerId: 1, clientX: 200, clientY: 0 });
    fireEvent.pointerUp(resize, { pointerId: 1, clientX: 200, clientY: 0 });
    expect(onChange).toHaveBeenCalledWith("a", { scale: 2 });
    fireEvent.pointerDown(rotate, { pointerId: 2, clientX: 0, clientY: -100 });
    fireEvent.pointerMove(rotate, { pointerId: 2, clientX: 100, clientY: 0 });
    fireEvent.pointerUp(rotate, { pointerId: 2, clientX: 100, clientY: 0 });
    expect(onChange).toHaveBeenCalledWith("a", { rotation: 90 });
  });

  it("has no handles on an unselected overlay", () => {
    const { container } = render(<OverlayLayer {...props([base])} />);
    expect(container.querySelector(".ovl-handle")).toBeNull();
  });

  it("drags the selected overlay to a free point, in fractions of the frame", () => {
    const onChange = vi.fn();
    render(<OverlayLayer {...props([base], "a", onChange)} />);
    const el = screen.getByTitle("Free");
    fireEvent.pointerDown(el, { pointerId: 1, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(el, { pointerId: 1, clientX: 74, clientY: 46 });
    fireEvent.pointerUp(el, { pointerId: 1, clientX: 74, clientY: 46 });
    expect(onChange).toHaveBeenCalledWith("a", { x: 0.16, y: 0.19 });
  });

  it("draws a freely placed overlay with the chosen corner at its point", () => {
    render(<OverlayLayer {...props([{ ...base, position: "top-right", x: 0.5, y: 0.25 }])} />);
    const el = screen.getByTitle("Free");
    expect(el.style.left).toBe("50%");
    expect(el.style.top).toBe("25%");
    expect(el.style.transform).toContain("translate(-100%, 0)");
    expect(el.style.transformOrigin).toBe("right top");
  });
});
