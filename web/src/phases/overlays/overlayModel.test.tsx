import { describe, expect, it } from "vitest";
import { newImageOverlay, rotationFromDrag, scaleFromDrag } from "./overlayModel";

describe("overlayModel", () => {
  it("makes an image overlay at the playhead with the default size and no turn", () => {
    const o = newImageOverlay(2.5, 3, "overlays/img_1.png");
    expect(o).toMatchObject({ kind: "image", image: "overlays/img_1.png", text: "", start: 2.5, end: 5.5, scale: 1, rotation: 0, edited: true });
  });

  it("scales by how far the corner handle moves from the centre", () => {
    const c = { x: 100, y: 100 };
    expect(scaleFromDrag(c, { x: 150, y: 100 }, { x: 200, y: 100 }, 1)).toBe(2);
    expect(scaleFromDrag(c, { x: 150, y: 100 }, { x: 125, y: 100 }, 2)).toBe(1);
    expect(scaleFromDrag(c, { x: 150, y: 100 }, { x: 101, y: 100 }, 1)).toBe(0.1); // never collapses
    expect(scaleFromDrag(c, { x: 150, y: 100 }, { x: 900, y: 100 }, 1)).toBe(5);   // never explodes
    expect(scaleFromDrag(c, { x: 100, y: 100 }, { x: 200, y: 100 }, 1)).toBe(1);   // a grab at the centre changes nothing
  });

  it("turns by the angle the handle sweeps around the centre, clockwise positive", () => {
    const c = { x: 0, y: 0 };
    expect(rotationFromDrag(c, { x: 0, y: -10 }, { x: 10, y: 0 }, 0)).toBe(90);   // up to right on screen = clockwise
    expect(rotationFromDrag(c, { x: 0, y: -10 }, { x: -10, y: 0 }, 0)).toBe(-90);
    expect(rotationFromDrag(c, { x: 0, y: -10 }, { x: -10, y: 0 }, -100)).toBe(170); // wraps into -180..180
    expect(rotationFromDrag(c, { x: 10, y: 0 }, { x: 10, y: 0 }, 33)).toBe(33);
  });
});
