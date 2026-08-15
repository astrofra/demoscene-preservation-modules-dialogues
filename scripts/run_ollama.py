import argparse
import json
import re
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


PROMPT_VERSION = "v4"
GENERIC_MENTION_TERMS = {
    "address",
    "addresses",
    "aliases",
    "artists",
    "contact",
    "contact info",
    "contacts",
    "context",
    "date",
    "dates",
    "entities",
    "group",
    "groups",
    "handle",
    "handles",
    "individual",
    "individuals",
    "location",
    "locations",
    "mention",
    "mentions",
    "musician",
    "musicians",
    "nickname",
    "nicknames",
    "people",
    "person",
    "place",
    "places",
    "real name",
    "real names",
    "reference",
    "references",
    "scene members",
    "theme",
    "themes"
}
VALID_SEGMENT_KINDS = {
    "factual",
    "signature",
    "contact",
    "poetic",
    "unclear"
}
VALID_ENTITY_TYPES = {
    "handle",
    "group",
    "person_name",
    "address",
    "place",
    "date",
    "time",
    "title",
    "abbreviation",
    "unclear_reference"
}
VALID_RELATION_PREDICATES = {
    "current_handle",
    "former_handle",
    "alias_change",
    "member_of",
    "real_name_of",
    "contact_address_of",
    "signed_on",
    "signed_at",
    "related_to_group",
    "possible_slogan_or_poetic_text",
    "unclear_reference"
}
FORMER_HANDLE_RE = re.compile(r"^\s*([a-z0-9][a-z0-9_\-]{1,})\s+n['’]est plus\.?\s*$", re.IGNORECASE)
SLASH_PAIR_RE = re.compile(r"^\s*([a-z0-9][a-z0-9_\-]{1,})\s*/\s*([a-z0-9][a-z0-9 _\-]{1,})\.?\s*$", re.IGNORECASE)
COMPACT_SIGNATURE_RE = re.compile(
    r"^\s*([a-z0-9][a-z0-9_\-]{1,})/([a-z0-9][a-z0-9_\-]{1,})/(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})/([0-9:.]{2,5})\s*$",
    re.IGNORECASE
)


def parse_args():
    parser = argparse.ArgumentParser(description="Run selective Ollama summaries on parsed module text.")
    parser.add_argument("--config", default=None, help="Path to a config JSON file.")
    parser.add_argument("--limit", type=int, default=None, help="Limit summarized modules.")
    parser.add_argument("--hash", action="append", default=None, help="Process one or more specific SHA-256 hashes.")
    parser.add_argument("--source", action="append", default=None, help="Restrict to one or more source names.")
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
        "You are analyzing ordered text fragments extracted from short tracker module text fields.",
        "Adjacent fragments may be split pieces of the same sentence because tracker fields are short.",
        "Preserve order and reconstruct likely continuous phrases only when adjacency supports it.",
        "Separate observed facts from interpretation.",
        "Do not invent certainty.",
        "Return strict JSON only.",
        "Required top-level keys: summary, tone, language, reconstructed_segments, entities, relations, interpretation, ambiguities, confidence.",
        "summary must be short and factual.",
        "tone must be one short label or null.",
        "language must be one of: fr, en, mixed, unknown.",
        "Use fragment indexes from the numbered list below. Indexes are 1-based.",
        "reconstructed_segments must be an array of objects with keys: fragment_indexes, text, kind, confidence.",
        "Allowed reconstructed segment kinds: factual, signature, contact, poetic, unclear.",
        "entities must be an array of objects with keys: id, type, value, normalized, confidence, evidence_fragment_indexes.",
        "Allowed entity types: handle, group, person_name, address, place, date, time, title, abbreviation, unclear_reference.",
        "relations must be an array of objects with keys: subject_id, predicate, object_id or object_value, confidence, evidence_fragment_indexes, note.",
        "Allowed relation predicates: current_handle, former_handle, alias_change, member_of, real_name_of, contact_address_of, signed_on, signed_at, related_to_group, possible_slogan_or_poetic_text, unclear_reference.",
        "Extract concrete entities only, never categories like groups, individuals, locations, or references.",
        "If a fragment embeds a handle inside a sentence, extract the handle itself as an entity.",
        "If an old and new handle likely coexist, express that with alias_change or former_handle/current_handle relations.",
        "If a fragment looks like 'handle / group', usually split it into one handle entity and one group entity.",
        "If a fragment says 'handle n'est plus', extract that handle as a likely former alias candidate.",
        "If a compact signature looks like 'handle/group/date/time', split it into separate entities when plausible.",
        "If a real name, postal address, date, or time appears, preserve it as exactly as possible.",
        "If text looks poetic, absurd, or like an inside joke, keep it in reconstructed_segments and interpretation instead of turning it into a hard fact.",
        "Fragments:"
    ]
    for index, fragment in enumerate(fragments, start=1):
        lines.append("[%s] %s" % (index, fragment))
    return "\n".join(lines)


