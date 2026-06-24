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
    summaries_by_sha = dict((item["sha256"], item) for item in summaries_state["items"])

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

        summary_item = summaries_by_sha.get(module_item["sha256"])
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
        for mention in summary_payload.get("mentions", []):
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
