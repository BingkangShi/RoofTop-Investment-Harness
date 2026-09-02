"""Mandatory persistence gateways for externally acquired documents and event graphs.

Production collectors must call these gateways after a successful external query.
The raw response is stored before normalized rows become visible to readers.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .db import DATA_LAKE, connect, ensure_data_lake, initialize


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)[:80] or "query"


def persist_source_documents(source_code: str, dataset: str, query_key: str,
                             documents: Iterable[dict], raw_payload: bytes | str) -> dict:
    """Persist one external document query into raw storage and the relational DB."""
    initialize()
    ensure_data_lake()
    captured = _now()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    raw = raw_payload.encode("utf-8") if isinstance(raw_payload, str) else raw_payload
    raw_dir = DATA_LAKE / "raw" / ("news" if dataset == "news" else "documents") / _safe(source_code)
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{_safe(dataset)}_{_safe(query_key)}_{stamp}.json"
    raw_path.write_bytes(raw)
    items = list(documents)

    with closing(connect()) as conn:
        source = conn.execute("SELECT id FROM data_sources WHERE code=?", (source_code,)).fetchone()
        if not source:
            raise ValueError(f"unknown data source: {source_code}")
        source_id = int(source[0])
        ids = []
        for item in items:
            title = str(item.get("title") or "未命名文档").strip()
            body = str(item.get("body") or item.get("content") or "").strip()
            source_url = str(item.get("source_url") or item.get("url") or "").strip() or None
            stable = source_url or f"{title}\n{body}"
            content_hash = hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest()
            doc_key = hashlib.sha256(f"{source_code}\n{stable}".encode("utf-8")).hexdigest()
            metadata = item.get("metadata") or {}
            conn.execute(
                """INSERT INTO source_documents
                   (doc_key,document_type,title,body,source_url,source_name,published_at,observed_at,
                    captured_at,raw_path,content_hash,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(doc_key) DO UPDATE SET document_type=excluded.document_type,
                     title=excluded.title,body=excluded.body,source_url=excluded.source_url,
                     source_name=excluded.source_name,published_at=excluded.published_at,
                     observed_at=excluded.observed_at,captured_at=excluded.captured_at,
                     raw_path=excluded.raw_path,content_hash=excluded.content_hash,
                     metadata_json=excluded.metadata_json""",
                (doc_key, str(item.get("document_type") or dataset), title, body, source_url,
                 str(item.get("source_name") or source_code), item.get("published_at"),
                 item.get("observed_at"), captured, str(raw_path), content_hash,
                 json.dumps(metadata, ensure_ascii=False, sort_keys=True)),
            )
            document_id = int(conn.execute("SELECT id FROM source_documents WHERE doc_key=?", (doc_key,)).fetchone()[0])
            ids.append(document_id)
            latest_version = conn.execute(
                "SELECT content_hash FROM source_document_versions WHERE document_id=? ORDER BY id DESC LIMIT 1",
                (document_id,),
            ).fetchone()
            if not latest_version or latest_version["content_hash"] != content_hash:
                conn.execute(
                    """INSERT INTO source_document_versions
                       (document_id,captured_at,title,body,content_hash,metadata_json,raw_path)
                       VALUES(?,?,?,?,?,?,?)""",
                    (document_id, captured, title, body, content_hash,
                     json.dumps(metadata, ensure_ascii=False, sort_keys=True), str(raw_path)),
                )
        conn.execute(
            """INSERT INTO ingestion_runs
               (source_id,dataset,asset_symbol,started_at,finished_at,status,row_count,raw_path)
               VALUES(?,?,?,?,?,'SUCCESS',?,?)""",
            (source_id, dataset, query_key, captured, _now(), len(items), str(raw_path)),
        )
        conn.execute("UPDATE data_sources SET health_status='HEALTHY',last_success_at=?,last_error=NULL WHERE id=?",
                     (captured, source_id))
        conn.commit()
    return {"source": source_code, "dataset": dataset, "rows": len(ids), "ids": ids,
            "raw_path": str(raw_path), "storage": "local_sqlite"}


def persist_event_graph(graph: dict, nodes: Iterable[dict], edges: Iterable[dict]) -> dict:
    """Transactionally upsert an event-graph snapshot and refresh its Neo4j Cypher mirror."""
    initialize()
    now = _now()
    graph_key = str(graph["graph_key"]).strip()
    if not graph_key:
        raise ValueError("graph_key is required")
    node_items = list(nodes)
    edge_items = list(edges)
    snapshot_payload = {"graph": graph, "nodes": node_items, "edges": edge_items, "captured_at": now}
    snapshot_json = json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True)
    snapshot_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
    snapshot_dir = DATA_LAKE / "graphs" / "snapshots" / _safe(graph_key)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"{datetime.now():%Y%m%dT%H%M%S%f}_{snapshot_hash[:12]}.json"
    snapshot_path.write_text(snapshot_json, encoding="utf-8")
    node_keys = [str(item["node_key"]).strip() for item in node_items]
    if len(node_keys) != len(set(node_keys)) or any(not key for key in node_keys):
        raise ValueError("node_key values must be non-empty and unique within a graph")

    with closing(connect()) as conn:
        conn.execute(
            """INSERT INTO event_graphs(graph_key,title,thesis,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(graph_key) DO UPDATE SET title=excluded.title,
               thesis=excluded.thesis,status=excluded.status,updated_at=excluded.updated_at""",
            (graph_key, str(graph.get("title") or graph_key), str(graph.get("thesis") or ""),
             str(graph.get("status") or "UNVERIFIED"), now, now),
        )
        graph_id = int(conn.execute("SELECT id FROM event_graphs WHERE graph_key=?", (graph_key,)).fetchone()[0])
        conn.execute("DELETE FROM graph_edges WHERE graph_id=?", (graph_id,))
        for item in node_items:
            conn.execute(
                """INSERT INTO graph_nodes
                   (graph_id,node_key,node_type,label,fact_opinion,properties_json,relational_ref,source_url,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(graph_id,node_key) DO UPDATE SET
                   node_type=excluded.node_type,label=excluded.label,fact_opinion=excluded.fact_opinion,
                   properties_json=excluded.properties_json,relational_ref=excluded.relational_ref,
                   source_url=excluded.source_url,observed_at=excluded.observed_at""",
                (graph_id, str(item["node_key"]), str(item.get("node_type") or "EVENT"),
                 str(item.get("label") or item["node_key"]), str(item.get("fact_opinion") or "待验证假设"),
                 json.dumps(item.get("properties") or {}, ensure_ascii=False, sort_keys=True),
                 item.get("relational_ref"), item.get("source_url"), item.get("observed_at")),
            )
        if node_keys:
            placeholders = ",".join("?" for _ in node_keys)
            conn.execute(f"DELETE FROM graph_nodes WHERE graph_id=? AND node_key NOT IN ({placeholders})",
                         (graph_id, *node_keys))
        else:
            conn.execute("DELETE FROM graph_nodes WHERE graph_id=?", (graph_id,))
        node_ids = {row["node_key"]: int(row["id"]) for row in conn.execute(
            "SELECT id,node_key FROM graph_nodes WHERE graph_id=?", (graph_id,))}
        for item in edge_items:
            from_key, to_key = str(item["from"]), str(item["to"])
            if from_key not in node_ids or to_key not in node_ids:
                raise ValueError(f"edge references an unknown node: {from_key} -> {to_key}")
            conn.execute(
                """INSERT INTO graph_edges
                   (graph_id,from_node_id,to_node_id,relation_type,confidence,properties_json)
                   VALUES(?,?,?,?,?,?)""",
                (graph_id, node_ids[from_key], node_ids[to_key], str(item.get("relation_type") or "RELATES_TO"),
                 float(item.get("confidence", 0.5)),
                 json.dumps(item.get("properties") or {}, ensure_ascii=False, sort_keys=True)),
            )
        snapshot_id = int(conn.execute(
            """INSERT INTO graph_snapshots(graph_id,captured_at,content_hash,payload_json,snapshot_path)
               VALUES(?,?,?,?,?)""",
            (graph_id, now, snapshot_hash, snapshot_json, str(snapshot_path)),
        ).lastrowid)
        conn.commit()

    # The SQLite graph is authoritative; this durable Cypher file is the idempotent
    # bridge to Neo4j Community whenever that optional service is running.
    from .graph_export import export_cypher
    mirror_path = export_cypher()
    return {"graph_id": graph_id, "graph_key": graph_key, "snapshot_id": snapshot_id,
            "snapshot_path": str(snapshot_path), "nodes": len(node_items),
            "edges": len(edge_items), "storage": "local_property_graph",
            "neo4j_cypher_mirror": str(mirror_path)}