def clean_text(value):
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def normalize_language(value):
    cleaned = (clean_text(value) or "unknown").lower()
    if cleaned in {"fr", "en", "mixed", "unknown"}:
        return cleaned
    if "fr" in cleaned and "en" in cleaned:
        return "mixed"
    if "fr" in cleaned:
        return "fr"
    if "en" in cleaned:
        return "en"
    return "unknown"


def confidence_rank(value):
    if value == "high":
        return 3
    if value == "medium":
        return 2
    if value == "low":
        return 1
    return 0


def normalize_confidence(value):
    cleaned = (clean_text(value) or "").lower()
    if cleaned in {"low", "medium", "high"}:
        return cleaned
    if cleaned in {"medium-high", "medium high", "high-ish"}:
        return "high"
    if cleaned in {"medium-low", "medium low"}:
        return "medium"
    if cleaned in {"uncertain", "weak"}:
        return "low"
    return None


def unique_fragment_indexes(values, fragment_count):
    if isinstance(values, int):
        values = [values]
    elif values is None:
        values = []

    indexes = []
    for value in values:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index < 1 or index > fragment_count:
            continue
        if index not in indexes:
            indexes.append(index)
    return indexes


def normalize_segment_kind(value):
    cleaned = (clean_text(value) or "unclear").lower()
    if cleaned in VALID_SEGMENT_KINDS:
        return cleaned
    if cleaned in {"fact", "statement"}:
        return "factual"
    if cleaned in {"poem", "joke", "slogan"}:
        return "poetic"
    return "unclear"


def normalize_entity_type(value):
    cleaned = (clean_text(value) or "unclear_reference").lower()
    aliases = {
        "alias": "handle",
        "artist_handle": "handle",
        "nickname": "handle",
        "crew": "group",
        "person": "person_name",
        "real_name": "person_name",
        "name": "person_name",
        "location": "place",
        "city": "place",
        "country": "place",
        "postal_address": "address",
        "street_address": "address",
        "reference": "unclear_reference",
        "unclear": "unclear_reference"
    }
    cleaned = aliases.get(cleaned, cleaned)
    if cleaned in VALID_ENTITY_TYPES:
        return cleaned
    return "unclear_reference"


def normalize_relation_predicate(value):
    cleaned = (clean_text(value) or "unclear_reference").lower()
    aliases = {
        "alias": "alias_change",
        "aka": "alias_change",
        "member": "member_of",
        "real_name": "real_name_of",
        "address": "contact_address_of",
        "date": "signed_on",
        "time": "signed_at",
        "group": "related_to_group",
        "poetic": "possible_slogan_or_poetic_text",
        "reference": "unclear_reference"
    }
    cleaned = aliases.get(cleaned, cleaned)
    if cleaned in VALID_RELATION_PREDICATES:
        return cleaned
    return "unclear_reference"


def normalize_mention_value(value):
    if isinstance(value, str):
        return clean_text(value)
    if not isinstance(value, dict):
        return None

    for key in ["normalized", "value", "name", "entity", "address", "note", "context"]:
        candidate = clean_text(value.get(key))
        if candidate:
            return candidate
    return None


