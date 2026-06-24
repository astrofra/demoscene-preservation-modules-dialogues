import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

from common_config import (
    build_json_artifact_path,
    get_path,
    load_config,
    prepare_runtime_directories,
    relative_repo_path,
    resolve_repo_path,
)
from common_state import ensure_state_files, find_item, load_state, save_state
from common_utils import atomic_write_json, build_logger, ensure_directory, now_iso, sha256_text


PROMPT_VERSION = "v1"


def parse_args():
    parser = argparse.ArgumentParser(description="Run selective Ollama summaries on parsed module text.")
    parser.add_argument("--config", default=None, help="Path to a config JSON file.")
    parser.add_argument("--limit", type=int, default=None, help="Limit summarized modules.")
    parser.add_argument("--hash", action="append", default=None, help="Process one or more specific SHA-256 hashes.")
    parser.add_argument("--force", action="store_true", help="Re-run summaries even if already done.")
    return parser.parse_args()


def build_summary_state_item(module_item):
    return {
        "module_id": module_item["module_id"],
        "sha256": module_item.get("sha256"),
        "source_name": module_item.get("source_name"),
        "remote_path": module_item.get("remote_path"),
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


def write_summary_artifact(config, module_item, payload):
    output_path = build_json_artifact_path(
        get_path(config, "summaries_dir"),
        module_item["source_name"],
        module_item["remote_path"]
    )
    atomic_write_json(output_path, payload)
    return output_path


def copy_existing_summary(existing_payload, module_item):
    copied = dict(existing_payload)
    copied["module_id"] = module_item["module_id"]
    copied["sha256"] = module_item.get("sha256")
    copied["summarized_at"] = now_iso()
    return copied


def build_summary_output_path(config, module_item):
    return build_json_artifact_path(
        get_path(config, "summaries_dir"),
        module_item["source_name"],
        module_item["remote_path"]
    )


def migrate_summaries_state(summaries_state, modules_state, config, logger):
    modules_by_sha = {}
    modules_by_id = {}
    for module_item in modules_state["items"]:
        if module_item.get("module_id"):
            modules_by_id[module_item["module_id"]] = module_item
        if module_item.get("sha256"):
            modules_by_sha.setdefault(module_item["sha256"], []).append(module_item)

    for summary_item in summaries_state["items"]:
        module_item = None

        if summary_item.get("module_id"):
            module_item = modules_by_id.get(summary_item["module_id"])
        elif summary_item.get("sha256"):
            matches = modules_by_sha.get(summary_item["sha256"], [])
            if len(matches) == 1:
                module_item = matches[0]
                summary_item["module_id"] = module_item["module_id"]

        if module_item is None:
            continue

        summary_item["sha256"] = module_item.get("sha256")
        summary_item["source_name"] = module_item.get("source_name")
        summary_item["remote_path"] = module_item.get("remote_path")

        if not summary_item.get("summary_path"):
            continue

        current_path = resolve_repo_path(summary_item["summary_path"])
        expected_path = build_summary_output_path(config, module_item)
        if current_path == expected_path or not current_path.exists():
            summary_item["summary_path"] = relative_repo_path(expected_path) if expected_path.exists() else summary_item["summary_path"]
            continue

        ensure_directory(expected_path.parent)
        if not expected_path.exists():
            current_path.replace(expected_path)
            logger.info("Moved summary file to readable path for %s", module_item["remote_path"])
        summary_item["summary_path"] = relative_repo_path(expected_path)


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

    migrate_summaries_state(summaries_state, modules_state, config, logger)

    for module_item in modules_state["items"]:
        if not module_item.get("module_id"):
            continue
        if find_item(summaries_state["items"], ("module_id",), {"module_id": module_item["module_id"]}) is None:
            summaries_state["items"].append(build_summary_state_item(module_item))
    save_state(summaries_state_path, summaries_state)

    processed = 0
    for module_item in modules_state["items"]:
        if args.limit is not None and processed >= args.limit:
            break

        if args.hash and module_item["sha256"] not in args.hash:
            continue
        if module_item["parse_status"] != "done":
            continue

        summary_item = find_item(summaries_state["items"], ("module_id",), {"module_id": module_item["module_id"]})
        if summary_item is None:
            continue

        summary_item["sha256"] = module_item.get("sha256")
        summary_item["source_name"] = module_item.get("source_name")
        summary_item["remote_path"] = module_item.get("remote_path")

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
                    "module_id": module_item["module_id"],
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
                output_path = write_summary_artifact(config, module_item, payload)
                summary_item["summary_status"] = "skipped"
                summary_item["summary_error"] = None
                summary_item["summary_skip_reason"] = module_item["llm_reason"]
                summary_item["summary_path"] = relative_repo_path(output_path)
                summary_item["tone"] = None
                summary_item["mentions"] = []
                summary_item["summarized_at"] = payload["summarized_at"]
                logger.info("Skipped LLM for %s", module_item["remote_path"])
                processed += 1
                save_state(summaries_state_path, summaries_state)
                continue

            reused = None
            for existing_item in summaries_state["items"]:
                if existing_item.get("module_id") == module_item["module_id"]:
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
                payload = copy_existing_summary(reused, module_item)
            else:
                prompt = build_prompt(useful_text_fragments)
                result = call_ollama(config, prompt)
                payload = {
                    "module_id": module_item["module_id"],
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

            output_path = write_summary_artifact(config, module_item, payload)
            summary_item["summary_status"] = payload["summary_status"]
            summary_item["summary_error"] = None
            summary_item["summary_skip_reason"] = payload["summary_skip_reason"]
            summary_item["summary_path"] = relative_repo_path(output_path)
            summary_item["tone"] = payload["tone"]
            summary_item["mentions"] = payload["mentions"]
            summary_item["summarized_at"] = payload["summarized_at"]
            logger.info("Summarized %s", module_item["remote_path"])
            processed += 1
        except Exception as exc:
            summary_item["summary_status"] = "failed"
            summary_item["summary_error"] = str(exc)
            logger.error("Summary failed for %s: %s", module_item.get("remote_path") or module_item.get("module_id"), exc)
        finally:
            save_state(summaries_state_path, summaries_state)


if __name__ == "__main__":
    main()
