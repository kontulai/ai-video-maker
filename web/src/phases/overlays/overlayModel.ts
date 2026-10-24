import { MAX_SCALE, MIN_SCALE, type Overlay, type Position, type Word } from "../../types";

/** Seconds a graphic appears before its words are spoken, like callouts.LEAD_IN. */
export const LEAD_IN = 0.15;
/** Lists stack from the left, like callouts.BULLET_POSITIONS. */
export const BULLET_POSITIONS: readonly Position[] = ["top-left", "bottom-left", "center"];
export const MIN_LENGTH = 0.3;

export const round3 = (x: number) => Math.round(x * 1000) / 1000;

export const newId = () => `ov_${Math.random().toString(36).slice(2, 8)}`;

export function newOverlay(at: number, duration = 4): Overlay {
  return {
    id: newId(), kind: "text", text: "New overlay", style: "swipe", position: "top-right", list_name: "",
    start: round3(Math.max(0, at)), end: round3(Math.max(0, at) + duration),
    anchor: "", segment_index: null, edited: true, scale: 1, rotation: 0, image: "", x: null, y: null,
  };
}

export function newImageOverlay(at: number, duration: number, image: string): Overlay {
  return { ...newOverlay(at, duration), kind: "image", text: "", position: "bottom-right", image };
}

export interface Point { x: number; y: number }

/** The presets' margins, as fractions of the frame, like callouts._placement. */
export const MX = 0.06, MY = 0.09;
export const ANCHORS: Record<Position, Point> = {
  "top-left": { x: MX, y: MY }, "top-right": { x: 1 - MX, y: MY },
  "bottom-left": { x: MX, y: 1 - MY }, "bottom-right": { x: 1 - MX, y: 1 - MY }, center: { x: 0.5, y: 0.5 },
};

export type Placed = Pick<Overlay, "position" | "x" | "y">;

/** Where the overlay's anchor corner sits, as fractions of the frame: its point, or the preset's spot. */
export function anchorOf(o: Placed): Point {
  return o.x !== null && o.y !== null ? { x: o.x, y: o.y } : ANCHORS[o.position];
}

/** The point after dragging the overlay by dx, dy pixels over a frame of w by h pixels. */
export function placeFromDrag(o: Placed, dx: number, dy: number, w: number, h: number): Point {
  const a = anchorOf(o);
  const clamp = (v: number) => round3(Math.max(0, Math.min(1, v)));
  return { x: clamp(a.x + dx / w), y: clamp(a.y + dy / h) };
}

/** `patch` applied to overlay `id`, marked edited. A placement (position or point) given to one
 * bullet of a named list moves every bullet of that list, since the list is drawn as one block. */
export function applyPatch(overlays: Overlay[], id: string, patch: Partial<Overlay>): Overlay[] {
  const target = overlays.find((o) => o.id === id);
  const placing = target && target.style === "bullet" && target.list_name && ("x" in patch || "y" in patch || "position" in patch);
  const listPatch: Partial<Overlay> = {};
  if (placing) for (const k of ["x", "y", "position"] as const) if (k in patch) (listPatch as Record<string, unknown>)[k] = patch[k];
  return overlays.map((o) => {
    if (o.id === id) return { ...o, ...patch, edited: true };
    if (placing && o.style === "bullet" && o.list_name === target.list_name) return { ...o, ...listPatch, edited: true };
    return o;
  });
}

const clampScale = (s: number) => Math.round(Math.max(MIN_SCALE, Math.min(MAX_SCALE, s)) * 100) / 100;

/** The scale after dragging the corner handle from `from` to `to`: it grows with the handle's
 * distance from the overlay's centre. */
export function scaleFromDrag(center: Point, from: Point, to: Point, scale0: number): number {
  const d0 = Math.hypot(from.x - center.x, from.y - center.y);
  if (d0 < 1) return clampScale(scale0);
  return clampScale(scale0 * Math.hypot(to.x - center.x, to.y - center.y) / d0);
}

/** The rotation after sweeping the turn handle from `from` to `to` around the centre, in whole
 * degrees, clockwise positive (screen y points down), wrapped into -180..180. */
export function rotationFromDrag(center: Point, from: Point, to: Point, rotation0: number): number {
  const a0 = Math.atan2(from.y - center.y, from.x - center.x);
  const a1 = Math.atan2(to.y - center.y, to.x - center.x);
  let r = Math.round(rotation0 + ((a1 - a0) * 180) / Math.PI);
  r = ((((r + 180) % 360) + 360) % 360) - 180;
  return r === -180 ? 180 : r;
}

/** The word whose start is nearest to t, and the time a graphic for it should appear. */
export function snapToWord(t: number, words: Word[]): { time: number; word: Word | null } {
  if (!words.length) return { time: round3(t), word: null };
  let best = words[0];
  for (const w of words) if (Math.abs(w.start - t) < Math.abs(best.start - t)) best = w;
  return { time: round3(Math.max(0, best.start - LEAD_IN)), word: best };
}

/** The word spoken when a graphic that starts at `start` appears. */
export function wordAt(start: number, words: Word[]): Word | null {
  return snapToWord(start + LEAD_IN, words).word;
}

export interface Stack { row: number; rows: number; position: Position; x: number | null; y: number | null }

/** Bullets of one list whose spans overlap become one list, mirroring callouts.stack_bullets:
 * rows in start order, every item at the first item's position and point. */
export function stackRows(overlays: Overlay[]): Map<string, Stack> {
  const out = new Map<string, Stack>();
  const ordered = [...overlays].sort((a, b) => a.start - b.start || a.end - b.end);
  const open = new Map<string, Overlay[]>();
  const groups: Overlay[][] = [];
  for (const o of ordered) {
    if (o.style !== "bullet" || !o.list_name) {
      out.set(o.id, { row: 0, rows: 1, position: o.position, x: o.x, y: o.y });
      continue;
    }
    let group = open.get(o.list_name);
    if (!group || o.start >= Math.max(...group.map((g) => g.end))) {
      group = [];
      open.set(o.list_name, group);
      groups.push(group);
    }
    const lead = group[0] ?? o;
    out.set(o.id, { row: group.length, rows: 1, position: lead.position, x: lead.x, y: lead.y });
    group.push(o);
  }
  for (const group of groups) for (const o of group) out.get(o.id)!.rows = group.length;
  return out;
}
