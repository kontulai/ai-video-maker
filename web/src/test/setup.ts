import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom has no ResizeObserver; the editor's layer and timeline only read sizes from it.
class StubResizeObserver { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = StubResizeObserver;

afterEach(cleanup);

// jsdom has no PointerEvent either: without one, fired pointer events carry no coordinates.
if (typeof globalThis.PointerEvent === "undefined") {
  class PolyPointerEvent extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
    }
  }
  (globalThis as unknown as { PointerEvent: unknown }).PointerEvent = PolyPointerEvent;
}