def is_generic_mention(value):
    if not isinstance(value, str):
        return False
    cleaned = value.strip().lower().strip(".:,;!?()[]{}")
    return cleaned in GENERIC_MENTION_TERMS


def normalize_reconstructed_segments(values, fragment_count):
    segments = []
    for raw_item in values or []:
        if isinstance(raw_item, str):
            text = clean_text(raw_item)
            item = {}
        elif isinstance(raw_item, dict):
            text = clean_text(raw_item.get("text") or raw_item.get("value"))
            item = raw_item
        else:
            continue

        if not text:
            continue

        segments.append({
            "fragment_indexes": unique_fragment_indexes(item.get("fragment_indexes"), fragment_count),
            "text": text,
            "kind": normalize_segment_kind(item.get("kind")),
            "confidence": normalize_confidence(item.get("confidence"))
        })

    return segments


def entity_label(entity):
    value = clean_text(entity.get("value"))
    normalized = clean_text(entity.get("normalized"))
    if value:
        return value
    return normalized


def normalize_entities(values, fragment_count):
    entities = []
    entities_by_key = {}
    used_ids = set()

    for raw_item in values or []:
        if isinstance(raw_item, str):
            item = {}
            value = clean_text(raw_item)
            entity_type = "unclear_reference"
        elif isinstance(raw_item, dict):
            item = raw_item
            value = clean_text(
                item.get("value")
                or item.get("name")
                or item.get("entity")
                or item.get("text")
                or item.get("label")
            )
            entity_type = normalize_entity_type(item.get("type"))
        else:
            continue

        if not value:
            continue

        normalized = clean_text(item.get("normalized")) or value
        key = (entity_type, normalized.lower())
        evidence_fragment_indexes = unique_fragment_indexes(item.get("evidence_fragment_indexes"), fragment_count)
        confidence = normalize_confidence(item.get("confidence"))

        if key in entities_by_key:
            existing = entities_by_key[key]
            for index in evidence_fragment_indexes:
                if index not in existing["evidence_fragment_indexes"]:
                    existing["evidence_fragment_indexes"].append(index)
            if confidence_rank(confidence) > confidence_rank(existing.get("confidence")):
                existing["confidence"] = confidence
            continue

        entity_id = clean_text(item.get("id")) or "e%s" % (len(entities) + 1)
        while entity_id in used_ids:
            entity_id = "e%s" % (len(entities) + 1)

        entity = {
            "id": entity_id,
            "type": entity_type,
            "value": value,
            "normalized": normalized,
            "confidence": confidence,
            "evidence_fragment_indexes": evidence_fragment_indexes
        }
        entities.append(entity)
        entities_by_key[key] = entity
        used_ids.add(entity_id)

    return entities


def build_entity_indexes(entities):
    by_id = {}
    by_value = {}
    for entity in entities:
        by_id[entity["id"]] = entity
        label = entity_label(entity)
        if label:
            by_value[label.lower()] = entity
    return by_id, by_value


def resolve_entity_reference(value, entities_by_id, entities_by_value):
    if isinstance(value, dict):
        raw_id = clean_text(value.get("id"))
        if raw_id and raw_id in entities_by_id:
            entity = entities_by_id[raw_id]
            return entity["id"], entity_label(entity)
        value = value.get("value") or value.get("name") or value.get("entity")

    cleaned = clean_text(value)
    if not cleaned:
        return None, None

    if cleaned in entities_by_id:
        entity = entities_by_id[cleaned]
        return entity["id"], entity_label(entity)

    entity = entities_by_value.get(cleaned.lower())
    if entity is not None:
        return entity["id"], entity_label(entity)

    return None, cleaned


