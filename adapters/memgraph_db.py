import os
from adapters.cypher_base import CypherAdapter


class MemgraphAdapter(CypherAdapter):
    name = "memgraph"

    def __init__(self):
        super().__init__(
            uri=os.environ.get("MEMGRAPH_URI", "bolt://localhost:7688"),
            user="memgraph",
            password=os.environ["MEMGRAPH_PASSWORD"],
        )

    def _ensure_node_id_index(self) -> None:
        with self.driver.session() as session:
            try:
                session.run("CREATE INDEX ON :User(node_id);")
            except Exception as e:
                print(f"[{self.name}] node_id index warning: {e}")
        print(f"[{self.name}] node_id index created (pre-edge-load)")

    def create_indexes(self) -> None:
        stmts = [
            "CREATE INDEX ON :User(node_id);",
            "CREATE INDEX ON :User(gender);",
            "CREATE INDEX ON :User(age);",
        ]
        with self.driver.session() as session:
            for stmt in stmts:
                try:
                    session.run(stmt)
                except Exception as e:
                    print(f"[{self.name}] index creation warning: {e}")
        print(f"[{self.name}] indexed: User.node_id, User.gender, User.age")

    def footprint(self) -> dict:
        try:
            with self.driver.session() as session:
                result = session.run("SHOW STORAGE INFO").data()
            info = {row["storage info"]: row["value"] for row in result} if result else {}
            return {"storage_info": info or "not observable"}
        except Exception:
            return {"stored_size": "not observable", "memory_usage": "not observable"}
