import argparse
import json
import xml.etree.ElementTree as ET

from common_config import get_path, load_config, prepare_runtime_directories, resolve_repo_path
from common_state import ensure_state_files, load_state
from common_utils import atomic_write_json, build_logger, escape_dot, normalize_handle, now_iso


def parse_args():
    parser = argparse.ArgumentParser(description="Build relationship graphs from parsed metadata and summaries.")
    parser.add_argument("--config", default=None, help="Path to a config JSON file.")
    return parser.parse_args()


def node_key(handle):
    return handle.lower()


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
GRAPH_ENTITY_TYPES = {
    "handle",
    "group",
    "person_name"
}


def register_node(nodes, handle):
    normalized = normalize_handle(handle)
    if not normalized:
        return None

    key = node_key(normalized)
    if key not in nodes:
        nodes[key] = {
            "id": key,
            "label": normalized
        }
    return key


def add_edge(edges, source_id, target_id, kind):
    if not source_id or not target_id or source_id == target_id:
        return

    edge_key = (source_id, target_id, kind)
    if edge_key not in edges:
        edges[edge_key] = {
            "source": source_id,
            "target": target_id,
            "kind": kind,
            "weight": 0
        }
    edges[edge_key]["weight"] += 1


def is_generic_mention(value):
    if not isinstance(value, str):
        return False

    cleaned = value.strip().lower().strip(".:,;!?()[]{}")
    return cleaned in GENERIC_MENTION_TERMS


def normalize_summary_mention(value):
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned and not is_generic_mention(cleaned):
            return cleaned
        return None
    if not isinstance(value, dict):
        return None

    for key in ["entity", "name", "value", "phone", "address", "note", "context"]:
        candidate = value.get(key)
        if isinstance(candidate, str):
            cleaned = candidate.strip()
            if cleaned and not is_generic_mention(cleaned):
                return cleaned

    return None


def normalize_entity_type(value):
    if not isinstance(value, str):
        return None

    cleaned = value.strip().lower()
    aliases = {
        "alias": "handle",
        "artist_handle": "handle",
        "nickname": "handle",
        "crew": "group",
        "person": "person_name",
        "real_name": "person_name",
        "name": "person_name"
    }
    return aliases.get(cleaned, cleaned)


def iter_structured_entity_mentions(summary_payload):
    seen = set()
    for raw_entity in summary_payload.get("entities", []):
        if not isinstance(raw_entity, dict):
            continue
        entity_type = normalize_entity_type(raw_entity.get("type"))
        if entity_type not in GRAPH_ENTITY_TYPES:
            continue
        mention = normalize_summary_mention(raw_entity.get("normalized") or raw_entity.get("value") or raw_entity.get("name"))
        if mention and mention not in seen:
            seen.add(mention)
            yield mention


def iter_summary_mentions(summary_payload):
    structured_mentions = list(iter_structured_entity_mentions(summary_payload))
    if structured_mentions:
        for mention in structured_mentions:
            yield mention
        return

    seen = set()

    for raw_mention in summary_payload.get("mentions", []):
        mention = normalize_summary_mention(raw_mention)
        if mention and mention not in seen:
            seen.add(mention)
            yield mention

    relationship_notes = summary_payload.get("relationship_notes")
    if isinstance(relationship_notes, dict):
        for key, nested in relationship_notes.items():
            key_mention = normalize_summary_mention(key)
            if key_mention and key_mention not in seen:
                seen.add(key_mention)
                yield key_mention

            if isinstance(nested, dict):
                nested_mention = normalize_summary_mention(nested)
                if nested_mention and nested_mention not in seen:
                    seen.add(nested_mention)
                    yield nested_mention
            elif isinstance(nested, list):
                for item in nested:
                    nested_mention = normalize_summary_mention(item)
                    if nested_mention and nested_mention not in seen:
                        seen.add(nested_mention)
                        yield nested_mention
    elif isinstance(relationship_notes, list):
        for nested in relationship_notes:
            if not isinstance(nested, dict):
                continue
            mention = normalize_summary_mention(nested)
            if mention and mention not in seen:
                seen.add(mention)
                yield mention


