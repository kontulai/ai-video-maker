import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { job, project } from "../test/fixtures";
import Stepper from "./Stepper";

describe("Stepper", () => {
  it("shows the overlays step running while the preview render job runs", () => {
    render(<Stepper project={project({ job: job("preview-render") })} current="overlays" onSelect={() => undefined} />);
    const step = screen.getByRole("button", { name: /Overlays/ });
    expect(step.className).toContain("running");
    expect(step.textContent).toContain("running...");
    expect(screen.getByRole("button", { name: /Final/ }).className).not.toContain("running");
  });
});
