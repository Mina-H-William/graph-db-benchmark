import csv
import time
from typing import Any, Dict, List

from neo4j import GraphDatabase

from adapters.base import GraphDBAdapter, LoadResult

BATCH_SIZE = 300 
                   


class CypherAdapter(GraphDBAdapter):
    name = "cypher-base" 

    def __init__(self, uri: str, user: str, password: str):
        self.uri = uri
        self.user = user
        self.password = password
        self.driver = None

    # ---- connection ----------------------------------------------------

    def connect(self) -> None:
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        self.driver.verify_connectivity()

    def close(self) -> None:
        if self.driver:
            self.driver.close()

    def clear(self) -> None:
        with self.driver.session() as session:
            while True:
                result = session.run(
                    "MATCH (n) WITH n LIMIT 500 DETACH DELETE n RETURN count(n) AS c"
                )
                deleted = result.single()["c"]
                if deleted == 0:
                    break

    # ---- loading ----------------------------------------------------------

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
        query = """
        UNWIND $rows AS row
        CREATE (u:User {
            node_id: row.node_id,
            public: row.public,
            gender: row.gender,
            age: row.age,
            region: row.region,
            completion_percentage: row.completion_percentage
        })
        """
        with self.driver.session() as session:
            session.run(query, rows=batch)

    def _load_edge_batch(self, batch: List[Dict[str, Any]]) -> None:
        query = """
        UNWIND $rows AS row
        MATCH (a:User {node_id: row.src}), (b:User {node_id: row.dst})
        CREATE (a)-[:FRIEND]->(b)
        """
        with self.driver.session() as session:
            session.run(query, rows=batch)

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
        """Called internally by load(), before edges are created. Subclasses
        (e.g. Memgraph) override this if their index syntax differs."""
        with self.driver.session() as session:
            session.run("CREATE INDEX user_node_id IF NOT EXISTS FOR (u:User) ON (u.node_id)")
        print(f"[{self.name}] node_id index created (pre-edge-load)")

    def create_indexes(self) -> None:
        stmts = [
            "CREATE INDEX user_node_id IF NOT EXISTS FOR (u:User) ON (u.node_id)",
            "CREATE INDEX user_gender IF NOT EXISTS FOR (u:User) ON (u.gender)",
            "CREATE INDEX user_age IF NOT EXISTS FOR (u:User) ON (u.age)",
        ]
        with self.driver.session() as session:
            for stmt in stmts:
                session.run(stmt)
        print(f"[{self.name}] indexed: User.node_id, User.gender, User.age")

    # ---- sampling ----------------------------------------------------

    def sample_node_ids(self, n: int) -> List[int]:
        query = "MATCH (u:User) RETURN u.node_id AS id ORDER BY rand() LIMIT $n"
        with self.driver.session() as session:
            result = session.run(query, n=n)
            return [r["id"] for r in result]

    # ---- workload queries ----------------------------------------------

    def hop_query(self, start_node_id: int, hops: int) -> Any:
        query = f"""
        MATCH (start:User {{node_id: $id}})-[:FRIEND*{hops}]->(reached:User)
        RETURN count(DISTINCT reached) AS reached_count
        """
        with self.driver.session() as session:
            return session.run(query, id=start_node_id).single()

    def point_lookup(self, node_id: int) -> Any:
        query = "MATCH (u:User {node_id: $id}) RETURN u"
        with self.driver.session() as session:
            return session.run(query, id=node_id).single()

    def filtered_lookup(self, gender: int, min_age: int) -> Any:
        query = """
        MATCH (u:User)
        WHERE u.gender = $gender AND u.age >= $min_age
        RETURN u.node_id AS id
        LIMIT 50
        """
        with self.driver.session() as session:
            return list(session.run(query, gender=gender, min_age=min_age))

    def aggregation(self) -> Any:
        query = """
        MATCH (u:User)
        RETURN u.gender AS gender, count(*) AS cnt
        ORDER BY gender
        """
        with self.driver.session() as session:
            return list(session.run(query))

    def mixed_workload_op(self, op_seed: int, write_ratio: float) -> Any:
        is_write = (op_seed % 1000) / 1000.0 < write_ratio
        node_id = op_seed % 11250  
        with self.driver.session() as session:
            if is_write:
                query = """
                MATCH (u:User {node_id: $id})
                SET u.completion_percentage = coalesce(u.completion_percentage, 0) + 1
                RETURN u.node_id
                """
                return session.run(query, id=node_id).single()
            else:
                query = "MATCH (u:User {node_id: $id}) RETURN u"
                return session.run(query, id=node_id).single()

    # ---- footprint ----------------------------------------------------

    def footprint(self) -> Dict[str, Any]:
        # Overridden per-platform where a size/memory query is available.
        return {"stored_size": "not observable", "memory_usage": "not observable"}
