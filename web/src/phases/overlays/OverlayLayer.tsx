import { useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { fileUrl } from "../../api";
import { IMAGE_WIDTH, POSITIONS, type Overlay, type Position } from "../../types";
import { BULLET_POSITIONS, rotationFromDrag, scaleFromDrag, stackRows, type Point } from "./overlayModel";

const MX = 0.06, MY = 0.09;
const ANCHORS: Record<Position, [number, number]> = {
  "top-left": [MX, MY], "top-right": [1 - MX, MY], "bottom-left": [MX, 1 - MY], "bottom-right": [1 - MX, 1 - MY], center: [0.5, 0.5],
};
/** The styles' own tilt, clockwise degrees, like the ASS \\frz the renderer settles on. */
const TILT = { slam: 3, swipe: 1, reveal: 0, bullet: 0 } as const;

type Mode = "move" | "resize" | "rotate";
interface Drag { id: string; mode: Mode; center: Point; from: Point; to: Point; scale0: number; rotation0: number }

/** Draws the overlays visible at time t over the video, as CSS approximations of the ASS styles
 * and ffmpeg placements. The selected one can be dragged to another anchor, resized from its
 * corner handle and turned with the handle above it. */
export default function OverlayLayer({ overlays, t, accent, selected, projectId, onSelect, onChange }: {
  overlays: Overlay[]; t: number; accent: string; selected: string | null; projectId: string;
  onSelect: (id: string) => void; onChange: (id: string, patch: Partial<Overlay>) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 640, h: 360 });
  const [drag, setDrag] = useState<Drag | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const stacks = stackRows(overlays);
  const base = Math.min(size.w, size.h);
  const at = (e: PointerEvent): Point => ({ x: e.clientX, y: e.clientY });

  const start = (e: PointerEvent, o: Overlay, mode: Mode) => {
    e.stopPropagation();
    const box = (mode === "move" ? e.currentTarget : e.currentTarget.parentElement!).getBoundingClientRect();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({ id: o.id, mode, center: { x: box.left + box.width / 2, y: box.top + box.height / 2 }, from: at(e), to: at(e), scale0: o.scale, rotation0: o.rotation });
  };
  const down = (e: PointerEvent, o: Overlay) => {
    e.stopPropagation();
    onSelect(o.id);
    if (selected === o.id) start(e, o, "move");
  };
  const move = (e: PointerEvent) => {
    if (drag) setDrag({ ...drag, to: at(e) });
  };
  const up = (e: PointerEvent, o: Overlay) => {
    if (!drag || drag.id !== o.id) return;
    e.stopPropagation();
    setDrag(null);
    if (drag.mode === "resize") { onChange(o.id, { scale: scaleFromDrag(drag.center, drag.from, drag.to, drag.scale0) }); return; }
    if (drag.mode === "rotate") { onChange(o.id, { rotation: rotationFromDrag(drag.center, drag.from, drag.to, drag.rotation0) }); return; }
    const dx = drag.to.x - drag.from.x, dy = drag.to.y - drag.from.y;
    if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
    const rect = ref.current!.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width, py = (e.clientY - rect.top) / rect.height;
    const allowed = o.kind === "text" && o.style === "bullet" ? BULLET_POSITIONS : POSITIONS;
    let best: Position = o.position, bestD = Infinity;
    for (const p of allowed) {
      const [ax, ay] = ANCHORS[p];
      const d = (ax - px) ** 2 + (ay - py) ** 2;
      if (d < bestD) { bestD = d; best = p; }
    }
    if (best !== o.position) onChange(o.id, { position: best });
  };

  return (
    <div className="ovl-layer" ref={ref}>
      {overlays.map((o) => {
        const live = o.start <= t && t < o.end;
        const isSel = o.id === selected;
        if (!live && !isSel) return null;
        const st = stacks.get(o.id)!;
        const d = drag?.id === o.id ? drag : null;
        // while a handle is dragged the overlay follows it, before the change is saved
        const shown: Overlay = !d ? o : d.mode === "resize" ? { ...o, scale: scaleFromDrag(d.center, d.from, d.to, d.scale0) }
          : d.mode === "rotate" ? { ...o, rotation: rotationFromDrag(d.center, d.from, d.to, d.rotation0) } : o;
        const label = o.kind === "image" ? "picture" : o.text;
        return (
          <div
            key={o.id}
            className={`ovl ${o.kind === "image" ? "ovl-image" : `ovl-${o.style}`} ${isSel ? "selected" : ""} ${live ? "" : "ghost"}`}
            style={styleFor(shown, st, accent, base, size.w, d?.mode === "move" ? d : null)}
            onPointerDown={(e) => down(e, o)}
            onPointerMove={move}
            onPointerUp={(e) => up(e, o)}
            title={live ? label : `${label} (outside its time)`}
          >
            {o.kind === "image" ? (
              <img src={fileUrl(projectId, o.image)} alt="overlay picture" draggable={false} style={{ width: size.w * IMAGE_WIDTH * shown.scale }} />
            ) : (
              <>
                {o.style === "bullet" && <span className="ovl-mark" style={{ width: base * 0.07 * shown.scale * 0.42, height: base * 0.07 * shown.scale * 0.42, background: accent, marginRight: base * 0.07 * shown.scale * 0.45 }} />}
                <span>{o.style === "slam" || o.style === "swipe" ? o.text.toUpperCase() : o.text}</span>
              </>
            )}
            {isSel && (
              <>
                <span className="ovl-handle rotate" title="Drag to turn" onPointerDown={(e) => start(e, o, "rotate")} onPointerMove={move} onPointerUp={(e) => up(e, o)} />
                <span className="ovl-handle resize" title="Drag to resize" onPointerDown={(e) => start(e, o, "resize")} onPointerMove={move} onPointerUp={(e) => up(e, o)} />
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

function styleFor(o: Overlay, st: { row: number; rows: number; position: Position }, accent: string, base: number, width: number, drag: Drag | null): CSSProperties {
  const pos = st.position;
  const s: CSSProperties = { position: "absolute" };
  const right = pos.endsWith("right");
  const transforms: string[] = [];
  if (pos === "center") {
    s.left = "50%"; s.top = "50%"; s.textAlign = "center";
    transforms.push("translate(-50%, -50%)");
  } else {
    if (right) s.right = `${MX * 100}%`; else s.left = `${MX * 100}%`;
    if (pos.startsWith("top")) s.top = `${MY * 100}%`; else s.bottom = `${MY * 100}%`;
    if (right) s.textAlign = "right";
  }
  // text turns about its anchor like the ASS \\org tag; a picture turns about its own centre like ffmpeg's rotate
  s.transformOrigin = pos === "center" || o.kind === "image" ? "center" : `${right ? "right" : "left"} ${o.kind === "text" && o.style === "bullet" ? "center" : pos.startsWith("top") ? "top" : "bottom"}`;
  let tilt = 0;
  if (o.kind === "image") {
    s.lineHeight = 0; s.width = width * IMAGE_WIDTH * o.scale;
  } else if (o.style === "bullet") {
    const size = base * 0.07 * o.scale, lineH = size * 1.5;
    s.fontSize = size;
    if (pos === "top-left") s.top = `calc(${MY * 100}% + ${st.row * lineH}px)`;
    else if (pos === "bottom-left") s.bottom = `calc(${MY * 100}% + ${(st.rows - 1 - st.row) * lineH}px)`;
    else transforms.push(`translateY(${(st.row - (st.rows - 1) / 2) * lineH}px)`);
  } else if (o.style === "slam") {
    const size = base * (o.text.length <= 8 ? 0.21 : 0.15) * o.scale;
    s.fontSize = size; s.color = accent;
    s.textShadow = `${size * 0.05}px ${size * 0.07}px ${size * 0.12}px rgba(0,0,0,0.55)`;
    s.WebkitTextStroke = `${Math.max(1, size * 0.03)}px #101010`;
    tilt = TILT.slam;
  } else if (o.style === "swipe") {
    const size = base * 0.072 * o.scale;
    s.fontSize = size; s.background = accent; s.padding = `${size * 0.12}px ${size * 0.35}px`;
    tilt = TILT.swipe;
  } else {
    const size = base * 0.09 * o.scale;
    s.fontSize = size; s.borderBottom = `${Math.max(4, size * 0.09)}px solid ${accent}`; s.paddingBottom = size * 0.14;
  }
  const turn = tilt + o.rotation;
  if (turn) transforms.push(`rotate(${turn}deg)`);
  if (drag) transforms.push(`translate(${drag.to.x - drag.from.x}px, ${drag.to.y - drag.from.y}px)`);
  if (transforms.length) s.transform = transforms.join(" ");
  return s;
}
