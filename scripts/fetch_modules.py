import argparse
import shutil
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, url2pathname, urlopen

from common_config import (
    build_module_id,
    build_readable_storage_path,
    get_path,
    load_config,
    prepare_runtime_directories,
    relative_repo_path,
    resolve_repo_path,
)
from common_state import ensure_state_files, load_state, save_state, find_item
from common_utils import build_logger, ensure_directory, now_iso


class LinkParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


def parse_args():
    parser = argparse.ArgumentParser(description="Discover and download tracker modules.")
    parser.add_argument("--config", default=None, help="Path to a config JSON file.")
    parser.add_argument("--discover", action="store_true", help="Discover remote files.")
    parser.add_argument("--download", action="store_true", help="Download pending files.")
    parser.add_argument("--recent-days", type=int, default=None, help="List files seen recently.")
    parser.add_argument("--limit", type=int, default=None, help="Limit processed items.")
    parser.add_argument("--source", action="append", default=None, help="Restrict to one or more source names.")
    return parser.parse_args()


def get_enabled_sources(config, selected_names):
    sources = []
    for source in config.get("sources", []):
        if not source.get("enabled", True):
            continue
        if selected_names and source["name"] not in selected_names:
            continue
        sources.append(source)
    return sources


def discover_http_index(source, allowed_extensions, user_agent, timeout_seconds, logger):
    base_url = source["base_url"]
    visited = set()
    pending = [base_url]
    discovered = []

    while pending:
        current_url = pending.pop()
        if current_url in visited:
            continue
        visited.add(current_url)

        logger.info("Discovering %s", current_url)
        request = Request(current_url, headers={"User-Agent": user_agent})
        with urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read()

        if "html" not in content_type and not body.lstrip().startswith(b"<"):
            continue

        parser = LinkParser()
        parser.feed(body.decode("utf-8", errors="ignore"))

        for href in parser.links:
            if href.startswith("?") or href.startswith("#") or href.startswith("../"):
                continue

            full_url = urljoin(current_url, href)
            if not full_url.startswith(base_url):
                continue

            parsed = urlparse(full_url)
            if parsed.path.endswith("/"):
                pending.append(full_url)
                continue

            suffix = Path(unquote(parsed.path)).suffix.lower()
            if suffix not in allowed_extensions:
                continue

            remote_path = unquote(full_url[len(base_url):])
            discovered.append({
                "source_name": source["name"],
                "remote_path": remote_path,
                "remote_url": full_url,
                "extension": suffix,
                "remote_size": None,
                "remote_mtime": None
            })

    return discovered


def discover_local_dir(source, allowed_extensions):
    root = resolve_repo_path(source["path"])
    discovered = []

    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue

        suffix = file_path.suffix.lower()
        if suffix not in allowed_extensions:
            continue

        discovered.append({
            "source_name": source["name"],
            "remote_path": str(file_path.relative_to(root)).replace("\\", "/"),
            "remote_url": file_path.resolve().as_uri(),
            "extension": suffix,
            "remote_size": file_path.stat().st_size,
            "remote_mtime": datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        })

    return discovered


def discover_sources(config, sources, logger):
    allowed_extensions = set(value.lower() for value in config["allowed_extensions"])
    user_agent = config.get("user_agent", "MODialogues/0.1")
    timeout_seconds = config.get("download_timeout_seconds", 60)

    discovered = []
    for source in sources:
        source_type = source["type"]
        if source_type == "http_index":
            discovered.extend(discover_http_index(source, allowed_extensions, user_agent, timeout_seconds, logger))
        elif source_type == "local_dir":
            discovered.extend(discover_local_dir(source, allowed_extensions))
        else:
            logger.warning("Unsupported source type: %s", source_type)

    return discovered


