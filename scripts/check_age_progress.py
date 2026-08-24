import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.environ.get("AGE_HOST", "localhost"),
    port=os.environ.get("AGE_PORT", "5433"),
    dbname=os.environ.get("AGE_DBNAME", "graphbench"),
    user=os.environ.get("AGE_USER", "age"),
    password=os.environ["AGE_PASSWORD"],
)
conn.autocommit = True

with conn.cursor() as cur:
    cur.execute("LOAD 'age';")
    cur.execute('SET search_path = ag_catalog, "$user", public;')
    cur.execute("SELECT * FROM cypher('graphbench', $$ MATCH (n:User) RETURN count(n) $$) AS (c agtype);")
    node_count = cur.fetchone()[0]
    cur.execute("SELECT * FROM cypher('graphbench', $$ MATCH ()-[r:FRIEND]->() RETURN count(r) $$) AS (c agtype);")
    edge_count = cur.fetchone()[0]

print(f"Nodes loaded so far:         {node_count}")
print(f"Relationships loaded so far: {edge_count}")
print(f"Target: 11250 nodes, 233031 relationships")

conn.close()
