import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(description="Run the MODialogues prototype pipeline.")
    parser.add_argument("--config", default=None, help="Path to a config JSON file.")
    parser.add_argument("--limit", type=int, default=None, help="Limit items for each applicable stage.")
    parser.add_argument("--skip-discover", action="store_true", help="Skip discovery.")
    parser.add_argument("--skip-download", action="store_true", help="Skip download.")
    parser.add_argument("--skip-parse", action="store_true", help="Skip parsing.")
    parser.add_argument("--skip-summarize", action="store_true", help="Skip Ollama summarization.")
    parser.add_argument("--skip-graph", action="store_true", help="Skip graph export.")
    return parser.parse_args()


def run_script(script_name, extra_args):
    command = [sys.executable, str(SCRIPT_DIR / script_name)]
    command.extend(extra_args)
    subprocess.run(command, check=True)


def main():
    args = parse_args()
    shared_args = []
    if args.config:
        shared_args.extend(["--config", args.config])

    if not args.skip_discover or not args.skip_download:
        fetch_args = list(shared_args)
        if args.limit is not None:
            fetch_args.extend(["--limit", str(args.limit)])
        if not args.skip_discover:
            fetch_args.append("--discover")
        if not args.skip_download:
            fetch_args.append("--download")
        run_script("fetch_modules.py", fetch_args)

    if not args.skip_parse:
        parse_stage_args = list(shared_args)
        if args.limit is not None:
            parse_stage_args.extend(["--limit", str(args.limit)])
        run_script("parse_modules.py", parse_stage_args)

    if not args.skip_summarize:
        summarize_args = list(shared_args)
        if args.limit is not None:
            summarize_args.extend(["--limit", str(args.limit)])
        run_script("run_ollama.py", summarize_args)

    if not args.skip_graph:
        run_script("build_graph.py", shared_args)


if __name__ == "__main__":
    main()
