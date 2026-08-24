import os
from adapters.cypher_base import CypherAdapter


class Neo4jAdapter(CypherAdapter):
    name = "neo4j"

    def __init__(self):
        super().__init__(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            user="neo4j",
            password=os.environ["NEO4J_PASSWORD"],
        )

    def footprint(self) -> dict:
        query = """
        CALL apoc.monitor.store() YIELD logSize, stringStoreSize, arrayStoreSize, relStoreSize, propStoreSize, totalStoreSize
        RETURN totalStoreSize AS total
        """
        try:
            with self.driver.session() as session:
                total = session.run(query).single()["total"]
            return {"stored_size_bytes": total, "memory_usage": "not observable"}
        except Exception:
            # APOC may not be installed on the community image -- fall back
            return {"stored_size": "not observable (APOC not available)", "memory_usage": "not observable"}
