import hashlib
from pathlib import Path

from common_utils import ensure_directory, load_json_file


REPO_ROOT = Path(__file__).resolve().parents[1]


def resolve_repo_path(value):
    path = Path(value)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def relative_repo_path(path):
    return str(Path(path).resolve().relative_to(REPO_ROOT)).replace("\\", "/")


def normalize_remote_path(remote_path):
    parts = []
    for raw_part in str(remote_path).replace("\\", "/").split("/"):
        part = raw_part.strip()
        if not part or part in [".", ".."]:
            continue
        parts.append(part)
    return parts


def build_module_id(source_name, remote_path):
    key = source_name + "|" + "/".join(normalize_remote_path(remote_path))
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return "mod_" + digest[:12]


def build_readable_storage_path(base_dir, source_name, remote_path):
    path = Path(base_dir) / source_name
    for part in normalize_remote_path(remote_path):
        path = path / part
    return path


def build_json_artifact_path(base_dir, source_name, remote_path):
    readable_path = build_readable_storage_path(base_dir, source_name, remote_path)
    return readable_path.with_name(readable_path.name + ".json")


def load_config(config_path=None):
    if config_path is None:
        config_path = REPO_ROOT / "config" / "config.json"
    else:
        config_path = resolve_repo_path(config_path)

    config = load_json_file(config_path)
    config["_config_path"] = str(config_path)
    config["_repo_root"] = str(REPO_ROOT)

    resolved_paths = {}
    for key, value in config.get("paths", {}).items():
        resolved_paths[key] = resolve_repo_path(value)
    config["_paths"] = resolved_paths

    return config


def get_path(config, key):
    return config["_paths"][key]


def prepare_runtime_directories(config):
    ensure_directory(get_path(config, "state_dir"))
    ensure_directory(get_path(config, "raw_modules_dir"))
    ensure_directory(get_path(config, "parsed_metadata_dir"))
    ensure_directory(get_path(config, "summaries_dir"))
    ensure_directory(get_path(config, "graphs_dir"))
    ensure_directory(get_path(config, "logs_dir"))
    ensure_directory(get_path(config, "embeddings_dir"))

    raw_root = get_path(config, "raw_modules_dir")
    ensure_directory(raw_root / "_partial")


def load_instrument_terms(config):
    path = resolve_repo_path(config["classification"]["instrument_terms_path"])
    terms = load_json_file(path)
    return set(value.lower() for value in terms)


def load_rule_patterns(config):
    path = resolve_repo_path(config["classification"]["rule_patterns_path"])
    return load_json_file(path)