def normalize_relations(values, entities, fragment_count):
    entities_by_id, entities_by_value = build_entity_indexes(entities)
    relations = []
    relation_keys = set()

    for raw_item in values or []:
        if not isinstance(raw_item, dict):
            continue

        subject_id, subject_value = resolve_entity_reference(
            raw_item.get("subject_id") or raw_item.get("subject") or raw_item.get("from"),
            entities_by_id,
            entities_by_value
        )
        object_id, object_value = resolve_entity_reference(
            raw_item.get("object_id") or raw_item.get("object") or raw_item.get("to") or raw_item.get("object_value"),
            entities_by_id,
            entities_by_value
        )
        predicate = normalize_relation_predicate(raw_item.get("predicate") or raw_item.get("relation") or raw_item.get("type"))
        note = clean_text(raw_item.get("note") or raw_item.get("context") or raw_item.get("explanation"))

        if not subject_id and not subject_value:
            continue
        if not object_id and not object_value and predicate != "unclear_reference":
            continue

        relation = {
            "subject_id": subject_id,
            "subject_value": subject_value,
            "predicate": predicate,
            "object_id": object_id,
            "object_value": object_value,
            "confidence": normalize_confidence(raw_item.get("confidence")),
            "evidence_fragment_indexes": unique_fragment_indexes(raw_item.get("evidence_fragment_indexes"), fragment_count),
            "note": note
        }
        if relation["subject_id"] and relation["object_id"] and relation["subject_id"] == relation["object_id"]:
            continue
        relation_key = (
            relation["subject_id"] or relation["subject_value"],
            relation["predicate"],
            relation["object_id"] or relation["object_value"],
            relation["note"]
        )
        if relation_key in relation_keys:
            continue
        relation_keys.add(relation_key)
        relations.append(relation)

    return relations


def normalize_ambiguities(values):
    ambiguities = []
    for raw_item in values or []:
        if isinstance(raw_item, str):
            text = clean_text(raw_item)
            item = {}
        elif isinstance(raw_item, dict):
            text = clean_text(raw_item.get("text") or raw_item.get("value") or raw_item.get("description"))
            item = raw_item
        else:
            continue

        if not text:
            continue

        meanings = []
        for meaning in item.get("possible_meanings") or []:
            cleaned = clean_text(meaning)
            if cleaned and cleaned not in meanings:
                meanings.append(cleaned)

        ambiguities.append({
            "text": text,
            "possible_meanings": meanings,
            "confidence": normalize_confidence(item.get("confidence"))
        })

    return ambiguities


def normalize_interpretation(value):
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        parts = []
        for nested in value.values():
            if isinstance(nested, dict):
                description = clean_text(nested.get("description"))
                if description:
                    parts.append(description)
            else:
                cleaned = clean_text(nested)
                if cleaned:
                    parts.append(cleaned)
        if parts:
            return " ".join(parts)
        return None
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                description = clean_text(item.get("description") or item.get("text"))
                if description:
                    parts.append(description)
            else:
                cleaned = clean_text(item)
                if cleaned:
                    parts.append(cleaned)
        if parts:
            return " ".join(parts)
    return None


def normalize_legacy_relationship_notes(value):
    if not value:
        return {}

    normalized = {}
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = []
        for raw_item in value:
            if not isinstance(raw_item, dict):
                continue
            key = clean_text(raw_item.get("entity") or raw_item.get("name") or raw_item.get("key"))
            note = clean_text(raw_item.get("note") or raw_item.get("value") or raw_item.get("context"))
            if key and note:
                items.append((key, note))
    else:
        return {}

    for key, raw_note in items:
        cleaned_key = clean_text(key)
        cleaned_note = clean_text(raw_note)
        if not cleaned_key or not cleaned_note:
            continue
        normalized.setdefault(cleaned_key, [])
        if cleaned_note not in normalized[cleaned_key]:
            normalized[cleaned_key].append(cleaned_note)

    return dict((key, "; ".join(notes)) for key, notes in normalized.items())


def build_relationship_notes(relations):
    normalized = {}
    for relation in relations:
        key = relation.get("object_value") or relation.get("subject_value")
        note = relation.get("note")
        if not key or not note:
            continue
        normalized.setdefault(key, [])
        if note not in normalized[key]:
            normalized[key].append(note)

    return dict((key, "; ".join(notes)) for key, notes in normalized.items())


