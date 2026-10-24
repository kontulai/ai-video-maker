import { describe, expect, it } from "vitest";
import type { Overlay } from "../../types";
import { anchorOf, applyPatch, newImageOverlay, newOverlay, placeFromDrag, rotationFromDrag, scaleFromDrag, stackRows } from "./overlayModel";

const bullet = (over: Partial<Overlay>): Overlay => ({ ...newOverlay(0, 5), style: "bullet", position: "top-left", list_name: "l", ...over });

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

  it("places an overlay at its preset corner until it is given a point", () => {
    expect(newOverlay(0)).toMatchObject({ x: null, y: null });
    expect(anchorOf({ position: "top-right", x: null, y: null })).toEqual({ x: 0.94, y: 0.09 });
    expect(anchorOf({ position: "bottom-left", x: null, y: null })).toEqual({ x: 0.06, y: 0.91 });
    expect(anchorOf({ position: "top-right", x: 0.5, y: 0.25 })).toEqual({ x: 0.5, y: 0.25 });
  });

  it("moves the anchor by the drag, as fractions of the frame, kept inside it", () => {
    expect(placeFromDrag({ position: "top-left", x: null, y: null }, 64, 36, 640, 360)).toEqual({ x: 0.16, y: 0.19 });
    expect(placeFromDrag({ position: "center", x: 0.5, y: 0.5 }, -640, 900, 640, 360)).toEqual({ x: 0, y: 1 });
  });

  it("stacks the bullets of a list at the first item's point", () => {
    const a = bullet({ id: "a", start: 0, end: 5, x: 0.3, y: 0.2 });
    const b = bullet({ id: "b", start: 1, end: 5, position: "bottom-left", x: 0.8, y: 0.9 });
    expect(stackRows([a, b]).get("b")).toEqual({ row: 1, rows: 2, position: "top-left", x: 0.3, y: 0.2 });
  });

  it("moves the whole list when one of its bullets is placed, and nothing else", () => {
    const a = bullet({ id: "a", start: 0, end: 5 });
    const b = bullet({ id: "b", start: 1, end: 5 });
    const other = bullet({ id: "c", list_name: "m" });
    const out = applyPatch([a, b, other], "b", { x: 0.1, y: 0.1 });
    expect(out.find((o) => o.id === "a")).toMatchObject({ x: 0.1, y: 0.1, edited: true });
    expect(out.find((o) => o.id === "c")).toMatchObject({ x: null, y: null });
    const moved = applyPatch([a, b], "b", { position: "center", x: null, y: null });
    expect(moved.find((o) => o.id === "a")!.position).toBe("center");
    const renamed = applyPatch([a, b], "b", { text: "hi" });
    expect(renamed.find((o) => o.id === "a")!.text).toBe(a.text);
    expect(renamed.find((o) => o.id === "b")!.text).toBe("hi");
  });
});
