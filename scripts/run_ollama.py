import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

from common_config import get_path, load_config, prepare_runtime_directories, resolve_repo_path
from common_state import ensure_state_files, find_item, load_state, save_state
from common_utils import atomic_write_json, build_logger, now_iso, sha256_text


PROMPT_VERSION = "v1"


def parse_args():
    parser = argparse.ArgumentParser(description="Run selective Ollama summaries on parsed module text.")
    parser.add_argument("--config", default=None, help="Path to a config JSON file.")
    parser.add_argument("--limit", type=int, default=None, help="Limit summarized modules.")
    parser.add_argument("--hash", action="append", default=None, help="Process one or more specific SHA-256 hashes.")
    parser.add_argument("--force", action="store_true", help="Re-run summaries even if already done.")
    return parser.parse_args()


def build_summary_state_item(sha256_value):
    return {
        "sha256": sha256_value,
        "model_name": None,
        "prompt_version": None,
        "input_text_hash": None,
        "summary_status": "pending",
        "summary_error": None,
        "summary_skip_reason": None,
        "summary_path": None,
        "tone": None,
        "mentions": [],
        "summarized_at": None
    }


def build_prompt(fragments):
    lines = [
        "You are reading text fragments extracted from a demoscene tracker module file.",
        "Return strict JSON only.",
        "Required keys: summary, tone, mentions, relationship_notes, confidence.",
        "Keep the summary short and factual.",
        "Text fragments:"
    ]
    for fragment in fragments:
        lines.append("- %s" % fragment)
    return "\n".join(lines)


def call_ollama(config, prompt):
    base_url = config["ollama"]["base_url"].rstrip("/")
    payload = {
        "model": config["ollama"]["model"],
        "prompt": prompt,
        "stream": False,
        "format": "json"
    }
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        base_url + "/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urlopen(request, timeout=config["ollama"]["timeout_seconds"]) as response:
        outer_payload = json.loads(response.read().decode("utf-8"))

    return json.loads(outer_payload["response"])


def write_summary_artifact(config, sha256_value, payload):
    output_path = get_path(config, "summaries_dir") / (sha256_value + ".json")
    atomic_write_json(output_path, payload)
    return output_path


def copy_existing_summary(existing_payload, sha256_value):
    copied = dict(existing_payload)
    copied["sha256"] = sha256_value
    copied["summarized_at"] = now_iso()
    return copied


