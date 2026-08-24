import os
from adapters.cypher_base import CypherAdapter


class CognoDBAdapter(CypherAdapter):
    name = "cognodb"

    def __init__(self):
        super().__init__(
            uri=os.environ["COGNODB_URI"],
            user=os.environ.get("COGNODB_USER", "cognodb"),
            password=os.environ["COGNODB_PASSWORD"],
        )