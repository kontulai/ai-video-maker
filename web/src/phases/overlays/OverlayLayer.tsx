import { useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { POSITIONS, type Overlay, type Position } from "../../types";
import { BULLET_POSITIONS, stackRows } from "./overlayModel";

const MX = 0.06, MY = 0.09;
const ANCHORS: Record<Position, [number, number]> = {
  "top-left": [MX, MY], "top-right": [1 - MX, MY], "bottom-left": [MX, 1 - MY], "bottom-right": [1 - MX, 1 - MY], center: [0.5, 0.5],
};

interface Drag { id: string; x0: number; y0: number; dx: number; dy: number }

/** Draws the overlays visible at time t over the video, as CSS approximations of the ASS styles. */
export default function OverlayLayer({ overlays, t, accent, selected, onSelect, onMove }: {
  overlays: Overlay[]; t: number; accent: string; selected: string | null;
  onSelect: (id: string) => void; onMove: (id: string, position: Position) => void;
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

  const down = (e: PointerEvent, o: Overlay) => {
    e.stopPropagation();
    onSelect(o.id);
    if (selected !== o.id) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({ id: o.id, x0: e.clientX, y0: e.clientY, dx: 0, dy: 0 });
  };
  const move = (e: PointerEvent) => {
    if (drag) setDrag({ ...drag, dx: e.clientX - drag.x0, dy: e.clientY - drag.y0 });
  };
  const up = (e: PointerEvent, o: Overlay) => {
    if (!drag || drag.id !== o.id) return;
    setDrag(null);
    if (Math.abs(drag.dx) < 4 && Math.abs(drag.dy) < 4) return;
    const rect = ref.current!.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width, py = (e.clientY - rect.top) / rect.height;
    const allowed = o.style === "bullet" ? BULLET_POSITIONS : POSITIONS;
    let best: Position = o.position, bestD = Infinity;
    for (const p of allowed) {
      const [ax, ay] = ANCHORS[p];
      const d = (ax - px) ** 2 + (ay - py) ** 2;
      if (d < bestD) { bestD = d; best = p; }
    }
    if (best !== o.position) onMove(o.id, best);
  };

  return (
    <div className="ovl-layer" ref={ref}>
      {overlays.map((o) => {
        const live = o.start <= t && t < o.end;
        const isSel = o.id === selected;
        if (!live && !isSel) return null;
        const st = stacks.get(o.id)!;
        const d = drag?.id === o.id ? drag : null;
        return (
          <div
            key={o.id}
            className={`ovl ovl-${o.style} ${isSel ? "selected" : ""} ${live ? "" : "ghost"}`}
            style={styleFor(o, st, accent, base, d)}
            onPointerDown={(e) => down(e, o)}
            onPointerMove={move}
            onPointerUp={(e) => up(e, o)}
            title={live ? o.text : `${o.text} (outside its time)`}
          >
            {o.style === "bullet" && <span className="ovl-mark" style={{ width: base * 0.07 * 0.42, height: base * 0.07 * 0.42, background: accent, marginRight: base * 0.07 * 0.45 }} />}
            <span>{o.style === "slam" || o.style === "swipe" ? o.text.toUpperCase() : o.text}</span>
          </div>
        );
      })}
    </div>
  );
}

function styleFor(o: Overlay, st: { row: number; rows: number; position: Position }, accent: string, base: number, drag: Drag | null): CSSProperties {
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
  if (o.style === "bullet") {
    const size = base * 0.07, lineH = size * 1.5;
    s.fontSize = size;
    if (pos === "top-left") s.top = `calc(${MY * 100}% + ${st.row * lineH}px)`;
    else if (pos === "bottom-left") s.bottom = `calc(${MY * 100}% + ${(st.rows - 1 - st.row) * lineH}px)`;
    else transforms.push(`translateY(${(st.row - (st.rows - 1) / 2) * lineH}px)`);
  } else if (o.style === "slam") {
    const size = base * (o.text.length <= 8 ? 0.21 : 0.15);
    s.fontSize = size; s.color = accent;
    s.textShadow = `${size * 0.05}px ${size * 0.07}px ${size * 0.12}px rgba(0,0,0,0.55)`;
    s.WebkitTextStroke = `${Math.max(1, size * 0.03)}px #101010`;
    transforms.push("rotate(-3deg)");
  } else if (o.style === "swipe") {
    const size = base * 0.072;
    s.fontSize = size; s.background = accent; s.padding = `${size * 0.12}px ${size * 0.35}px`;
    transforms.push("rotate(-1deg)");
  } else {
    const size = base * 0.09;
    s.fontSize = size; s.borderBottom = `${Math.max(4, size * 0.09)}px solid ${accent}`; s.paddingBottom = size * 0.14;
  }
  if (drag) transforms.push(`translate(${drag.dx}px, ${drag.dy}px)`);
  if (transforms.length) s.transform = transforms.join(" ");
  return s;
}