def main():
    args = parse_args()
    config = load_config(args.config)
    prepare_runtime_directories(config)
    ensure_state_files([
        get_path(config, "remote_files_state"),
        get_path(config, "modules_state"),
        get_path(config, "summaries_state")
    ])

    logger = build_logger("run_ollama", get_path(config, "logs_dir"))
    modules_state = load_state(get_path(config, "modules_state"))
    summaries_state_path = get_path(config, "summaries_state")
    summaries_state = load_state(summaries_state_path)

    for module_item in modules_state["items"]:
        if find_item(summaries_state["items"], ("sha256",), {"sha256": module_item["sha256"]}) is None:
            summaries_state["items"].append(build_summary_state_item(module_item["sha256"]))
    save_state(summaries_state_path, summaries_state)

    processed = 0
    for module_item in modules_state["items"]:
        if args.limit is not None and processed >= args.limit:
            break

        if args.hash and module_item["sha256"] not in args.hash:
            continue
        if module_item["parse_status"] != "done":
            continue

        summary_item = find_item(summaries_state["items"], ("sha256",), {"sha256": module_item["sha256"]})
        if summary_item is None:
            continue

        if summary_item["summary_status"] in ["done", "skipped"] and summary_item.get("summary_path") and not args.force:
            summary_path = resolve_repo_path(summary_item["summary_path"])
            if summary_path.exists():
                continue

        metadata_path = resolve_repo_path(module_item["metadata_path"])
        if not metadata_path.exists():
            summary_item["summary_status"] = "failed"
            summary_item["summary_error"] = "Metadata file is missing"
            save_state(summaries_state_path, summaries_state)
            continue

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        useful_text_fragments = metadata.get("useful_text_fragments", [])
        input_text_hash = sha256_text(json.dumps(useful_text_fragments, ensure_ascii=False))
        summary_item["model_name"] = config["ollama"]["model"]
        summary_item["prompt_version"] = PROMPT_VERSION
        summary_item["input_text_hash"] = input_text_hash

        try:
            if module_item["llm_decision"] == "skip" or not useful_text_fragments:
                payload = {
                    "sha256": module_item["sha256"],
                    "summary_status": "skipped",
                    "summary_skip_reason": module_item["llm_reason"],
                    "model_name": config["ollama"]["model"],
                    "prompt_version": PROMPT_VERSION,
                    "input_text_hash": input_text_hash,
                    "input_text_fragments": useful_text_fragments,
                    "summary": None,
                    "tone": None,
                    "mentions": [],
                    "relationship_notes": [],
                    "confidence": None,
                    "summarized_at": now_iso()
                }
                output_path = write_summary_artifact(config, module_item["sha256"], payload)
                summary_item["summary_status"] = "skipped"
                summary_item["summary_error"] = None
                summary_item["summary_skip_reason"] = module_item["llm_reason"]
                summary_item["summary_path"] = str(output_path.relative_to(resolve_repo_path("."))).replace("\\", "/")
                summary_item["tone"] = None
                summary_item["mentions"] = []
                summary_item["summarized_at"] = payload["summarized_at"]
                logger.info("Skipped LLM for %s", module_item["sha256"])
                processed += 1
                save_state(summaries_state_path, summaries_state)
                continue

            reused = None
            for existing_item in summaries_state["items"]:
                if existing_item["sha256"] == module_item["sha256"]:
                    continue
                if existing_item.get("summary_status") != "done":
                    continue
                if existing_item.get("input_text_hash") != input_text_hash:
                    continue
                if existing_item.get("model_name") != config["ollama"]["model"]:
                    continue
                if existing_item.get("prompt_version") != PROMPT_VERSION:
                    continue
                if not existing_item.get("summary_path"):
                    continue

                existing_path = resolve_repo_path(existing_item["summary_path"])
                if not existing_path.exists():
                    continue

                reused = json.loads(existing_path.read_text(encoding="utf-8"))
                break

            if reused is not None and not args.force:
                payload = copy_existing_summary(reused, module_item["sha256"])
            else:
                prompt = build_prompt(useful_text_fragments)
                result = call_ollama(config, prompt)
                payload = {
                    "sha256": module_item["sha256"],
                    "summary_status": "done",
                    "summary_skip_reason": None,
                    "model_name": config["ollama"]["model"],
                    "prompt_version": PROMPT_VERSION,
                    "input_text_hash": input_text_hash,
                    "input_text_fragments": useful_text_fragments,
                    "summary": result.get("summary"),
                    "tone": result.get("tone"),
                    "mentions": result.get("mentions", []),
                    "relationship_notes": result.get("relationship_notes", []),
                    "confidence": result.get("confidence"),
                    "summarized_at": now_iso()
                }

            output_path = write_summary_artifact(config, module_item["sha256"], payload)
            summary_item["summary_status"] = payload["summary_status"]
            summary_item["summary_error"] = None
            summary_item["summary_skip_reason"] = payload["summary_skip_reason"]
            summary_item["summary_path"] = str(output_path.relative_to(resolve_repo_path("."))).replace("\\", "/")
            summary_item["tone"] = payload["tone"]
            summary_item["mentions"] = payload["mentions"]
            summary_item["summarized_at"] = payload["summarized_at"]
            logger.info("Summarized %s", module_item["sha256"])
            processed += 1
        except Exception as exc:
            summary_item["summary_status"] = "failed"
            summary_item["summary_error"] = str(exc)
            logger.error("Summary failed for %s: %s", module_item["sha256"], exc)
        finally:
            save_state(summaries_state_path, summaries_state)


if __name__ == "__main__":
    main()
