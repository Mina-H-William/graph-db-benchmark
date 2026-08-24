import argparse
import json
import os
import sys
from dataclasses import asdict

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

EDGES_PATH = "data/trimmed/edges.csv"
PROFILES_PATH = "data/trimmed/profiles.csv"
RESULTS_PATH = "results/load_results.json"

ADAPTER_REGISTRY = {}

try:
    from adapters.neo4j_db import Neo4jAdapter
    ADAPTER_REGISTRY["neo4j"] = Neo4jAdapter
except Exception as e:
    print(f"[warn] neo4j adapter unavailable: {e}")

try:
    from adapters.memgraph_db import MemgraphAdapter
    ADAPTER_REGISTRY["memgraph"] = MemgraphAdapter
except Exception as e:
    print(f"[warn] memgraph adapter unavailable: {e}")

try:
    from adapters.cognodb import CognoDBAdapter
    ADAPTER_REGISTRY["cognodb"] = CognoDBAdapter
except Exception as e:
    print(f"[warn] cognodb adapter unavailable: {e}")

try:
    from adapters.arangodb_db import ArangoDBAdapter
    ADAPTER_REGISTRY["arangodb"] = ArangoDBAdapter
except Exception as e:
    print(f"[warn] arangodb adapter unavailable: {e}")

# try:
#     from adapters.age_db import AgeAdapter
#     ADAPTER_REGISTRY["age"] = AgeAdapter
# except Exception as e:
#     print(f"[warn] age adapter unavailable: {e}")


def load_one(name: str, adapter_cls) -> dict:
    print(f"\n=== Loading {name} ===")
    db = adapter_cls()
    try:
        db.connect()
        print(f"[{name}] connected, clearing existing data...")
        db.clear()
        print(f"[{name}] loading {EDGES_PATH} + {PROFILES_PATH} ...")
        result = db.load(EDGES_PATH, PROFILES_PATH)
        print(f"[{name}] loaded: {result}")
        print(f"[{name}] creating indexes...")
        db.create_indexes()
        return {"platform": name, "status": "ok", **asdict(result)}
    except Exception as e:
        print(f"[{name}] FAILED: {e}")
        return {"platform": name, "status": "failed", "error": str(e)}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Load a single platform by name (e.g. neo4j)")
    args = parser.parse_args()

    targets = {args.only: ADAPTER_REGISTRY[args.only]} if args.only else ADAPTER_REGISTRY

    if not targets:
        print("No adapters registered/available. Check imports above.")
        sys.exit(1)

    os.makedirs("results", exist_ok=True)

    all_results = []
    for name, adapter_cls in targets.items():
        all_results.append(load_one(name, adapter_cls))

    existing = []
    if os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH) as f:
            existing = json.load(f)
    existing_by_platform = {r["platform"]: r for r in existing}
    for r in all_results:
        existing_by_platform[r["platform"]] = r

    merged = list(existing_by_platform.values())
    with open(RESULTS_PATH, "w") as f:
        json.dump(merged, f, indent=2)

    print(f"\n[done] results saved to {RESULTS_PATH}")
    print(json.dumps(all_results, indent=2))


if __name__ == "__main__":
    main()
