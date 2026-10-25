"""Run the cases through the Gemini text steps, check and judge the answers, and write
one JSON per case plus a summary; compare two runs."""
import hashlib
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import callouts as fx
import make_video as mv

from . import checks as ck
from . import judge as jd
from .cases import Case, case_json, pdf_bytes, segments_for

STAGES = ("plan", "callouts", "fit", "draft")

# The instruction texts a run can override (`--prompt name=file`), and whose hashes it records.
PROMPTS = {
    "planner": (mv, "PLANNER_INSTRUCTIONS"),
    "rewrite": (mv, "REWRITE_INSTRUCTIONS"),
    "fit": (mv, "FIT_INSTRUCTIONS"),
    "draft": (mv, "DRAFT_INSTRUCTIONS"),
    "callouts": (fx, "INSTRUCTIONS"),
    "judge": (jd, "JUDGE_INSTRUCTIONS"),
}


def prompt_hashes() -> dict[str, str]:
    return {name: hashlib.sha256(getattr(mod, attr).encode()).hexdigest()[:12]
            for name, (mod, attr) in PROMPTS.items()}


def run_case(client, case: Case, stages: Sequence[str] = STAGES, judge: bool = True) -> dict:
    """The model's answers for one case with their checks and scores. A failure in one
    stage is recorded under `error`; the stages before it stand."""
    segments = segments_for(case)
    result: dict = {"case": case_json(case), "segments": [s.to_dict() for s in segments],
                    "checks": {}, "prompts": prompt_hashes()}
    if judge:
        result["judge"] = {}
    plan = items = scenes = None
    try:
        if "plan" in stages or "callouts" in stages or "fit" in stages:
            plan = mv.request_plan(client, case.brief, case.talk_track, segments, case.max_scene)
            scenes = mv.scenes_from_plan(plan, segments, case.max_scene)
            items = mv.assemble_prompts(client, plan, scenes)
            result["plan"] = json.loads(plan.model_dump_json())
            result["items"] = items
            result["checks"]["plan"] = [c.to_dict() for c in ck.check_plan(plan, segments, case.max_scene)
                                        + ck.check_prompts(items)]
            if judge:
                result["judge"]["plan"] = jd.judge_plan(client, case, plan, items, segments).to_dict()
        if "callouts" in stages:
            shots = {s.index: f"{s.camera} {s.action}".strip() for s in plan.shots}
            chosen = fx.request_callouts(client, mv.PROMPT_MODEL, scenes, shots, plan.visual_style, case.density)
            kept = fx.enforce_rules([c.model_copy() for c in chosen.callouts], {s.index for s in scenes})
            result["callouts"] = {"accent_color": chosen.accent_color,
                                  "callouts": [c.model_dump() for c in chosen.callouts],
                                  "dropped_by_rules": len(chosen.callouts) - len(kept)}
            result["checks"]["callouts"] = [c.to_dict() for c in ck.check_callouts(chosen.callouts, scenes, case.density)]
            if judge:
                result["judge"]["callouts"] = jd.judge_callouts(client, case, scenes, shots, chosen.callouts).to_dict()
        if "fit" in stages and case.in_frame:
            result["fit"], result["checks"]["fit"] = [], []
            if judge:
                result["judge"]["fit"] = []
            for f in case.in_frame:
                item = items[f.segment - 1]
                fitted = mv.fit_to_footage(client, item, f.in_frame)
                result["fit"].append({"segment": f.segment, "in_frame": f.in_frame, "prompt": fitted})
                result["checks"]["fit"].append([c.to_dict() for c in ck.check_fit(fitted, f.in_frame)])
                if judge:
                    result["judge"]["fit"].append(jd.judge_fit(client, item, f.in_frame, fitted).to_dict())
        if "draft" in stages and case.pdf:
            pdf = pdf_bytes(case)
            draft = mv.draft_talk_track(client, pdf, case.brief, case.notes)
            result["draft"] = draft
            result["checks"]["draft"] = [c.to_dict() for c in ck.check_talk_track(draft)]
            if judge:
                result["judge"]["draft"] = jd.judge_talk_track(client, case, pdf, draft).to_dict()
    except Exception as e:  # noqa: BLE001 - one case must not end the suite
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()
    return result


def _pass_rates(results: list[dict]) -> dict:
    """Per stage, the fraction of (case, run, entry) where each check passed."""
    tally: dict[str, dict[str, list[bool]]] = {}
    for r in results:
        for stage, checks in r.get("checks", {}).items():
            groups = checks if checks and isinstance(checks[0], list) else [checks]
            for group in groups:
                for c in group:
                    tally.setdefault(stage, {}).setdefault(c["name"], []).append(c["passed"])
    return {stage: {name: round(sum(v) / len(v), 3) for name, v in names.items()}
            for stage, names in tally.items()}


def _judge_means(results: list[dict]) -> dict:
    tally: dict[str, dict[str, list[float]]] = {}
    for r in results:
        for stage, judged in r.get("judge", {}).items():
            for j in (judged if isinstance(judged, list) else [judged]):
                for k, v in j.items():
                    if isinstance(v, (int, float)):
                        tally.setdefault(stage, {}).setdefault(k, []).append(v)
    return {stage: {k: round(sum(v) / len(v), 3) for k, v in ks.items()} for stage, ks in tally.items()}


