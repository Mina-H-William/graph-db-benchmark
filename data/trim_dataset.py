import argparse
import json
import os

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "raw")
TRIMMED_DIR = os.path.join(SCRIPT_DIR, "trimmed")

EDGES_FILE = os.path.join(RAW_DIR, "soc-pokec-relationships.txt.gz")
PROFILES_FILE = os.path.join(RAW_DIR, "soc-pokec-profiles.txt.gz")

# Only the first 8 columns are named here; the rest are free-text fields based on website
PROFILE_COLS = {
    0: "original_id",
    1: "public",
    2: "completion_percentage",
    3: "gender",
    4: "region",
    5: "last_login",
    6: "registration",
    7: "age",
}


def load_edges() -> pd.DataFrame:
    if not os.path.exists(EDGES_FILE):
        raise FileNotFoundError(
            f"{EDGES_FILE} not found. Run download_dataset.py first."
        )
    print(f"[load] {EDGES_FILE}")
    edges = pd.read_csv(
        EDGES_FILE,
        sep="\t",
        header=None,
        names=["src", "dst"],
        dtype={"src": "int32", "dst": "int32"},
        compression="gzip",
    )
    print(f"[load] {len(edges):,} raw edges")
    return edges


def top_degree_nodes(edges: pd.DataFrame, n: int) -> pd.Index:
    deg = pd.concat([edges["src"], edges["dst"]]).value_counts()
    return deg.index[:n]


def induced_edge_count(edges: pd.DataFrame, node_set: set) -> int:
    mask = edges["src"].isin(node_set) & edges["dst"].isin(node_set)
    return int(mask.sum())


def find_node_count(
    edges: pd.DataFrame, initial_n: int, target_min: int, target_max: int, max_iters: int = 8
):
    n = initial_n
    low, high = None, None  # bounds on N we've tried

    for i in range(max_iters):
        node_set = set(top_degree_nodes(edges, n).tolist())
        e_count = induced_edge_count(edges, node_set)
        print(f"[search] N={n:,} nodes -> {e_count:,} induced edges")

        if target_min <= e_count <= target_max:
            return n, node_set, e_count

        if e_count < target_min:
            low = n
            n = n * 2 if high is None else (n + high) // 2
        else:
            high = n
            n = n // 2 if low is None else (n + low) // 2

        if low is not None and high is not None and high - low <= 1:
            break

    # Fall back to whatever the last attempt was
    print("[search] could not hit target range exactly, using closest result")
    node_set = set(top_degree_nodes(edges, n).tolist())
    e_count = induced_edge_count(edges, node_set)
    return n, node_set, e_count


def trim_edges(edges: pd.DataFrame, node_set: set):
    mask = edges["src"].isin(node_set) & edges["dst"].isin(node_set)
    trimmed = edges[mask].copy()

    # Remap original ids -> contiguous 0..k-1 ids
    unique_ids = sorted(node_set)
    id_map = {orig: i for i, orig in enumerate(unique_ids)}

    trimmed["src"] = trimmed["src"].map(id_map)
    trimmed["dst"] = trimmed["dst"].map(id_map)
    trimmed = trimmed.rename(columns={"src": "src_id", "dst": "dst_id"})

    mapping_df = pd.DataFrame(
        {"original_id": list(id_map.keys()), "node_id": list(id_map.values())}
    )
    return trimmed, mapping_df, id_map


def trim_profiles(node_set: set, id_map: dict) -> pd.DataFrame:
    if not os.path.exists(PROFILES_FILE):
        raise FileNotFoundError(
            f"{PROFILES_FILE} not found. Run download_dataset.py first."
        )
    print(f"[load] {PROFILES_FILE} (this can take a minute)")

    usecols = list(PROFILE_COLS.keys())
    names = [PROFILE_COLS[i] for i in usecols]

    chunks = []
    reader = pd.read_csv(
        PROFILES_FILE,
        sep="\t",
        header=None,
        usecols=usecols,
        names=names,
        dtype=str,
        na_values=["null", ""],
        compression="gzip",
        on_bad_lines="skip",
        chunksize=200_000,
    )
    for chunk in reader:
        chunk["original_id"] = chunk["original_id"].astype("int32")
        chunk = chunk[chunk["original_id"].isin(node_set)]
        if not chunk.empty:
            chunks.append(chunk)

    profiles = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=names)
    profiles["node_id"] = profiles["original_id"].map(id_map)
    profiles["age"] = pd.to_numeric(profiles["age"], errors="coerce")
    profiles["gender"] = pd.to_numeric(profiles["gender"], errors="coerce")
    profiles["public"] = pd.to_numeric(profiles["public"], errors="coerce")
    profiles["completion_percentage"] = pd.to_numeric(
        profiles["completion_percentage"], errors="coerce"
    )

    cols = [
        "node_id",
        "original_id",
        "public",
        "gender",
        "age",
        "region",
        "completion_percentage",
    ]
    return profiles[cols]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initial-n", type=int, default=60000,
                         help="Starting guess for number of top-degree nodes to keep")
    parser.add_argument("--target-min", type=int, default=150000,
                         help="Minimum acceptable relationship count")
    parser.add_argument("--target-max", type=int, default=300000,
                         help="Maximum acceptable relationship count")
    args = parser.parse_args()

    os.makedirs(TRIMMED_DIR, exist_ok=True)

    edges = load_edges()

    n, node_set, e_count = find_node_count(
        edges, args.initial_n, args.target_min, args.target_max
    )

    print(f"\n[select] keeping {len(node_set):,} nodes, inducing {e_count:,} edges")

    trimmed_edges, mapping_df, id_map = trim_edges(edges, node_set)
    profiles = trim_profiles(node_set, id_map)

    edges_path = os.path.join(TRIMMED_DIR, "edges.csv")
    mapping_path = os.path.join(TRIMMED_DIR, "node_id_mapping.csv")
    profiles_path = os.path.join(TRIMMED_DIR, "profiles.csv")
    summary_path = os.path.join(TRIMMED_DIR, "summary.json")

    trimmed_edges.to_csv(edges_path, index=False)
    mapping_df.to_csv(mapping_path, index=False)
    profiles.to_csv(profiles_path, index=False)

    summary = {
        "source": "https://snap.stanford.edu/data/soc-Pokec.html",
        "node_count": len(node_set),
        "relationship_count": int(len(trimmed_edges)),
        "profiles_matched": int(len(profiles)),
        "selection_method": "induced subgraph on top-degree nodes (in+out degree)",
        "target_range": [args.target_min, args.target_max],
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    print("\n[done] wrote:")
    print(f"  {edges_path}      ({len(trimmed_edges):,} rows)")
    print(f"  {profiles_path}   ({len(profiles):,} rows)")
    print(f"  {mapping_path}")
    print(f"  {summary_path}")
    print(f"\nSummary: {json.dumps(summary, indent=2)}")


if __name__ == "__main__":
    main()