def update_remote_state(remote_state, discovered_items):
    now_value = now_iso()
    created_count = 0
    updated_count = 0

    for item in discovered_items:
        existing = find_item(
            remote_state["items"],
            ("source_name", "remote_path"),
            {"source_name": item["source_name"], "remote_path": item["remote_path"]}
        )

        if existing is None:
            new_item = {
                "module_id": build_module_id(item["source_name"], item["remote_path"]),
                "source_name": item["source_name"],
                "remote_path": item["remote_path"],
                "remote_url": item["remote_url"],
                "extension": item["extension"],
                "remote_size": item["remote_size"],
                "remote_mtime": item["remote_mtime"],
                "first_seen_at": now_value,
                "last_seen_at": now_value,
                "download_status": "pending",
                "download_error": None,
                "local_path": None,
                "sha256": None
            }
            remote_state["items"].append(new_item)
            created_count += 1
            continue

        existing["module_id"] = existing.get("module_id") or build_module_id(item["source_name"], item["remote_path"])
        existing["remote_url"] = item["remote_url"]
        existing["extension"] = item["extension"]
        existing["remote_size"] = item["remote_size"]
        existing["remote_mtime"] = item["remote_mtime"]
        existing["last_seen_at"] = now_value
        updated_count += 1

    return created_count, updated_count


def build_partial_path(raw_modules_dir, item):
    return raw_modules_dir / "_partial" / (item["module_id"] + item["extension"] + ".part")


def compute_sha256_and_size(path):
    import hashlib

    digest = hashlib.sha256()
    size = 0

    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
            size += len(chunk)

    return digest.hexdigest(), size


def build_expected_local_path(config, item):
    return build_readable_storage_path(
        get_path(config, "raw_modules_dir"),
        item["source_name"],
        item["remote_path"]
    )


def migrate_local_path_if_needed(config, item, logger):
    if not item.get("local_path"):
        return

    current_path = resolve_repo_path(item["local_path"])
    expected_path = build_expected_local_path(config, item)

    if current_path == expected_path:
        return
    if not current_path.exists():
        return

    ensure_directory(expected_path.parent)
    if not expected_path.exists():
        current_path.replace(expected_path)
        logger.info("Moved raw file to readable path for %s", item["remote_path"])

    item["local_path"] = relative_repo_path(expected_path)


def copy_local_source(item, partial_path):
    parsed = urlparse(item["remote_url"])
    source_value = url2pathname(parsed.path)
    if len(source_value) >= 3 and source_value[0] == "\\" and source_value[2] == ":":
        source_value = source_value[1:]
    source_path = Path(source_value)
    if partial_path.exists():
        partial_path.unlink()

    ensure_directory(partial_path.parent)
    shutil.copyfile(source_path, partial_path)


