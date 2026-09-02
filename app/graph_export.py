"""Export the local property graph as idempotent Cypher for Neo4j Community."""

import json
from contextlib import closing

from .db import DATA_LAKE, connect, initialize


def _cypher_string(value) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def export_cypher():
    initialize()
    target = DATA_LAKE / "graphs" / "event_graphs.cypher"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["CREATE CONSTRAINT graph_key IF NOT EXISTS FOR (g:EventGraph) REQUIRE g.key IS UNIQUE;",
             "CREATE CONSTRAINT node_uid IF NOT EXISTS FOR (n:EventNode) REQUIRE n.uid IS UNIQUE;"]
    with closing(connect()) as conn:
        for graph in conn.execute("SELECT * FROM event_graphs ORDER BY id"):
            lines.append(f"MERGE (g:EventGraph {{key: {_cypher_string(graph['graph_key'])}}}) SET g.title={_cypher_string(graph['title'])}, g.status={_cypher_string(graph['status'])};")
        for node in conn.execute("SELECT n.*,g.graph_key FROM graph_nodes n JOIN event_graphs g ON g.id=n.graph_id ORDER BY n.id"):
            uid = f"{node['graph_key']}::{node['node_key']}"
            lines.append(f"MERGE (n:EventNode {{uid: {_cypher_string(uid)}}}) SET n.label={_cypher_string(node['label'])}, n.type={_cypher_string(node['node_type'])}, n.fact_opinion={_cypher_string(node['fact_opinion'])}, n.relational_ref={_cypher_string(node['relational_ref'] or '')}, n.source_url={_cypher_string(node['source_url'] or '')}, n.observed_at={_cypher_string(node['observed_at'] or '')}, n.properties_json={_cypher_string(node['properties_json'])};")
            lines.append(f"MATCH (g:EventGraph {{key: {_cypher_string(node['graph_key'])}}}),(n:EventNode {{uid: {_cypher_string(uid)}}}) MERGE (g)-[:CONTAINS]->(n);")
        for edge in conn.execute("""SELECT e.*,g.graph_key,a.node_key AS from_key,b.node_key AS to_key
                                    FROM graph_edges e JOIN event_graphs g ON g.id=e.graph_id
                                    JOIN graph_nodes a ON a.id=e.from_node_id JOIN graph_nodes b ON b.id=e.to_node_id"""):
            from_uid = f"{edge['graph_key']}::{edge['from_key']}"
            to_uid = f"{edge['graph_key']}::{edge['to_key']}"
            lines.append(f"MATCH (a:EventNode {{uid: {_cypher_string(from_uid)}}}),(b:EventNode {{uid: {_cypher_string(to_uid)}}}) MERGE (a)-[r:RELATES_TO {{kind: {_cypher_string(edge['relation_type'])}}}]->(b) SET r.confidence={edge['confidence']};")
    temporary = target.with_suffix(".cypher.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


if __name__ == "__main__":
    print(export_cypher())