def merge_relationship_notes(primary, secondary):
    merged = {}
    for source in [primary or {}, secondary or {}]:
        for key, note in source.items():
            cleaned_key = clean_text(key)
            cleaned_note = clean_text(note)
            if not cleaned_key or not cleaned_note:
                continue
            merged.setdefault(cleaned_key, [])
            if cleaned_note not in merged[cleaned_key]:
                merged[cleaned_key].append(cleaned_note)
    return dict((key, "; ".join(notes)) for key, notes in merged.items())


def upsert_entity(entities, entity_type, value, evidence_fragment_indexes, confidence=None, normalized=None):
    cleaned_value = clean_text(value)
    if not cleaned_value:
        return None

    for entity in entities:
        if entity["type"] != entity_type:
            continue
        if entity.get("value", "").lower() != cleaned_value.lower():
            continue
        for index in evidence_fragment_indexes:
            if index not in entity["evidence_fragment_indexes"]:
                entity["evidence_fragment_indexes"].append(index)
        if confidence_rank(confidence) > confidence_rank(entity.get("confidence")):
            entity["confidence"] = confidence
        if normalized and not entity.get("normalized"):
            entity["normalized"] = normalized
        return entity["id"]

    entity_id = "e%s" % (len(entities) + 1)
    used_ids = set(item["id"] for item in entities)
    while entity_id in used_ids:
        entity_id = "e%s" % (len(entities) + 1)

    entities.append({
        "id": entity_id,
        "type": entity_type,
        "value": cleaned_value,
        "normalized": clean_text(normalized) or cleaned_value,
        "confidence": confidence,
        "evidence_fragment_indexes": list(evidence_fragment_indexes)
    })
    return entity_id


def upsert_relation(relations, entities, subject_id, predicate, object_id, object_value, confidence, evidence_fragment_indexes, note):
    entities_by_id, _ = build_entity_indexes(entities)
    subject_value = entity_label(entities_by_id[subject_id]) if subject_id in entities_by_id else None
    resolved_object_value = object_value
    if object_id and object_id in entities_by_id:
        resolved_object_value = entity_label(entities_by_id[object_id])

    relation_key = (subject_id, predicate, object_id or resolved_object_value, note)
    for relation in relations:
        existing_key = (
            relation.get("subject_id"),
            relation.get("predicate"),
            relation.get("object_id") or relation.get("object_value"),
            relation.get("note")
        )
        if existing_key != relation_key:
            continue
        for index in evidence_fragment_indexes:
            if index not in relation["evidence_fragment_indexes"]:
                relation["evidence_fragment_indexes"].append(index)
        if confidence_rank(confidence) > confidence_rank(relation.get("confidence")):
            relation["confidence"] = confidence
        return

    relations.append({
        "subject_id": subject_id,
        "subject_value": subject_value,
        "predicate": predicate,
        "object_id": object_id,
        "object_value": resolved_object_value,
        "confidence": confidence,
        "evidence_fragment_indexes": list(evidence_fragment_indexes),
        "note": clean_text(note)
    })


