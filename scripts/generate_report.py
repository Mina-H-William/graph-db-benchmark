import json
import os

RESULTS_DIR = "results"
LOAD_RESULTS = os.path.join(RESULTS_DIR, "load_results.json")
WORKLOAD_RESULTS = os.path.join(RESULTS_DIR, "workload_results.json")


PLATFORM_ORDER = ["cognodb", "neo4j", "memgraph", "arangodb"]


def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def ordered(results):
    by_name = {r["platform"]: r for r in results}
    return [by_name[p] for p in PLATFORM_ORDER if p in by_name]


def fmt(v, decimals=2):
    if isinstance(v, float):
        return f"{v:.{decimals}f}"
    return str(v)


def print_load_table(load_results):
    print("### Data Loading (Ingest Throughput)\n")
    print("| Platform | Nodes | Relationships | Load Time (s) | Nodes/sec | Rels/sec |")
    print("|---|---|---|---|---|---|")
    for r in ordered(load_results):
        if r.get("status") != "ok":
            print(f"| {r['platform']} | — | — | — | — | FAILED: {r.get('error', 'unknown')} |")
            continue
        print(f"| {r['platform']} | {r['node_count']:,} | {r['relationship_count']:,} | "
              f"{fmt(r['load_time_seconds'])} | {fmt(r['nodes_per_second'])} | "
              f"{fmt(r['relationships_per_second'])} |")
    print()


def print_latency_table(workload_results, key, title):
    print(f"### {title} (p50 / p95, ms)\n")
    print("| Platform | p50 (ms) | p95 (ms) | mean (ms) | samples | failed |")
    print("|---|---|---|---|---|---|")
    for r in ordered(workload_results):
        if r.get("status") != "ok":
            print(f"| {r['platform']} | — | — | — | — | FAILED |")
            continue
        lat = r["latencies"].get(key)
        if not lat:
            print(f"| {r['platform']} | not run | | | | |")
            continue
        print(f"| {r['platform']} | {fmt(lat['p50_ms'])} | {fmt(lat['p95_ms'])} | "
              f"{fmt(lat['mean_ms'])} | {lat['samples']} | {lat.get('failed_iterations', 0)} |")
    print()


def print_mixed_workload_table(workload_results):
    print("### Concurrent Mixed Read/Write Throughput (80% read / 20% write)\n")
    print("| Platform | Concurrency | Throughput (ops/sec) | Total Ops | Errors |")
    print("|---|---|---|---|---|")
    for r in ordered(workload_results):
        if r.get("status") != "ok":
            continue
        for mw in r.get("mixed_workload", []):
            print(f"| {r['platform']} | {mw['concurrency']} | "
                  f"{fmt(mw['throughput_ops_per_sec'])} | {mw['total_ops']:,} | "
                  f"{mw.get('total_errors', 0)} |")
    print()


def print_footprint_table(workload_results):
    print("### Footprint\n")
    print("| Platform | Details |")
    print("|---|---|")
    for r in ordered(workload_results):
        if r.get("status") != "ok":
            continue
        fp = r.get("footprint", {})
        details = ", ".join(f"{k}: {v}" for k, v in fp.items())
        print(f"| {r['platform']} | {details} |")
    print()


def main():
    load_results = load_json(LOAD_RESULTS)
    workload_results = load_json(WORKLOAD_RESULTS)

    if not load_results and not workload_results:
        print("No results found in results/. Run load_all.py and runner.py first.")
        return

    print("## Results\n")

    if load_results:
        print_load_table(load_results)

    if workload_results:
        print_latency_table(workload_results, "hop_1", "1-Hop Traversal Latency")
        print_latency_table(workload_results, "hop_2", "2-Hop Traversal Latency")
        print_latency_table(workload_results, "hop_3", "3-Hop Traversal Latency")
        print_latency_table(workload_results, "point_lookup", "Point Lookup Latency")
        print_latency_table(workload_results, "filtered_lookup", "Filtered/Indexed Lookup Latency (gender=1, age>=25)")
        print_latency_table(workload_results, "aggregation", "Aggregation Latency (count by gender)")
        print_mixed_workload_table(workload_results)
        print_footprint_table(workload_results)


if __name__ == "__main__":
    main()
