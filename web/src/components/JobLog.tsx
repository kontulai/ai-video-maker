import { useEffect, useRef, useState } from "react";
import { jobStreamUrl } from "../api";
import type { Project } from "../types";

/** The current (or last) job of a project, with its log streamed over server-sent events. */
export default function JobLog({ project, reload }: { project: Project; reload: () => void }) {
  const job = project.job;
  const running = job?.status === "running";
  const [lines, setLines] = useState<string[]>(job?.log ?? []);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLines(job?.log ?? []);
  }, [job?.started, job?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!running) return;
    const es = new EventSource(jobStreamUrl(project.id));
    es.onmessage = (ev) => setLines((old) => (old.includes(ev.data) && old[old.length - 1] === ev.data ? old : [...old, ev.data]));
    es.addEventListener("end", () => {
      es.close();
      reload();
    });
    es.onerror = () => {
      es.close();
      setTimeout(reload, 500);
    };
    return () => es.close();
  }, [running, project.id, job?.started, reload]);

  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight });
  }, [lines]);

  if (!job) return <div className="panel"><h2>Log</h2><p className="muted">No job has run yet.</p></div>;
  return (
    <div className="panel">
      <h2>
        Log <span className={`badge ${job.status}`}>{job.phase} · {job.status}</span>
      </h2>
      <div className="log" ref={box}>
        {lines.map((l, i) => <div key={i}>{l}</div>)}
        {job.status === "failed" && <div className="err">Failed: {job.error}</div>}
      </div>
    </div>
  );
}
