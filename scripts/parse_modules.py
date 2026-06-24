import argparse
import re
from pathlib import Path

from common_config import get_path, load_config, load_instrument_terms, load_rule_patterns, prepare_runtime_directories, resolve_repo_path
from common_state import ensure_state_files, find_item, load_state, save_state
from common_utils import build_logger, normalize_handle, normalize_text_fragment, now_iso, unique_preserve_order


TRACKER_SIGNATURES = {
    "M.K.": "ProTracker-compatible",
    "M!K!": "ProTracker-compatible",
    "FLT4": "StarTrekker",
    "4CHN": "FastTracker-compatible",
    "6CHN": "FastTracker-compatible",
    "8CHN": "FastTracker-compatible",
    "CD81": "Octalyser"
}

SIMPLE_INSTRUMENT_SUFFIXES = {
    "l", "r", "lo", "hi", "fx", "rev", "dry", "wet", "a", "b", "c"
}


def parse_args():
    parser = argparse.ArgumentParser(description="Parse tracker modules and classify text fragments.")
    parser.add_argument("--config", default=None, help="Path to a config JSON file.")
    parser.add_argument("--limit", type=int, default=None, help="Limit parsed modules.")
    parser.add_argument("--hash", action="append", default=None, help="Parse one or more specific SHA-256 hashes.")
    return parser.parse_args()


def decode_text_bytes(raw_bytes):
    text = raw_bytes.decode("latin-1", errors="ignore")
    return normalize_text_fragment(text)


def parse_mod(path):
    data = Path(path).read_bytes()
    if len(data) < 1084:
        raise ValueError("File too small for MOD parsing")

    title = decode_text_bytes(data[0:20])
    sample_names = []

    offset = 20
    for _ in range(31):
        sample_name = decode_text_bytes(data[offset:offset + 22])
        if sample_name:
            sample_names.append(sample_name)
        offset += 30

    signature = data[1080:1084].decode("latin-1", errors="ignore")
    tracker_name = TRACKER_SIGNATURES.get(signature, "ProTracker-compatible")

    return {
        "format": "mod",
        "title": title,
        "tracker_name": tracker_name,
        "sample_names": sample_names,
        "instrument_names": [],
        "song_message": None
    }


def parse_xm(path):
    data = Path(path).read_bytes()
    if not data.startswith(b"Extended Module: "):
        raise ValueError("Not an XM file")

    title = decode_text_bytes(data[17:37])
    tracker_name = decode_text_bytes(data[38:58])

    return {
        "format": "xm",
        "title": title,
        "tracker_name": tracker_name,
        "sample_names": [],
        "instrument_names": [],
        "song_message": None
    }


def parse_s3m(path):
    data = Path(path).read_bytes()
    if len(data) < 48 or data[44:48] != b"SCRM":
        raise ValueError("Not an S3M file")

    title = decode_text_bytes(data[0:28])

    return {
        "format": "s3m",
        "title": title,
        "tracker_name": "ScreamTracker-compatible",
        "sample_names": [],
        "instrument_names": [],
        "song_message": None
    }


def parse_it(path):
    data = Path(path).read_bytes()
    if len(data) < 30 or data[0:4] != b"IMPM":
        raise ValueError("Not an IT file")

    title = decode_text_bytes(data[4:30])

    return {
        "format": "it",
        "title": title,
        "tracker_name": "Impulse Tracker-compatible",
        "sample_names": [],
        "instrument_names": [],
        "song_message": None
    }


