import csv
import os
import time
from typing import Any, Dict, List

import psycopg2

from adapters.base import GraphDBAdapter, LoadResult

BATCH_SIZE = 300
GRAPH_NAME = "graphbench"


def _cypher_literal(value) -> str:
    """Render a Python value as a Cypher literal for inlining into a query."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _row_to_map_literal(row: Dict[str, Any]) -> str:
    parts = [f"{k}: {_cypher_literal(v)}" for k, v in row.items()]
    return "{" + ", ".join(parts) + "}"


class AgeAdapter(GraphDBAdapter):
    name = "age"

    def __init__(self):
        self.host = os.environ.get("AGE_HOST", "localhost")
        self.port = os.environ.get("AGE_PORT", "5433")
        self.dbname = os.environ.get("AGE_DBNAME", "graphbench")
        self.user = os.environ.get("AGE_USER", "age")
        self.password = os.environ["AGE_PASSWORD"]
        self.conn = None

    # ---- connection -----------------------------------------------------

    def connect(self) -> None:
        self.conn = psycopg2.connect(
            host=self.host, port=self.port, dbname=self.dbname,
            user=self.user, password=self.password,
        )
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute("LOAD 'age';")
            cur.execute('SET search_path = ag_catalog, "$user", public;')
            cur.execute("SELECT count(*) FROM ag_graph WHERE name = %s;", (GRAPH_NAME,))
            exists = cur.fetchone()[0] > 0
            if not exists:
                cur.execute("SELECT create_graph(%s);", (GRAPH_NAME,))

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def _cursor(self):
        cur = self.conn.cursor()
        cur.execute("LOAD 'age';")
        cur.execute('SET search_path = ag_catalog, "$user", public;')
        return cur

    def _run_cypher(self, cypher_body: str, return_cols: str = "(v agtype)") -> List[Any]:
        sql = f"SELECT * FROM cypher('{GRAPH_NAME}', $$ {cypher_body} $$) AS {return_cols};"
        with self._cursor() as cur:
            cur.execute(sql)
            try:
                return cur.fetchall()
            except psycopg2.ProgrammingError:
                return []  # statement had no results (e.g. a bare CREATE)

    def clear(self) -> None:
        while True:
            rows = self._run_cypher(
                "MATCH (n) WITH n LIMIT 500 DETACH DELETE n RETURN count(n)",
                "(c agtype)",
            )
            deleted = int(str(rows[0][0]).split("::")[0]) if rows else 0
            if deleted == 0:
                break

    # ---- loading ----------------------------------------------------

    def load(self, edges_path: str, profiles_path: str) -> LoadResult:
        start = time.perf_counter()

        node_count = 0
        with open(profiles_path, newline="") as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                batch.append({
                    "node_id": int(row["node_id"]),
                    "public": self._to_int_or_none(row["public"]),
                    "gender": self._to_int_or_none(row["gender"]),
                    "age": self._to_int_or_none(row["age"]),
                    "region": row["region"] or None,
                    "completion_percentage": self._to_int_or_none(row["completion_percentage"]),
                })
                if len(batch) >= BATCH_SIZE:
                    self._load_node_batch(batch)
                    node_count += len(batch)
                    batch = []
            if batch:
                self._load_node_batch(batch)
                node_count += len(batch)

        # index node_id BEFORE edges load -- see cypher_base.py for why this
        # ordering matters (unindexed MATCH during edge creation is a full
        # scan and can hang/OOM at this dataset size).
        self._ensure_node_id_index()

        rel_count = 0
        with open(edges_path, newline="") as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                batch.append({"src": int(row["src_id"]), "dst": int(row["dst_id"])})
                if len(batch) >= BATCH_SIZE:
                    self._load_edge_batch(batch)
                    rel_count += len(batch)
                    batch = []
            if batch:
                self._load_edge_batch(batch)
                rel_count += len(batch)

        elapsed = time.perf_counter() - start
        return LoadResult(
            node_count=node_count,
            relationship_count=rel_count,
            load_time_seconds=elapsed,
            nodes_per_second=node_count / elapsed if elapsed > 0 else 0,
            relationships_per_second=rel_count / elapsed if elapsed > 0 else 0,
        )

    def _load_node_batch(self, batch: List[Dict[str, Any]]) -> None:
        rows_literal = "[" + ", ".join(_row_to_map_literal(r) for r in batch) + "]"
        cypher_body = f"""
        UNWIND {rows_literal} AS row
        CREATE (u:User {{node_id: row.node_id, public: row.public, gender: row.gender,
                          age: row.age, region: row.region,
                          completion_percentage: row.completion_percentage}})
        """
        self._run_cypher(cypher_body)

    def _load_edge_batch(self, batch: List[Dict[str, Any]]) -> None:
        rows_literal = "[" + ", ".join(_row_to_map_literal(r) for r in batch) + "]"
        cypher_body = f"""
        UNWIND {rows_literal} AS row
        MATCH (a:User {{node_id: row.src}}), (b:User {{node_id: row.dst}})
        CREATE (a)-[:FRIEND]->(b)
        """
        self._run_cypher(cypher_body)

    @staticmethod
    def _to_int_or_none(v: str):
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except ValueError:
            return None

    # ---- indexes ------------------------------------------------------

    def _ensure_node_id_index(self) -> None:
        try:
            with self._cursor() as cur:
                cur.execute(
                    f'CREATE INDEX IF NOT EXISTS idx_user_node_id '
                    f'ON {GRAPH_NAME}."User" '
                    f'USING btree (ag_catalog.agtype_access_operator(properties, \'"node_id"\'));'
                )
            print(f"[{self.name}] node_id index created (pre-edge-load)")
        except Exception as e:
            print(f"[{self.name}] node_id index warning (may already exist or "
                  f"AGE version syntax differs): {e}")

    def create_indexes(self) -> None:
        self._ensure_node_id_index()
        for prop in ("gender", "age"):
            try:
                with self._cursor() as cur:
                    cur.execute(
                        f'CREATE INDEX IF NOT EXISTS idx_user_{prop} '
                        f'ON {GRAPH_NAME}."User" '
                        f'USING btree (ag_catalog.agtype_access_operator(properties, \'"{prop}"\'));'
                    )
            except Exception as e:
                print(f"[{self.name}] index warning for {prop}: {e}")
        print(f"[{self.name}] indexed: User.node_id, User.gender, User.age "
              f"(as Postgres btree expression indexes via agtype_access_operator on the "
              f"properties column)")

    # ---- sampling ----------------------------------------------------

    def sample_node_ids(self, n: int) -> List[int]:
        rows = self._run_cypher(
            f"MATCH (u:User) RETURN u.node_id ORDER BY rand() LIMIT {n}",
            "(id agtype)",
        )
        return [int(str(r[0]).split("::")[0]) for r in rows]

    # ---- workload queries ----------------------------------------------

    def hop_query(self, start_node_id: int, hops: int) -> Any:
        cypher_body = f"""
        MATCH (start:User {{node_id: {start_node_id}}})-[:FRIEND*{hops}]->(reached:User)
        RETURN count(DISTINCT reached)
        """
        return self._run_cypher(cypher_body, "(reached_count agtype)")

    def point_lookup(self, node_id: int) -> Any:
        return self._run_cypher(f"MATCH (u:User {{node_id: {node_id}}}) RETURN u")

    def filtered_lookup(self, gender: int, min_age: int) -> Any:
        cypher_body = f"""
        MATCH (u:User)
        WHERE u.gender = {gender} AND u.age >= {min_age}
        RETURN u.node_id
        LIMIT 50
        """
        return self._run_cypher(cypher_body, "(id agtype)")

    def aggregation(self) -> Any:
        cypher_body = """
        MATCH (u:User)
        RETURN u.gender AS gender, count(*) AS cnt
        ORDER BY gender
        """
        return self._run_cypher(cypher_body, "(gender agtype, cnt agtype)")

    def mixed_workload_op(self, op_seed: int, write_ratio: float) -> Any:
        is_write = (op_seed % 1000) / 1000.0 < write_ratio
        node_id = op_seed % 11250  # bounded by trimmed node count; adjust if dataset size changes
        if is_write:
            cypher_body = f"""
            MATCH (u:User {{node_id: {node_id}}})
            SET u.completion_percentage = coalesce(u.completion_percentage, 0) + 1
            RETURN u.node_id
            """
            return self._run_cypher(cypher_body, "(id agtype)")
        else:
            return self._run_cypher(f"MATCH (u:User {{node_id: {node_id}}}) RETURN u")

    # ---- footprint ----------------------------------------------------

    def footprint(self) -> Dict[str, Any]:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT sum(pg_total_relation_size(quote_ident(table_schema)||'.'||"
                    "quote_ident(table_name))) FROM information_schema.tables "
                    "WHERE table_schema = %s;",
                    (GRAPH_NAME,),
                )
                size_bytes = cur.fetchone()[0]
            return {"stored_size_bytes": size_bytes, "memory_usage": "not observable"}
        except Exception:
            return {"stored_size": "not observable", "memory_usage": "not observable"}