def summarize(results: list[dict], cases: list[Case], stages: Sequence[str], repeat: int,
              label: str = "") -> dict:
    return {
        "label": label,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cases": [c.name for c in cases],
        "runs": repeat,
        "stages": list(stages),
        "models": {"prompt": mv.PROMPT_MODEL, "judge": jd.JUDGE_MODEL},
        "prompts": prompt_hashes(),
        "checks": _pass_rates(results),
        "judge": _judge_means(results),
        "errors": [f"{r['case']['name']}: {r['error']}" for r in results if r.get("error")],
    }


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def render_summary(summary: dict, results: list[dict] = ()) -> str:
    out = [f"# Eval run {summary.get('label') or ''} ({summary['time']})", "",
           f"Cases: {', '.join(summary['cases'])}. Runs per case: {summary['runs']}. "
           f"Stages: {', '.join(summary['stages'])}. Models: {summary['models']['prompt']} "
           f"(judge {summary['models']['judge']}).", "",
           "Prompts: " + ", ".join(f"{k} {v}" for k, v in summary["prompts"].items()), ""]
    for stage, checks in summary["checks"].items():
        out += [f"## {stage}: checks", "", "| check | pass rate |", "|---|---|"]
        out += [f"| {name} | {_pct(rate)} |" for name, rate in checks.items()]
        out.append("")
    for stage, scores in summary["judge"].items():
        out += [f"## {stage}: judge (1-5)", "", "| criterion | mean |", "|---|---|"]
        out += [f"| {k} | {v:.2f} |" for k, v in scores.items()]
        out.append("")
    if summary["errors"]:
        out += ["## Errors", ""] + [f"- {e}" for e in summary["errors"]] + [""]
    failures = [(r["case"]["name"], stage, c) for r in results for stage, checks in r.get("checks", {}).items()
                for group in (checks if checks and isinstance(checks[0], list) else [checks]) for c in group if not c["passed"]]
    if failures:
        out += ["## Failed checks", ""] + [f"- {name} / {stage} / {c['name']}: {c['detail']}" for name, stage, c in failures] + [""]
    verdicts = [(r["case"]["name"], stage, j) for r in results for stage, judged in r.get("judge", {}).items()
                for j in (judged if isinstance(judged, list) else [judged]) if j.get("verdict")]
    if verdicts:
        out += ["## The judge's verdicts", ""] + [f"- {name} / {stage} ({j['mean']:.1f}): {j['verdict']}" for name, stage, j in verdicts] + [""]
    return "\n".join(out)


def run_suite(client, cases: list[Case], out: Path, stages: Sequence[str] = STAGES, judge: bool = True,
              repeat: int = 1, label: str = "") -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for case in cases:
        for n in range(1, repeat + 1):
            mv.log(f"[evals] {case.name} run {n}/{repeat}...")
            result = run_case(client, case, stages, judge)
            name = f"{case.name}.json" if n == 1 else f"{case.name}.{n}.json"
            (out / name).write_text(json.dumps(result, indent=2, ensure_ascii=False))
            results.append(result)
    summary = summarize(results, cases, stages, repeat, label)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "summary.md").write_text(render_summary(summary, results))
    return summary


def compare(a: Path, b: Path) -> str:
    """A markdown table of what changed between two runs' summaries."""
    sa = json.loads((Path(a) / "summary.json").read_text())
    sb = json.loads((Path(b) / "summary.json").read_text())
    out = [f"# {Path(a).name} -> {Path(b).name}", ""]
    changed = [f"- {k}: {sa['prompts'].get(k)} -> {sb['prompts'].get(k)}" for k in sorted(set(sa["prompts"]) | set(sb["prompts"]))
               if sa["prompts"].get(k) != sb["prompts"].get(k)]
    out += ["Prompts changed:", *(changed or ["- none"]), ""]
    for stage in sorted(set(sa["checks"]) | set(sb["checks"])):
        ca, cb = sa["checks"].get(stage, {}), sb["checks"].get(stage, {})
        out += [f"## {stage}: checks", "", "| check | before | after | change |", "|---|---|---|---|"]
        for name in sorted(set(ca) | set(cb)):
            if name in ca and name in cb:
                out.append(f"| {name} | {_pct(ca[name])} | {_pct(cb[name])} | {(cb[name] - ca[name]) * 100:+.0f}% |")
            else:
                out.append(f"| {name} | {_pct(ca[name]) if name in ca else '-'} | {_pct(cb[name]) if name in cb else '-'} | |")
        out.append("")
    for stage in sorted(set(sa["judge"]) | set(sb["judge"])):
        ja, jb = sa["judge"].get(stage, {}), sb["judge"].get(stage, {})
        out += [f"## {stage}: judge (1-5)", "", "| criterion | before | after | change |", "|---|---|---|---|"]
        for name in sorted(set(ja) | set(jb)):
            if name in ja and name in jb:
                out.append(f"| {name} | {ja[name]:.2f} | {jb[name]:.2f} | {jb[name] - ja[name]:+.1f} |")
            else:
                out.append(f"| {name} | {ja.get(name, '-')} | {jb.get(name, '-')} | |")
        out.append("")
    if sa["errors"] or sb["errors"]:
        out += ["Errors before: " + (", ".join(sa["errors"]) or "none"), "Errors after: " + (", ".join(sb["errors"]) or "none"), ""]
    return "\n".join(out)
