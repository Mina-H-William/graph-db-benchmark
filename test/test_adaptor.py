import os
from dotenv import load_dotenv

from adapters.neo4j_db import Neo4jAdapter

load_dotenv()

db = Neo4jAdapter()
db.connect()
db.clear()
result = db.load("data/trimmed/edges.csv", "data/trimmed/profiles.csv")
print(result)
db.create_indexes()
print(db.sample_node_ids(3))
db.close()