from pathlib import Path

from common_utils import atomic_write_json, now_iso


def default_state():
    return {
        "version": 1,
        "updated_at": now_iso(),
        "items": []
    }


def load_state(path):
    path = Path(path)
    if not path.exists():
        return default_state()

    import json

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if "items" not in data or not isinstance(data["items"], list):
        raise ValueError("Invalid state file: %s" % path)

    return data


def save_state(path, data):
    data["updated_at"] = now_iso()
    atomic_write_json(path, data)


def ensure_state_files(paths):
    for path in paths:
        if Path(path).exists():
            continue
        save_state(path, default_state())


def upsert_item(items, new_item, key_fields):
    for index, item in enumerate(items):
        same_key = True
        for field_name in key_fields:
            if item.get(field_name) != new_item.get(field_name):
                same_key = False
                break
        if same_key:
            items[index] = new_item
            return new_item

    items.append(new_item)
    return new_item


def find_item(items, key_fields, values):
    for item in items:
        same_key = True
        for field_name in key_fields:
            if item.get(field_name) != values.get(field_name):
                same_key = False
                break
        if same_key:
            return item
    return None