def augment_entities_and_relations(entities, relations, fragments):
    primary_handle_id = None
    former_handle_ids = []
    former_handle_indexes = []

    for index, fragment in enumerate(fragments, start=1):
        former_match = FORMER_HANDLE_RE.match(fragment)
        if former_match:
            former_handle_ids.append(
                upsert_entity(entities, "handle", former_match.group(1), [index], "high")
            )
            former_handle_indexes.append(index)

        slash_match = SLASH_PAIR_RE.match(fragment)
        if slash_match:
            handle_id = upsert_entity(entities, "handle", slash_match.group(1), [index], "high")
            group_id = upsert_entity(entities, "group", slash_match.group(2).rstrip("."), [index], "high")
            if handle_id:
                primary_handle_id = primary_handle_id or handle_id
            if handle_id and group_id:
                upsert_relation(relations, entities, handle_id, "member_of", group_id, None, "high", [index], None)

        compact_match = COMPACT_SIGNATURE_RE.match(fragment)
        if compact_match:
            handle_id = upsert_entity(entities, "handle", compact_match.group(1), [index], "high")
            upsert_entity(entities, "abbreviation", compact_match.group(2), [index], "medium")
            date_id = upsert_entity(entities, "date", compact_match.group(3), [index], "high")
            time_id = upsert_entity(entities, "time", compact_match.group(4), [index], "medium")
            if handle_id:
                primary_handle_id = primary_handle_id or handle_id
            if handle_id and date_id:
                upsert_relation(relations, entities, handle_id, "signed_on", date_id, None, "high", [index], None)
            if handle_id and time_id:
                upsert_relation(relations, entities, handle_id, "signed_at", time_id, None, "medium", [index], None)

    person_entities = [entity for entity in entities if entity["type"] == "person_name"]
    address_entities = [entity for entity in entities if entity["type"] == "address"]
    if len(person_entities) == 1 and len(address_entities) == 1:
        upsert_relation(
            relations,
            entities,
            person_entities[0]["id"],
            "contact_address_of",
            address_entities[0]["id"],
            None,
            "high",
            sorted(set(person_entities[0]["evidence_fragment_indexes"] + address_entities[0]["evidence_fragment_indexes"])),
            None
        )

    if primary_handle_id:
        for former_handle_id in former_handle_ids:
            if not former_handle_id or former_handle_id == primary_handle_id:
                continue
            upsert_relation(
                relations,
                entities,
                primary_handle_id,
                "alias_change",
                former_handle_id,
                None,
                "medium",
                former_handle_indexes,
                "Likely former alias inferred from the text."
            )


def extract_legacy_mentions(payload):
    mentions = []
    for value in payload.get("mentions", []) or []:
        normalized = normalize_mention_value(value)
        if normalized and not is_generic_mention(normalized) and normalized not in mentions:
            mentions.append(normalized)

    relationship_notes = payload.get("relationship_notes")
    if isinstance(relationship_notes, dict):
        for key in relationship_notes.keys():
            cleaned_key = clean_text(key)
            if cleaned_key and not is_generic_mention(cleaned_key) and cleaned_key not in mentions:
                mentions.append(cleaned_key)

    return mentions


def build_mentions(payload):
    mentions = []

    for entity in payload.get("entities", []) or []:
        mention = entity_label(entity)
        if mention and not is_generic_mention(mention) and mention not in mentions:
            mentions.append(mention)

    if mentions:
        return mentions

    return extract_legacy_mentions(payload)


def normalize_analysis_result(raw_payload, fragments):
    payload = raw_payload or {}
    fragment_count = len(fragments)
    summary = clean_text(payload.get("summary"))
    interpretation = normalize_interpretation(payload.get("interpretation"))

    if summary is None:
        summary = interpretation
    if interpretation is None:
        interpretation = summary

    entities = normalize_entities(payload.get("entities", []), fragment_count)
    relations = normalize_relations(payload.get("relations", []), entities, fragment_count)
    augment_entities_and_relations(entities, relations, fragments)
    legacy_relationship_notes = normalize_legacy_relationship_notes(payload.get("relationship_notes"))
    relationship_notes = merge_relationship_notes(build_relationship_notes(relations), legacy_relationship_notes)

    normalized = {
        "summary": summary,
        "tone": clean_text(payload.get("tone")),
        "language": normalize_language(payload.get("language")),
        "reconstructed_segments": normalize_reconstructed_segments(payload.get("reconstructed_segments", []), fragment_count),
        "entities": entities,
        "relations": relations,
        "interpretation": interpretation,
        "ambiguities": normalize_ambiguities(payload.get("ambiguities", [])),
        "confidence": normalize_confidence(payload.get("confidence")),
        "relationship_notes": relationship_notes
    }
    normalized["mentions"] = build_mentions(dict(payload, **normalized))
    return normalized


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

    normalized = normalize_analysis_result(copied, copied.get("input_text_fragments", []))
    copied.update(normalized)
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


