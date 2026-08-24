from neo4j import GraphDatabase
import os
from dotenv import load_dotenv

load_dotenv()

driver = GraphDatabase.driver(
    os.environ["COGNODB_URI"],
    auth=(os.environ["COGNODB_USER"], os.environ["COGNODB_PASSWORD"])
)
with driver.session() as session:
    result = session.run("RETURN 1 AS test")
    print(result.single()["test"])  # should print 1

driver.close()