def write_dot(path, nodes, edges):
    lines = ["digraph MODialogues {"]
    for node in nodes.values():
        lines.append('  "%s" [label="%s"];' % (escape_dot(node["id"]), escape_dot(node["label"])))
    for edge in edges.values():
        lines.append(
            '  "%s" -> "%s" [label="%s:%s"];' % (
                escape_dot(edge["source"]),
                escape_dot(edge["target"]),
                escape_dot(edge["kind"]),
                edge["weight"]
            )
        )
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_gexf(path, nodes, edges):
    root = ET.Element("gexf", attrib={
        "xmlns": "http://www.gexf.net/1.2draft",
        "version": "1.2"
    })
    graph = ET.SubElement(root, "graph", attrib={
        "mode": "static",
        "defaultedgetype": "directed"
    })

    attributes = ET.SubElement(graph, "attributes", attrib={
        "class": "edge",
        "mode": "static"
    })
    ET.SubElement(attributes, "attribute", attrib={"id": "kind", "title": "kind", "type": "string"})

    nodes_element = ET.SubElement(graph, "nodes")
    for node in nodes.values():
        ET.SubElement(nodes_element, "node", attrib={"id": node["id"], "label": node["label"]})

    edges_element = ET.SubElement(graph, "edges")
    for index, edge in enumerate(edges.values()):
        edge_element = ET.SubElement(edges_element, "edge", attrib={
            "id": str(index),
            "source": edge["source"],
            "target": edge["target"],
            "weight": str(edge["weight"])
        })
        attvalues = ET.SubElement(edge_element, "attvalues")
        ET.SubElement(attvalues, "attvalue", attrib={"for": "kind", "value": edge["kind"]})

    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def main():
    args = parse_args()
    config = load_config(args.config)
    prepare_runtime_directories(config)
    ensure_state_files([
        get_path(config, "remote_files_state"),
        get_path(config, "modules_state"),
        get_path(config, "summaries_state")
    ])

    logger = build_logger("build_graph", get_path(config, "logs_dir"))
    modules_state = load_state(get_path(config, "modules_state"))
    summaries_state = load_state(get_path(config, "summaries_state"))
    summaries_by_module_id = dict((item["module_id"], item) for item in summaries_state["items"] if item.get("module_id"))

    nodes = {}
    edges = {}

    for module_item in modules_state["items"]:
        if module_item.get("parse_status") != "done":
            continue
        if not module_item.get("metadata_path"):
            continue

        metadata_path = resolve_repo_path(module_item["metadata_path"])
        if not metadata_path.exists():
            continue

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        source_id = register_node(nodes, metadata.get("author_guess"))
        if not source_id:
            continue

        for greet in metadata.get("greets_rule_based", []):
            target_id = register_node(nodes, greet)
            add_edge(edges, source_id, target_id, "greet")

        summary_item = summaries_by_module_id.get(module_item.get("module_id"))
        if summary_item is None:
            continue
        if summary_item.get("summary_status") != "done":
            continue
        if not summary_item.get("summary_path"):
            continue

        summary_path = resolve_repo_path(summary_item["summary_path"])
        if not summary_path.exists():
            continue

        summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
        for mention in iter_summary_mentions(summary_payload):
            target_id = register_node(nodes, mention)
            add_edge(edges, source_id, target_id, "mention")

    graph_payload = {
        "generated_at": now_iso(),
        "nodes": sorted(nodes.values(), key=lambda item: item["id"]),
        "edges": sorted(edges.values(), key=lambda item: (item["source"], item["target"], item["kind"]))
    }

    graphs_dir = get_path(config, "graphs_dir")
    json_path = graphs_dir / "handles_graph.json"
    dot_path = graphs_dir / "handles_graph.dot"
    gexf_path = graphs_dir / "handles_graph.gexf"

    atomic_write_json(json_path, graph_payload)
    write_dot(dot_path, nodes, edges)
    write_gexf(gexf_path, nodes, edges)

    logger.info("Graph exported: %s nodes, %s edges", len(nodes), len(edges))


if __name__ == "__main__":
    main()