def is_summary_current(summary_item, summary_path, module_item, config, input_text_hash):
    if not summary_path.exists():
        return False
    if summary_item.get("model_name") != config["ollama"]["model"]:
        return False
    if summary_item.get("prompt_version") != PROMPT_VERSION:
        return False
    if summary_item.get("input_text_hash") != input_text_hash:
        return False
    if summary_item.get("summary_status") == "skipped" and module_item.get("llm_decision") == "run":
        return False
    if summary_item.get("summary_status") == "done" and module_item.get("llm_decision") == "skip":
        return False
    if summary_item.get("summary_status") == "skipped":
        return summary_item.get("summary_skip_reason") == module_item.get("llm_reason")
    return summary_item.get("summary_status") == "done"


def build_skipped_payload(module_item, useful_text_fragments, config, input_text_hash):
    return {
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
        "language": "unknown",
        "reconstructed_segments": [],
        "entities": [],
        "relations": [],
        "interpretation": None,
        "ambiguities": [],
        "mentions": [],
        "relationship_notes": {},
        "confidence": None,
        "summarized_at": now_iso()
    }


def apply_payload_to_summary_state(summary_item, payload):
    summary_item["summary_status"] = payload["summary_status"]
    summary_item["summary_error"] = None
    summary_item["summary_skip_reason"] = payload["summary_skip_reason"]
    summary_item["tone"] = payload["tone"]
    summary_item["mentions"] = payload["mentions"]
    summary_item["summarized_at"] = payload["summarized_at"]


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
    selected_sources = set(args.source) if args.source else None

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
        if selected_sources and module_item.get("source_name") not in selected_sources:
            continue

        summary_item = find_item(summaries_state["items"], ("module_id",), {"module_id": module_item["module_id"]})
        if summary_item is None:
            continue

        summary_item["sha256"] = module_item.get("sha256")
        summary_item["source_name"] = module_item.get("source_name")
        summary_item["remote_path"] = module_item.get("remote_path")

        metadata_path = resolve_repo_path(module_item["metadata_path"])
        if not metadata_path.exists():
            summary_item["summary_status"] = "failed"
            summary_item["summary_error"] = "Metadata file is missing"
            save_state(summaries_state_path, summaries_state)
            continue

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        useful_text_fragments = metadata.get("useful_text_fragments", [])
        input_text_hash = sha256_text(json.dumps(useful_text_fragments, ensure_ascii=False))

        if summary_item["summary_status"] in ["done", "skipped"] and summary_item.get("summary_path") and not args.force:
            summary_path = resolve_repo_path(summary_item["summary_path"])
            if is_summary_current(summary_item, summary_path, module_item, config, input_text_hash):
                continue

        summary_item["model_name"] = config["ollama"]["model"]
        summary_item["prompt_version"] = PROMPT_VERSION
        summary_item["input_text_hash"] = input_text_hash

        try:
            if module_item["llm_decision"] == "skip" or not useful_text_fragments:
                payload = build_skipped_payload(module_item, useful_text_fragments, config, input_text_hash)
                output_path = write_summary_artifact(config, module_item, payload)
                apply_payload_to_summary_state(summary_item, payload)
                summary_item["summary_path"] = relative_repo_path(output_path)
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
                analysis = normalize_analysis_result(result, useful_text_fragments)
                payload = {
                    "module_id": module_item["module_id"],
                    "sha256": module_item["sha256"],
                    "summary_status": "done",
                    "summary_skip_reason": None,
                    "model_name": config["ollama"]["model"],
                    "prompt_version": PROMPT_VERSION,
                    "input_text_hash": input_text_hash,
                    "input_text_fragments": useful_text_fragments,
                    "summary": analysis["summary"],
                    "tone": analysis["tone"],
                    "language": analysis["language"],
                    "reconstructed_segments": analysis["reconstructed_segments"],
                    "entities": analysis["entities"],
                    "relations": analysis["relations"],
                    "interpretation": analysis["interpretation"],
                    "ambiguities": analysis["ambiguities"],
                    "mentions": analysis["mentions"],
                    "relationship_notes": analysis["relationship_notes"],
                    "confidence": analysis["confidence"],
                    "summarized_at": now_iso()
                }

            output_path = write_summary_artifact(config, module_item, payload)
            apply_payload_to_summary_state(summary_item, payload)
            summary_item["summary_path"] = relative_repo_path(output_path)
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
