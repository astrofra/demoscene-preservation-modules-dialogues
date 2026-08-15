import argparse
import json
import re
from pathlib import Path

from common_config import (
    build_json_artifact_path,
    get_path,
    load_config,
    load_instrument_terms,
    load_rule_patterns,
    prepare_runtime_directories,
    relative_repo_path,
    resolve_repo_path,
)
from common_state import ensure_state_files, find_item, load_state, save_state
from common_utils import atomic_write_json, build_logger, ensure_directory, normalize_handle, normalize_text_fragment, now_iso, unique_preserve_order


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
CLASSIFICATION_VERSION = "v2"

DATE_LIKE_RE = re.compile(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b", re.IGNORECASE)
TIME_LIKE_RE = re.compile(r"\b\d{1,2}[:.]\d{1,2}\b")
EMAIL_LIKE_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_LIKE_RE = re.compile(r"\+?\d[\d\s().\-]{6,}\d")
ADDRESS_KEYWORD_RE = re.compile(
    r"\b(?:rue|street|st\.?|road|rd\.?|avenue|ave\.?|boulevard|blvd\.?|lane|ln\.?|drive|dr\.?|"
    r"plaza|platz|allee|allée|chemin|route|quai|impasse|place|way|weg|strasse|straße)\b",
    re.IGNORECASE
)
POSTAL_CITY_RE = re.compile(r"\b\d{4,6}\s+[a-z][a-z\-\s]+\b", re.IGNORECASE)
ALIAS_CHANGE_RE = re.compile(
    r"\b(?:aka|a\.k\.a\.|formerly|anciennement|now\b|is now|no longer|n'est plus|used to be)\b",
    re.IGNORECASE
)
GROUP_SIGNATURE_RE = re.compile(
    r"^[a-z0-9][a-z0-9_\-]{1,}\s*/\s*[a-z0-9][a-z0-9 _\-]{1,}$",
    re.IGNORECASE
)
DATED_SIGNATURE_RE = re.compile(
    r"^[a-z0-9][a-z0-9_\-]*(?:/[a-z0-9][a-z0-9_\-]*)+(?:/[0-9]{1,2}[.\-/][0-9]{1,2}[.\-/][0-9]{2,4})"
    r"(?:/[0-9:.]{2,5})?$",
    re.IGNORECASE
)
NAME_STOPWORDS = {
    "module",
    "music",
    "coded",
    "written",
    "destine",
    "recolte",
    "plaie",
    "renouveau",
    "chenal",
    "prononcer",
    "dreamdealers",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Parse tracker modules and classify text fragments.")
    parser.add_argument("--config", default=None, help="Path to a config JSON file.")
    parser.add_argument("--limit", type=int, default=None, help="Limit parsed modules.")
    parser.add_argument("--hash", action="append", default=None, help="Parse one or more specific SHA-256 hashes.")
    parser.add_argument("--source", action="append", default=None, help="Restrict to one or more source names.")
    parser.add_argument("--force", action="store_true", help="Reparse selected modules even if already done.")
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


def looks_like_person_name(fragment):
    words = re.findall(r"[a-z]+(?:[-'][a-z]+)?", fragment.lower())
    if len(words) < 2 or len(words) > 4:
        return False
    if not any("-" in word or "'" in word for word in words):
        return False
    if any(word in NAME_STOPWORDS for word in words):
        return False
    return True


def looks_like_phone(fragment):
    stripped = fragment.strip()
    if len([character for character in stripped if character.isdigit()]) < 8:
        return False
    return PHONE_LIKE_RE.fullmatch(stripped) is not None


def detect_signal_flags(useful_text_fragments):
    flags = []
    for fragment in useful_text_fragments:
        lowered = fragment.lower()

        if EMAIL_LIKE_RE.search(fragment):
            flags.append("email_like")
        if looks_like_phone(fragment):
            flags.append("phone_like")
        if ADDRESS_KEYWORD_RE.search(lowered) or POSTAL_CITY_RE.search(lowered):
            flags.append("address_like")
        if ALIAS_CHANGE_RE.search(lowered):
            flags.append("alias_change")
        if DATED_SIGNATURE_RE.match(lowered):
            flags.append("dated_signature")
        elif GROUP_SIGNATURE_RE.match(lowered):
            flags.append("group_signature")
        if looks_like_person_name(fragment):
            flags.append("person_name_like")
        if DATE_LIKE_RE.search(fragment) and "/" in fragment:
            flags.append("dated_signature")
        if TIME_LIKE_RE.search(fragment) and DATE_LIKE_RE.search(fragment):
            flags.append("dated_signature")

    return unique_preserve_order(flags)


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
    signal_flags = detect_signal_flags(useful_text_fragments)

    llm_decision, llm_reason = decide_llm_usage(labels, useful_text_fragments, signal_flags, config)

    return {
        "labels": labels,
        "instrument_like_fragments": instrument_like_fragments,
        "useful_text_fragments": useful_text_fragments,
        "greets_rule_based": greeting_targets,
        "signature_fragments": signature_fragments,
        "work_offer_fragments": work_offer_fragments,
        "contact_fragments": contact_fragments,
        "technical_fragments": technical_fragments,
        "signal_flags": signal_flags,
        "llm_decision": llm_decision,
        "llm_reason": llm_reason
    }


def decide_llm_usage(labels, useful_text_fragments, signal_flags, config):
    skip_only_labels = set(config["classification"]["llm_skip_if_only_labels"])
    force_labels = set(config["classification"].get("llm_force_if_labels", []))
    force_signal_flags = set(config["classification"].get("llm_force_if_signal_flags", []))
    useful_length = sum(len(fragment) for fragment in useful_text_fragments)
    forced_labels = sorted(set(labels).intersection(force_labels))
    forced_flags = sorted(set(signal_flags).intersection(force_signal_flags))

    if not useful_text_fragments:
        return "skip", "no useful text fragments"

    if forced_labels:
        return "run", "contains high-value rule labels: %s" % ", ".join(forced_labels)

    if forced_flags:
        return "run", "contains archival signals: %s" % ", ".join(forced_flags)

    # Empty labels mean the rule layer did not understand the text at all,
    # so we should not treat that as a sufficient local classification.
    if labels and set(labels).issubset(skip_only_labels) and "unknown_social" not in labels:
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


def build_existing_text_fragments(existing_metadata):
    text_fragments = list(existing_metadata.get("text_fragments") or [])
    if text_fragments:
        return text_fragments

    parsed = {
        "sample_names": existing_metadata.get("sample_names", []),
        "instrument_names": existing_metadata.get("instrument_names", []),
        "song_message": existing_metadata.get("song_message")
    }
    return build_text_fragments(parsed)


def build_source_files(primary_remote_item, related_remote_items):
    source_files = [{
        "source_name": primary_remote_item["source_name"],
        "remote_path": primary_remote_item["remote_path"],
        "remote_url": primary_remote_item["remote_url"]
    }]
    seen = set([
        (
            primary_remote_item["source_name"],
            primary_remote_item["remote_path"],
            primary_remote_item["remote_url"]
        )
    ])

    ordered_items = sorted(
        related_remote_items,
        key=lambda value: (value["source_name"], value["remote_path"], value["remote_url"])
    )
    for remote_item in ordered_items:
        key = (remote_item["source_name"], remote_item["remote_path"], remote_item["remote_url"])
        if key in seen:
            continue
        source_files.append({
            "source_name": remote_item["source_name"],
            "remote_path": remote_item["remote_path"],
            "remote_url": remote_item["remote_url"]
        })
        seen.add(key)

    return source_files


def build_module_state_item(remote_item):
    return {
        "module_id": remote_item["module_id"],
        "sha256": remote_item.get("sha256"),
        "source_name": remote_item["source_name"],
        "remote_path": remote_item["remote_path"],
        "local_path": remote_item.get("local_path"),
        "format": remote_item["extension"].lstrip("."),
        "parse_status": "pending",
        "parse_error": None,
        "metadata_path": None,
        "title": None,
        "tracker_name": None,
        "author_guess": None,
        "author_source": None,
        "rule_labels": [],
        "llm_signal_flags": [],
        "llm_decision": "skip",
        "llm_reason": "not parsed yet",
        "text_fragment_count": 0,
        "useful_fragment_count": 0,
        "parsed_at": None
    }


def build_metadata_output_path(config, remote_item):
    return build_json_artifact_path(
        get_path(config, "parsed_metadata_dir"),
        remote_item["source_name"],
        remote_item["remote_path"]
    )


def migrate_metadata_path_if_needed(config, module_item, remote_item, logger):
    if not module_item.get("metadata_path"):
        return

    current_path = resolve_repo_path(module_item["metadata_path"])
    expected_path = build_metadata_output_path(config, remote_item)

    if current_path == expected_path:
        return
    if not current_path.exists():
        return

    ensure_directory(expected_path.parent)
    if not expected_path.exists():
        current_path.replace(expected_path)
        logger.info("Moved metadata file to readable path for %s", remote_item["remote_path"])

    module_item["metadata_path"] = relative_repo_path(expected_path)


def build_metadata_from_existing(existing_metadata, module_item, remote_item, source_files, author_guess, author_source, instrument_terms, compiled_patterns, config):
    text_fragments = build_existing_text_fragments(existing_metadata)
    classification = classify_text_fragments(text_fragments, instrument_terms, compiled_patterns, config)

    return {
        "module_id": module_item["module_id"],
        "sha256": module_item["sha256"],
        "local_path": remote_item["local_path"],
        "source_files": source_files,
        "filename": Path(remote_item["remote_path"]).name,
        "format": existing_metadata["format"],
        "title": existing_metadata["title"],
        "tracker_name": existing_metadata["tracker_name"],
        "author_guess": author_guess,
        "author_source": author_source,
        "sample_names": existing_metadata.get("sample_names", []),
        "instrument_names": existing_metadata.get("instrument_names", []),
        "song_message": existing_metadata.get("song_message"),
        "text_fragments": text_fragments,
        "instrument_like_fragments": classification["instrument_like_fragments"],
        "useful_text_fragments": classification["useful_text_fragments"],
        "greets_rule_based": classification["greets_rule_based"],
        "rule_based_classification": {
            "version": CLASSIFICATION_VERSION,
            "labels": classification["labels"],
            "signature_fragments": classification["signature_fragments"],
            "work_offer_fragments": classification["work_offer_fragments"],
            "contact_fragments": classification["contact_fragments"],
            "technical_fragments": classification["technical_fragments"],
            "signal_flags": classification["signal_flags"],
            "llm_decision": classification["llm_decision"],
            "llm_reason": classification["llm_reason"]
        },
        "parsed_at": now_iso()
    }


def apply_metadata_to_module_state(module_item, remote_item, metadata):
    module_item["sha256"] = remote_item.get("sha256")
    module_item["source_name"] = remote_item["source_name"]
    module_item["remote_path"] = remote_item["remote_path"]
    module_item["local_path"] = remote_item.get("local_path")
    module_item["format"] = metadata["format"]
    module_item["parse_status"] = "done"
    module_item["parse_error"] = None
    module_item["title"] = metadata["title"]
    module_item["tracker_name"] = metadata["tracker_name"]
    module_item["author_guess"] = metadata["author_guess"]
    module_item["author_source"] = metadata["author_source"]
    module_item["rule_labels"] = metadata["rule_based_classification"]["labels"]
    module_item["llm_signal_flags"] = metadata["rule_based_classification"].get("signal_flags", [])
    module_item["llm_decision"] = metadata["rule_based_classification"]["llm_decision"]
    module_item["llm_reason"] = metadata["rule_based_classification"]["llm_reason"]
    module_item["text_fragment_count"] = len(metadata["text_fragments"])
    module_item["useful_fragment_count"] = len(metadata["useful_text_fragments"])
    module_item["parsed_at"] = metadata["parsed_at"]


def migrate_modules_state(modules_state, remote_state, config, logger):
    remote_by_sha = {}
    remote_by_module_id = {}
    for remote_item in remote_state["items"]:
        if remote_item.get("module_id"):
            remote_by_module_id[remote_item["module_id"]] = remote_item
        if remote_item.get("sha256"):
            remote_by_sha.setdefault(remote_item["sha256"], []).append(remote_item)

    for module_item in modules_state["items"]:
        if module_item.get("module_id"):
            remote_item = remote_by_module_id.get(module_item["module_id"])
            if remote_item is not None:
                module_item["source_name"] = remote_item["source_name"]
                module_item["remote_path"] = remote_item["remote_path"]
                module_item["local_path"] = remote_item.get("local_path")
                migrate_metadata_path_if_needed(config, module_item, remote_item, logger)
            continue

        matches = remote_by_sha.get(module_item.get("sha256"), [])
        if len(matches) != 1:
            continue

        remote_item = matches[0]
        module_item["module_id"] = remote_item["module_id"]
        module_item["source_name"] = remote_item["source_name"]
        module_item["remote_path"] = remote_item["remote_path"]
        module_item["local_path"] = remote_item.get("local_path")
        migrate_metadata_path_if_needed(config, module_item, remote_item, logger)


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

    migrate_modules_state(modules_state, remote_state, config, logger)

    ready_remote_items = []
    for item in remote_state["items"]:
        if item.get("download_status") != "done":
            continue
        if not item.get("module_id"):
            continue
        ready_remote_items.append(item)
        if find_item(modules_state["items"], ("module_id",), {"module_id": item["module_id"]}) is not None:
            continue
        modules_state["items"].append(build_module_state_item(item))

    save_state(modules_state_path, modules_state)
    remote_by_module_id = dict((item["module_id"], item) for item in ready_remote_items)
    remote_by_sha = {}
    for remote_item in ready_remote_items:
        if not remote_item.get("sha256"):
            continue
        remote_by_sha.setdefault(remote_item["sha256"], []).append(remote_item)

    parsed_metadata_by_sha = {}
    for existing_module_item in modules_state["items"]:
        if existing_module_item.get("parse_status") != "done":
            continue
        if not existing_module_item.get("sha256"):
            continue
        if not existing_module_item.get("metadata_path"):
            continue
        existing_metadata_path = resolve_repo_path(existing_module_item["metadata_path"])
        if not existing_metadata_path.exists():
            continue
        parsed_metadata_by_sha.setdefault(existing_module_item["sha256"], existing_metadata_path)

    selected_sources = set(args.source) if args.source else None

    processed = 0
    for module_item in modules_state["items"]:
        if args.limit is not None and processed >= args.limit:
            break

        if args.hash and module_item.get("sha256") not in args.hash:
            continue

        remote_item = remote_by_module_id.get(module_item.get("module_id"))
        if not remote_item:
            continue
        if selected_sources and remote_item["source_name"] not in selected_sources:
            continue

        module_item["sha256"] = remote_item.get("sha256")
        module_item["source_name"] = remote_item["source_name"]
        module_item["remote_path"] = remote_item["remote_path"]
        module_item["local_path"] = remote_item.get("local_path")
        migrate_metadata_path_if_needed(config, module_item, remote_item, logger)

        metadata_path = None
        if module_item.get("metadata_path"):
            metadata_path = resolve_repo_path(module_item["metadata_path"])

        if module_item["parse_status"] == "done" and metadata_path and metadata_path.exists() and not args.force:
            existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            rule_based_classification = existing_metadata.get("rule_based_classification", {})
            if rule_based_classification.get("version") == CLASSIFICATION_VERSION:
                continue

        local_path = resolve_repo_path(remote_item["local_path"])
        if not local_path.exists():
            module_item["parse_status"] = "failed"
            module_item["parse_error"] = "Local file is missing"
            save_state(modules_state_path, modules_state)
            continue

        try:
            filename = Path(remote_item["remote_path"]).name
            source_files = build_source_files(remote_item, remote_by_sha.get(module_item["sha256"], [remote_item]))
            author_guess, author_source = guess_author(source_files, filename)
            existing_metadata_path = None if args.force else parsed_metadata_by_sha.get(module_item["sha256"])
            if existing_metadata_path is not None and existing_metadata_path.exists():
                existing_metadata = json.loads(existing_metadata_path.read_text(encoding="utf-8"))
                metadata = build_metadata_from_existing(
                    existing_metadata,
                    module_item,
                    remote_item,
                    source_files,
                    author_guess,
                    author_source,
                    instrument_terms,
                    compiled_patterns,
                    config
                )
                log_message = "Reused parsed metadata for %s"
            else:
                parsed = parse_by_extension(local_path)
                text_fragments = build_text_fragments(parsed)
                classification = classify_text_fragments(text_fragments, instrument_terms, compiled_patterns, config)

                metadata = {
                    "module_id": module_item["module_id"],
                    "sha256": module_item["sha256"],
                    "local_path": remote_item["local_path"],
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
                        "version": CLASSIFICATION_VERSION,
                        "labels": classification["labels"],
                        "signature_fragments": classification["signature_fragments"],
                        "work_offer_fragments": classification["work_offer_fragments"],
                        "contact_fragments": classification["contact_fragments"],
                        "technical_fragments": classification["technical_fragments"],
                        "signal_flags": classification["signal_flags"],
                        "llm_decision": classification["llm_decision"],
                        "llm_reason": classification["llm_reason"]
                    },
                    "parsed_at": now_iso()
                }
                log_message = "Parsed %s"

            output_path = build_metadata_output_path(config, remote_item)
            atomic_write_json(output_path, metadata)
            parsed_metadata_by_sha[module_item["sha256"]] = output_path

            apply_metadata_to_module_state(module_item, remote_item, metadata)
            module_item["metadata_path"] = relative_repo_path(output_path)

            logger.info(log_message, module_item["remote_path"])
            processed += 1
        except Exception as exc:
            module_item["parse_status"] = "failed"
            module_item["parse_error"] = str(exc)
            logger.error("Parse failed for %s: %s", module_item.get("remote_path") or module_item.get("module_id"), exc)
        finally:
            save_state(modules_state_path, modules_state)


if __name__ == "__main__":
    main()
