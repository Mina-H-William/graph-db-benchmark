import csv
import os
import time
from typing import Any, Dict, List

from arango import ArangoClient

from adapters.base import GraphDBAdapter, LoadResult

BATCH_SIZE = 500


class ArangoDBAdapter(GraphDBAdapter):
    name = "arangodb"

    def __init__(self):
        self.url = os.environ.get("ARANGO_URL", "http://localhost:8529")
        self.password = os.environ["ARANGO_PASSWORD"]
        self.db_name = os.environ.get("ARANGO_DB_NAME", "graphbench")
        self.client = None
        self.db = None

    # ---- connection -----------------------------------------------------

    def connect(self) -> None:
        self.client = ArangoClient(hosts=self.url)
        sys_db = self.client.db("_system", username="root", password=self.password)
        if not sys_db.has_database(self.db_name):
            sys_db.create_database(self.db_name)
        self.db = self.client.db(self.db_name, username="root", password=self.password)

        if not self.db.has_collection("users"):
            self.db.create_collection("users")
        if not self.db.has_collection("friend"):
            self.db.create_collection("friend", edge=True)

    def close(self) -> None:
        pass

    def clear(self) -> None:
        self.db.collection("friend").truncate()
        self.db.collection("users").truncate()

    # ---- loading ----------------------------------------------------

    def load(self, edges_path: str, profiles_path: str) -> LoadResult:
        start = time.perf_counter()
        users = self.db.collection("users")
        friend = self.db.collection("friend")

        node_count = 0
        with open(profiles_path, newline="") as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                batch.append({
                    "_key": row["node_id"],
                    "node_id": int(row["node_id"]),
                    "public": self._to_int_or_none(row["public"]),
                    "gender": self._to_int_or_none(row["gender"]),
                    "age": self._to_int_or_none(row["age"]),
                    "region": row["region"] or None,
                    "completion_percentage": self._to_int_or_none(row["completion_percentage"]),
                })
                if len(batch) >= BATCH_SIZE:
                    users.insert_many(batch)
                    node_count += len(batch)
                    batch = []
            if batch:
                users.insert_many(batch)
                node_count += len(batch)


        rel_count = 0
        with open(edges_path, newline="") as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                batch.append({
                    "_from": f"users/{row['src_id']}",
                    "_to": f"users/{row['dst_id']}",
                })
                if len(batch) >= BATCH_SIZE:
                    friend.insert_many(batch)
                    rel_count += len(batch)
                    batch = []
            if batch:
                friend.insert_many(batch)
                rel_count += len(batch)

        elapsed = time.perf_counter() - start
        return LoadResult(
            node_count=node_count,
            relationship_count=rel_count,
            load_time_seconds=elapsed,
            nodes_per_second=node_count / elapsed if elapsed > 0 else 0,
            relationships_per_second=rel_count / elapsed if elapsed > 0 else 0,
        )

    @staticmethod
    def _to_int_or_none(v: str):
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except ValueError:
            return None

    # ---- indexes ------------------------------------------------------

    def create_indexes(self) -> None:
        users = self.db.collection("users")
        users.add_persistent_index(fields=["gender"])
        users.add_persistent_index(fields=["age"])
        print(f"[{self.name}] indexed: users._key (primary index, = node_id), users.gender, users.age")

    # ---- sampling ----------------------------------------------------

    def sample_node_ids(self, n: int) -> List[int]:
        cursor = self.db.aql.execute(
            "FOR u IN users SORT RAND() LIMIT @n RETURN u.node_id",
            bind_vars={"n": n},
        )
        return list(cursor)

    # ---- workload queries ----------------------------------------------

    def hop_query(self, start_node_id: int, hops: int) -> Any:
        query = """
        FOR v IN @hops..@hops OUTBOUND @start friend
            RETURN DISTINCT v.node_id
        """
        cursor = self.db.aql.execute(
            query,
            bind_vars={"hops": hops, "start": f"users/{start_node_id}"},
        )
        return list(cursor)

    def point_lookup(self, node_id: int) -> Any:
        return self.db.collection("users").get(str(node_id))

    def filtered_lookup(self, gender: int, min_age: int) -> Any:
        query = """
        FOR u IN users
            FILTER u.gender == @gender AND u.age >= @min_age
            LIMIT 50
            RETURN u.node_id
        """
        cursor = self.db.aql.execute(query, bind_vars={"gender": gender, "min_age": min_age})
        return list(cursor)

    def aggregation(self) -> Any:
        query = """
        FOR u IN users
            COLLECT gender = u.gender WITH COUNT INTO cnt
            RETURN {gender: gender, cnt: cnt}
        """
        cursor = self.db.aql.execute(query)
        return list(cursor)

    def mixed_workload_op(self, op_seed: int, write_ratio: float) -> Any:
        is_write = (op_seed % 1000) / 1000.0 < write_ratio
        node_id = op_seed % 11250 
        users = self.db.collection("users")
        if is_write:
            doc = users.get(str(node_id))
            current = (doc.get("completion_percentage") or 0) if doc else 0
            users.update({"_key": str(node_id), "completion_percentage": current + 1})
            return node_id
        else:
            return users.get(str(node_id))

    # ---- footprint ----------------------------------------------------

    def footprint(self) -> Dict[str, Any]:
        try:
            stats = self.db.collection("users").statistics()
            figures = stats.get("figures", {})
            return {
                "documents_size_bytes": figures.get("documentsSize", "not observable"),
                "memory_usage": "not observable",
            }
        except Exception:
            return {"stored_size": "not observable", "memory_usage": "not observable"}
