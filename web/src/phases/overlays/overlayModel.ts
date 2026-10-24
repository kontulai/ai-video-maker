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
    anchor: "", segment_index: null, edited: true, scale: 1, rotation: 0, image: "",
  };
}

export function newImageOverlay(at: number, duration: number, image: string): Overlay {
  return { ...newOverlay(at, duration), kind: "image", text: "", position: "bottom-right", image };
}

export interface Point { x: number; y: number }

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

export interface Stack { row: number; rows: number; position: Position }

/** Bullets of one list whose spans overlap become one list, mirroring callouts.stack_bullets:
 * rows in start order, every item in the first item's position. */
export function stackRows(overlays: Overlay[]): Map<string, Stack> {
  const out = new Map<string, Stack>();
  const ordered = [...overlays].sort((a, b) => a.start - b.start || a.end - b.end);
  const open = new Map<string, Overlay[]>();
  const groups: Overlay[][] = [];
  for (const o of ordered) {
    if (o.style !== "bullet" || !o.list_name) {
      out.set(o.id, { row: 0, rows: 1, position: o.position });
      continue;
    }
    let group = open.get(o.list_name);
    if (!group || o.start >= Math.max(...group.map((g) => g.end))) {
      group = [];
      open.set(o.list_name, group);
      groups.push(group);
    }
    out.set(o.id, { row: group.length, rows: 1, position: group[0]?.position ?? o.position });
    group.push(o);
  }
  for (const group of groups) for (const o of group) out.get(o.id)!.rows = group.length;
  return out;
}