def stream_http_source(item, partial_path, user_agent, timeout_seconds):
    ensure_directory(partial_path.parent)

    existing_size = partial_path.stat().st_size if partial_path.exists() else 0
    headers = {"User-Agent": user_agent}
    if existing_size:
        headers["Range"] = "bytes=%s-" % existing_size

    request = Request(item["remote_url"], headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        status = getattr(response, "status", None)
        can_append = existing_size and status == 206
        if not can_append and partial_path.exists():
            partial_path.unlink()

        mode = "ab" if can_append else "wb"
        with partial_path.open(mode) as handle:
            for chunk in iter(lambda: response.read(65536), b""):
                handle.write(chunk)


def download_item(config, source_map, item, logger):
    raw_modules_dir = get_path(config, "raw_modules_dir")
    partial_path = build_partial_path(raw_modules_dir, item)
    final_path = build_expected_local_path(config, item)

    source = source_map.get(item["source_name"])
    if source is None:
        raise RuntimeError("Missing source configuration for %s" % item["source_name"])

    if item["download_status"] == "done" and item.get("local_path"):
        local_path = resolve_repo_path(item["local_path"])
        if local_path.exists():
            return False
        item["download_status"] = "pending"

    if final_path.exists() and item["download_status"] in ["pending", "failed"]:
        sha256_value, _ = compute_sha256_and_size(final_path)
        item["download_status"] = "done"
        item["download_error"] = None
        item["local_path"] = relative_repo_path(final_path)
        item["sha256"] = sha256_value
        logger.info("Recovered existing file %s", item["local_path"])
        return True

    if source["type"] == "local_dir":
        copy_local_source(item, partial_path)
    elif source["type"] == "http_index":
        stream_http_source(
            item,
            partial_path,
            config.get("user_agent", "MODialogues/0.1"),
            config.get("download_timeout_seconds", 60)
        )
    else:
        raise RuntimeError("Unsupported source type: %s" % source["type"])

    sha256_value, _ = compute_sha256_and_size(partial_path)
    ensure_directory(final_path.parent)
    if final_path.exists():
        final_path.unlink()
    partial_path.replace(final_path)

    item["download_status"] = "done"
    item["download_error"] = None
    item["local_path"] = relative_repo_path(final_path)
    item["sha256"] = sha256_value
    logger.info("Downloaded %s -> %s", item["remote_path"], item["local_path"])
    return True


def list_recent_items(remote_state, recent_days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
    items = []
    for item in remote_state["items"]:
        recent_value = item.get("remote_mtime") or item.get("first_seen_at")
        if not recent_value:
            continue
        parsed = datetime.fromisoformat(recent_value.replace("Z", "+00:00"))
        if parsed >= cutoff:
            items.append(item)

    return items


def main():
    args = parse_args()
    config = load_config(args.config)
    prepare_runtime_directories(config)
    ensure_state_files([
        get_path(config, "remote_files_state"),
        get_path(config, "modules_state"),
        get_path(config, "summaries_state")
    ])

    logger = build_logger("fetch_modules", get_path(config, "logs_dir"))
    sources = get_enabled_sources(config, args.source)
    source_map = dict((source["name"], source) for source in sources)
    remote_state_path = get_path(config, "remote_files_state")
    remote_state = load_state(remote_state_path)
    for item in remote_state["items"]:
        item["module_id"] = item.get("module_id") or build_module_id(item["source_name"], item["remote_path"])
        migrate_local_path_if_needed(config, item, logger)
    save_state(remote_state_path, remote_state)

    discover_selected = args.discover or (not args.discover and not args.download and args.recent_days is None)
    download_selected = args.download or (not args.discover and not args.download and args.recent_days is None)

    if discover_selected:
        discovered_items = discover_sources(config, sources, logger)
        created_count, updated_count = update_remote_state(remote_state, discovered_items)
        save_state(remote_state_path, remote_state)
        logger.info("Discovery complete: %s new, %s updated", created_count, updated_count)

    if download_selected:
        processed = 0
        for item in remote_state["items"]:
            if args.limit is not None and processed >= args.limit:
                break
            if item["source_name"] not in source_map:
                continue
            migrate_local_path_if_needed(config, item, logger)
            if item.get("download_status") == "done" and item.get("local_path"):
                local_path = resolve_repo_path(item["local_path"])
                if local_path.exists():
                    continue
                item["download_status"] = "pending"
            if item.get("download_status") not in ["pending", "failed"]:
                continue

            try:
                changed = download_item(config, source_map, item, logger)
                if changed:
                    processed += 1
            except Exception as exc:
                item["download_status"] = "failed"
                item["download_error"] = str(exc)
                logger.error("Download failed for %s: %s", item["remote_path"], exc)
            finally:
                save_state(remote_state_path, remote_state)

        logger.info("Download step complete")

    if args.recent_days is not None:
        recent_items = list_recent_items(remote_state, args.recent_days)
        for item in recent_items:
            print("%s %s %s" % (item["source_name"], item["download_status"], item["remote_path"]))
        logger.info("Recent items listed: %s", len(recent_items))


if __name__ == "__main__":
    main()
