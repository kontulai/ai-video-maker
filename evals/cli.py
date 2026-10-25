"""`python -m evals run|compare|list`."""
import argparse
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import make_video as mv

from . import runner
from .cases import CASES_DIR, load_cases, segments_for

RUNS_DIR = Path(__file__).resolve().parent / "runs"


def real_client():
    from google import genai
    return genai.Client(api_key=mv.require_env("GEMINI_API_KEY"))


@contextmanager
def prompt_overrides(overrides: dict[str, str]):
    """Swap instruction texts for a run and put them back afterwards."""
    saved = {}
    try:
        for name, text in overrides.items():
            mod, attr = runner.PROMPTS[name]
            saved[name] = getattr(mod, attr)
            setattr(mod, attr, text)
        yield
    finally:
        for name, text in saved.items():
            mod, attr = runner.PROMPTS[name]
            setattr(mod, attr, text)


def parse(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the cases and write a timestamped run directory")
    run.add_argument("--cases", type=Path, default=CASES_DIR)
    run.add_argument("--out", type=Path, default=RUNS_DIR, help="where run directories go")
    run.add_argument("--only", action="append", default=[], help="run only this case (repeatable)")
    run.add_argument("--stages", default=",".join(runner.STAGES), help=f"comma list of {', '.join(runner.STAGES)}")
    run.add_argument("--no-judge", action="store_true", help="deterministic checks only")
    run.add_argument("--repeat", type=int, default=1, help="runs per case (the models are not deterministic)")
    run.add_argument("--prompt", action="append", default=[], metavar="NAME=FILE",
                     help=f"try an instruction text from a file; names: {', '.join(runner.PROMPTS)}")
    run.add_argument("--label", default="", help="a name for the run directory")
    cmp_ = sub.add_parser("compare", help="show what changed between two runs")
    cmp_.add_argument("before", type=Path)
    cmp_.add_argument("after", type=Path)
    ls = sub.add_parser("list", help="list the cases")
    ls.add_argument("--cases", type=Path, default=CASES_DIR)
    return p.parse_args(argv)


def main(argv, client_factory=real_client) -> int:
    args = parse(argv)
    if args.command == "list":
        for case in load_cases(args.cases):
            segs = segments_for(case)
            extras = [f"{len(case.in_frame)} frame fits" if case.in_frame else "", "pdf" if case.pdf else ""]
            print(f"{case.name}: {len(segs)} segments, {segs[-1].end}s, density {case.density}"
                  + "".join(f", {e}" for e in extras if e))
        return 0
    if args.command == "compare":
        print(runner.compare(args.before, args.after))
        return 0
    overrides = {}
    for spec in args.prompt:
        name, _, file = spec.partition("=")
        if name not in runner.PROMPTS or not file:
            print(f"Unknown prompt '{name}'; choose from {', '.join(runner.PROMPTS)} and give NAME=FILE.", file=sys.stderr)
            return 2
        overrides[name] = Path(file).read_text()
    stages = tuple(s.strip() for s in args.stages.split(",") if s.strip())
    unknown = [s for s in stages if s not in runner.STAGES]
    if unknown:
        print(f"Unknown stage(s) {unknown}; choose from {', '.join(runner.STAGES)}.", file=sys.stderr)
        return 2
    cases = load_cases(args.cases, args.only)
    if not cases:
        print(f"No cases found in {args.cases}.", file=sys.stderr)
        return 2
    run_dir = args.out / (datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{args.label}" if args.label else ""))
    with prompt_overrides(overrides):
        summary = runner.run_suite(client_factory(), cases, run_dir, stages, not args.no_judge, args.repeat, args.label)
    print((run_dir / "summary.md").read_text())
    print(f"Written to {run_dir}")
    return 0 if not summary["errors"] else 1
