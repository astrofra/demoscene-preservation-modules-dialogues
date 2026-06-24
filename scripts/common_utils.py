import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_directory(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def load_json_file(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_text(path, text):
    path = Path(path)
    ensure_directory(path.parent)
    temp_path = path.with_name(path.name + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    temp_path.replace(path)


def atomic_write_json(path, data):
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unique_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def normalize_text_fragment(value):
    if value is None:
        return None

    text = value.replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    return text


def normalize_handle(value):
    if not value:
        return None
    text = value.strip()
    text = re.sub(r"^[^A-Za-z0-9]+", "", text)
    text = re.sub(r"[^A-Za-z0-9_\-]+$", "", text)
    return text or None


def lowercase_words(value):
    return [part for part in re.split(r"[\s_\-\/\.]+", value.lower()) if part]


def escape_dot(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_logger(name, log_dir):
    ensure_directory(log_dir)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    log_path = Path(log_dir) / (name + ".log")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
