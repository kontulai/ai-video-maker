import { useEffect, useRef, useState, type PointerEvent } from "react";
import type { Overlay, Segment, Word } from "../../types";
import { MIN_LENGTH, round3, snapToWord } from "./overlayModel";

interface Drag { id: string; mode: "move" | "start" | "end"; x0: number; start: number; end: number }

const LABEL = 64;

/** Overlapping overlays go on separate lanes so every block stays clickable. */
function lanes(overlays: Overlay[]): Map<string, number> {
  const out = new Map<string, number>();
  const ends: number[] = [];
  for (const o of [...overlays].sort((a, b) => a.start - b.start || a.end - b.end)) {
    let lane = ends.findIndex((e) => e <= o.start);
    if (lane < 0) { lane = ends.length; ends.push(0); }
    ends[lane] = o.end;
    out.set(o.id, lane);
  }
  return out;
}

function tickStep(pps: number): number {
  for (const s of [0.5, 1, 2, 5, 10, 30, 60]) if (s * pps >= 70) return s;
  return 120;
}

export default function Timeline({ duration, t, words, segments, overlays, selected, onSeek, onSelect, onRetime }: {
  duration: number; t: number; words: Word[]; segments: Segment[]; overlays: Overlay[]; selected: string | null;
  onSeek: (t: number) => void; onSelect: (id: string) => void; onRetime: (id: string, start: number, end: number) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [zoom, setZoom] = useState(1);
  const [drag, setDrag] = useState<Drag | null>(null);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const dur = Math.max(duration, 1);
  const pps = ((width - LABEL - 2) / dur) * zoom;
  const px = (s: number) => s * pps;
  const laneOf = lanes(overlays);
  const laneCount = Math.max(1, ...Array.from(laneOf.values()).map((l) => l + 1));
  const step = tickStep(pps);
  const ticks: number[] = [];
  for (let x = 0; x <= dur; x = round3(x + step)) ticks.push(x);

  const seekAt = (e: { clientX: number; currentTarget: HTMLElement }) => {
    const scroller = wrap.current!.querySelector(".tl-scroll") as HTMLElement;
    const rect = scroller.getBoundingClientRect();
    onSeek(Math.max(0, Math.min(dur, (e.clientX - rect.left + scroller.scrollLeft) / pps)));
  };

  const down = (e: PointerEvent, o: Overlay, mode: Drag["mode"]) => {
    e.stopPropagation();
    onSelect(o.id);
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({ id: o.id, mode, x0: e.clientX, start: o.start, end: o.end });
  };
  const move = (e: PointerEvent) => {
    if (!drag) return;
    const delta = (e.clientX - drag.x0) / pps;
    const len = drag.end - drag.start;
    if (drag.mode === "move") {
      let start = snapToWord(drag.start + delta, words).time;
      start = Math.max(0, Math.min(dur - len, start));
      onRetime(drag.id, round3(start), round3(start + len));
    } else if (drag.mode === "start") {
      const start = Math.max(0, Math.min(drag.end - MIN_LENGTH, snapToWord(drag.start + delta, words).time));
      onRetime(drag.id, round3(start), drag.end);
    } else {
      const end = Math.max(drag.start + MIN_LENGTH, Math.min(dur, drag.end + delta));
      onRetime(drag.id, drag.start, round3(end));
    }
  };
  const up = () => setDrag(null);

  return (
    <div className="timeline" ref={wrap}>
      <div className="tl-labels">
        <div className="tl-lbl tl-ruler-lbl">
          <button className="small" onClick={() => setZoom((z) => Math.max(1, z / 2))} disabled={zoom <= 1} title="Zoom out">−</button>
          <button className="small" onClick={() => setZoom((z) => Math.min(16, z * 2))} title="Zoom in">+</button>
        </div>
        <div className="tl-lbl">clips</div>
        <div className="tl-lbl">words</div>
        <div className="tl-lbl" style={{ height: laneCount * 26 + 6 }}>overlays</div>
      </div>
      <div className="tl-scroll">
        <div className="tl-inner" style={{ width: px(dur) }} onClick={seekAt}>
          <div className="tl-ruler">
            {ticks.map((x) => (
              <div key={x} className="tl-tick" style={{ left: px(x) }}><span>{x % 1 === 0 ? `${x}s` : x.toFixed(1)}</span></div>
            ))}
          </div>
          <div className="tl-track">
            {segments.map((seg, i) => (
              <div key={seg.index} className={`tl-block clip ${seg.cut || i === 0 ? "cut" : ""}`} style={{ left: px(seg.start), width: Math.max(2, px(seg.duration) - 2) }} title={`Step ${seg.index}: ${seg.text}`}>
                {(i === 0 || segments[i - 1].scene !== seg.scene) ? `scene ${seg.scene}` : ""}
              </div>
            ))}
          </div>
          <div className="tl-track">
            {words.map((w, i) => (
              <div key={i} className="tl-block word" style={{ left: px(w.start), width: Math.max(2, px(w.end - w.start) - 1) }} title={`${w.text} ${w.start.toFixed(2)}s`}>{w.text}</div>
            ))}
          </div>
          <div className="tl-track" style={{ height: laneCount * 26 + 6 }}>
            {overlays.map((o) => (
              <div
                key={o.id}
                className={`tl-block overlay ${o.style} ${o.id === selected ? "selected" : ""}`}
                style={{ left: px(o.start), width: Math.max(6, px(o.end - o.start)), top: 3 + (laneOf.get(o.id) ?? 0) * 26 }}
                onPointerDown={(e) => down(e, o, "move")}
                onPointerMove={move}
                onPointerUp={up}
                onClick={(e) => e.stopPropagation()}
                title={`${o.text}  ${o.start.toFixed(2)}–${o.end.toFixed(2)}s`}
              >
                <span className="tl-handle left" onPointerDown={(e) => down(e, o, "start")} onPointerMove={move} onPointerUp={up} />
                <span className="tl-text">{o.text}</span>
                <span className="tl-handle right" onPointerDown={(e) => down(e, o, "end")} onPointerMove={move} onPointerUp={up} />
              </div>
            ))}
          </div>
          <div className="tl-playhead" style={{ left: px(t) }} />
        </div>
      </div>
    </div>
  );
}