def parse_by_extension(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".mod":
        return parse_mod(path)
    if suffix == ".xm":
        return parse_xm(path)
    if suffix == ".s3m":
        return parse_s3m(path)
    if suffix == ".it":
        return parse_it(path)
    raise ValueError("Unsupported extension: %s" % suffix)


def guess_author(source_files, filename):
    if source_files:
        remote_path = source_files[0]["remote_path"]
        parent = Path(remote_path).parent.name
        if parent and parent != ".":
            return parent, "directory_name"

    stem = Path(filename).stem
    for separator in ["-", "_"]:
        if separator in stem:
            author_guess = stem.split(separator, 1)[0].strip()
            if author_guess:
                return author_guess, "filename"

    return None, None


def is_instrument_like(fragment, instrument_terms):
    normalized = fragment.lower().strip()
    words = [part for part in re.split(r"[\s_\-\/\.]+", normalized) if part]
    if not words:
        return False

    first = words[0]
    if first in instrument_terms:
        for word in words[1:]:
            if word.isdigit():
                continue
            if word in SIMPLE_INSTRUMENT_SUFFIXES:
                continue
            if len(word) <= 2:
                continue
            return False
        return True

    for term in instrument_terms:
        if normalized == term:
            return True
        if normalized.startswith(term):
            suffix = normalized[len(term):]
            if suffix and (suffix.isdigit() or suffix in SIMPLE_INSTRUMENT_SUFFIXES):
                return True

    return False


def compile_rule_patterns(raw_patterns):
    compiled = {}
    for label, pattern_list in raw_patterns.items():
        compiled[label] = [re.compile(pattern, re.IGNORECASE) for pattern in pattern_list]
    return compiled


def extract_greets(fragment):
    greets = []
    match = re.search(r"(?:greetz?|greets|hello|hi|respect to)\s+(?:to\s+)?(.+)$", fragment, re.IGNORECASE)
    if not match:
        return greets

    remainder = match.group(1)
    remainder = re.split(r"[.;:!?]", remainder, 1)[0]
    parts = re.split(r",|/|&|\band\b", remainder, flags=re.IGNORECASE)
    for part in parts:
        handle = normalize_handle(part.strip())
        if handle:
            greets.append(handle)
    return unique_preserve_order(greets)


def classify_text_fragments(text_fragments, instrument_terms, compiled_patterns, config):
    labels = []
    instrument_like_fragments = []
    useful_text_fragments = []
    greeting_targets = []
    signature_fragments = []
    work_offer_fragments = []
    contact_fragments = []
    technical_fragments = []

    for fragment in text_fragments:
        if is_instrument_like(fragment, instrument_terms):
            instrument_like_fragments.append(fragment)
        else:
            useful_text_fragments.append(fragment)

    if text_fragments and len(instrument_like_fragments) == len(text_fragments):
        labels.append("instrument_only")

    for fragment in useful_text_fragments:
        matched_any = False
        for label, regex_list in compiled_patterns.items():
            if not any(regex.search(fragment) for regex in regex_list):
                continue

            matched_any = True
            labels.append(label)

            if label == "greeting":
                greeting_targets.extend(extract_greets(fragment))
            elif label == "signature":
                signature_fragments.append(fragment)
            elif label == "work_offer":
                work_offer_fragments.append(fragment)
            elif label == "contact":
                contact_fragments.append(fragment)
            elif label == "technical_note":
                technical_fragments.append(fragment)

        if not matched_any and len(fragment) >= config["classification"]["llm_min_useful_chars"]:
            labels.append("unknown_social")

    labels = unique_preserve_order(labels)
    greeting_targets = unique_preserve_order(greeting_targets)

    llm_decision, llm_reason = decide_llm_usage(labels, useful_text_fragments, config)

    return {
        "labels": labels,
        "instrument_like_fragments": instrument_like_fragments,
        "useful_text_fragments": useful_text_fragments,
        "greets_rule_based": greeting_targets,
        "signature_fragments": signature_fragments,
        "work_offer_fragments": work_offer_fragments,
        "contact_fragments": contact_fragments,
        "technical_fragments": technical_fragments,
        "llm_decision": llm_decision,
        "llm_reason": llm_reason
    }


def decide_llm_usage(labels, useful_text_fragments, config):
    skip_only_labels = set(config["classification"]["llm_skip_if_only_labels"])
    useful_length = sum(len(fragment) for fragment in useful_text_fragments)

    if not useful_text_fragments:
        return "skip", "no useful text fragments"

    if set(labels).issubset(skip_only_labels) and "unknown_social" not in labels:
        return "skip", "rule-based labels are sufficient"

    if useful_length < config["classification"]["llm_min_useful_chars"]:
        return "skip", "useful text is too short"

    if len(useful_text_fragments) < config["classification"]["llm_min_social_fragments"] and "unknown_social" not in labels:
        return "skip", "not enough social fragments"

    if "unknown_social" in labels:
        return "run", "contains unresolved social text"

    return "run", "contains social text beyond instrument names"


def build_text_fragments(parsed):
    fragments = []
    fragments.extend(parsed.get("sample_names", []))
    fragments.extend(parsed.get("instrument_names", []))
    if parsed.get("song_message"):
        fragments.append(parsed["song_message"])

    normalized = []
    for value in fragments:
        text = normalize_text_fragment(value)
        if text:
            normalized.append(text)
    return unique_preserve_order(normalized)


def build_source_files(remote_items):
    source_files = []
    for item in remote_items:
        source_files.append({
            "source_name": item["source_name"],
            "remote_path": item["remote_path"],
            "remote_url": item["remote_url"]
        })
    return source_files


def build_module_state_item(sha256_value, extension):
    return {
        "sha256": sha256_value,
        "format": extension.lstrip("."),
        "parse_status": "pending",
        "parse_error": None,
        "metadata_path": None,
        "title": None,
        "tracker_name": None,
        "author_guess": None,
        "author_source": None,
        "rule_labels": [],
        "llm_decision": "skip",
        "llm_reason": "not parsed yet",
        "text_fragment_count": 0,
        "useful_fragment_count": 0,
        "parsed_at": None
    }


def main():
    args = parse_args()
    config = load_config(args.config)
    prepare_runtime_directories(config)
    ensure_state_files([
        get_path(config, "remote_files_state"),
        get_path(config, "modules_state"),
        get_path(config, "summaries_state")
    ])

    logger = build_logger("parse_modules", get_path(config, "logs_dir"))
    remote_state = load_state(get_path(config, "remote_files_state"))
    modules_state_path = get_path(config, "modules_state")
    modules_state = load_state(modules_state_path)
    instrument_terms = load_instrument_terms(config)
    compiled_patterns = compile_rule_patterns(load_rule_patterns(config))

    grouped_by_sha = {}
    for item in remote_state["items"]:
        if item.get("download_status") != "done":
            continue
        sha256_value = item.get("sha256")
        if not sha256_value:
            continue
        grouped_by_sha.setdefault(sha256_value, []).append(item)

    for sha256_value, remote_items in grouped_by_sha.items():
        if find_item(modules_state["items"], ("sha256",), {"sha256": sha256_value}) is not None:
            continue
        modules_state["items"].append(build_module_state_item(sha256_value, remote_items[0]["extension"]))

    save_state(modules_state_path, modules_state)

    processed = 0
    for module_item in modules_state["items"]:
        if args.limit is not None and processed >= args.limit:
            break

        if args.hash and module_item["sha256"] not in args.hash:
            continue

        remote_items = grouped_by_sha.get(module_item["sha256"], [])
        if not remote_items:
            continue

        metadata_path = None
        if module_item.get("metadata_path"):
            metadata_path = resolve_repo_path(module_item["metadata_path"])

        if module_item["parse_status"] == "done" and metadata_path and metadata_path.exists():
            continue

        local_path = resolve_repo_path(remote_items[0]["local_path"])
        if not local_path.exists():
            module_item["parse_status"] = "failed"
            module_item["parse_error"] = "Local file is missing"
            save_state(modules_state_path, modules_state)
            continue

        try:
            parsed = parse_by_extension(local_path)
            text_fragments = build_text_fragments(parsed)
            classification = classify_text_fragments(text_fragments, instrument_terms, compiled_patterns, config)
            source_files = build_source_files(remote_items)
            filename = Path(remote_items[0]["remote_path"]).name
            author_guess, author_source = guess_author(source_files, filename)

            metadata = {
                "sha256": module_item["sha256"],
                "source_files": source_files,
                "filename": filename,
                "format": parsed["format"],
                "title": parsed["title"],
                "tracker_name": parsed["tracker_name"],
                "author_guess": author_guess,
                "author_source": author_source,
                "sample_names": parsed["sample_names"],
                "instrument_names": parsed["instrument_names"],
                "song_message": parsed["song_message"],
                "text_fragments": text_fragments,
                "instrument_like_fragments": classification["instrument_like_fragments"],
                "useful_text_fragments": classification["useful_text_fragments"],
                "greets_rule_based": classification["greets_rule_based"],
                "rule_based_classification": {
                    "labels": classification["labels"],
                    "signature_fragments": classification["signature_fragments"],
                    "work_offer_fragments": classification["work_offer_fragments"],
                    "contact_fragments": classification["contact_fragments"],
                    "technical_fragments": classification["technical_fragments"],
                    "llm_decision": classification["llm_decision"],
                    "llm_reason": classification["llm_reason"]
                },
                "parsed_at": now_iso()
            }

            output_path = get_path(config, "parsed_metadata_dir") / (module_item["sha256"] + ".json")
            from common_utils import atomic_write_json
            atomic_write_json(output_path, metadata)

            module_item["format"] = parsed["format"]
            module_item["parse_status"] = "done"
            module_item["parse_error"] = None
            module_item["metadata_path"] = str(output_path.relative_to(resolve_repo_path("."))).replace("\\", "/")
            module_item["title"] = parsed["title"]
            module_item["tracker_name"] = parsed["tracker_name"]
            module_item["author_guess"] = author_guess
            module_item["author_source"] = author_source
            module_item["rule_labels"] = classification["labels"]
            module_item["llm_decision"] = classification["llm_decision"]
            module_item["llm_reason"] = classification["llm_reason"]
            module_item["text_fragment_count"] = len(text_fragments)
            module_item["useful_fragment_count"] = len(classification["useful_text_fragments"])
            module_item["parsed_at"] = metadata["parsed_at"]

            logger.info("Parsed %s", module_item["sha256"])
            processed += 1
        except Exception as exc:
            module_item["parse_status"] = "failed"
            module_item["parse_error"] = str(exc)
            logger.error("Parse failed for %s: %s", module_item["sha256"], exc)
        finally:
            save_state(modules_state_path, modules_state)


if __name__ == "__main__":
    main()
