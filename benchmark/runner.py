import argparse
import concurrent.futures
import itertools
import json
import os
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from adapters.base import compute_latency_stats

WARMUP_ITERATIONS = 20
MEASURED_ITERATIONS = 150          
SAMPLE_POOL_SIZE = 200            
FILTER_GENDER = 1
FILTER_MIN_AGE = 25
MIXED_WORKLOAD_CONCURRENCY_LEVELS = [1, 10, 40]
MIXED_WORKLOAD_DURATION_SECONDS = 15
MIXED_WORKLOAD_WRITE_RATIO = 0.2   # 80% read / 20% write

RESULTS_DIR = "results"


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



def time_calls(fn, iterations: int, warmup: int, label: str = ""):
    for _ in range(warmup):
        try:
            fn()
        except Exception:
            pass  

    times_ms = []
    failures = 0
    for i in range(iterations):
        start = time.perf_counter()
        try:
            fn()
        except Exception as e:
            failures += 1
            if failures <= 3:  
                print(f"    [warn] {label} iteration {i} failed: {e!r}")
            continue
        times_ms.append((time.perf_counter() - start) * 1000)

    failure_rate = failures / iterations if iterations else 0
    if failure_rate > 0.2:
        raise RuntimeError(
            f"{label}: {failures}/{iterations} iterations failed ({failure_rate:.0%}) -- "
            f"treating as a broken workload rather than sampling noise"
        )
    if failures:
        print(f"    [note] {label}: {failures}/{iterations} iterations failed and were skipped "
              f"({failure_rate:.1%})")

    return times_ms, failures


def run_mixed_workload(db, concurrency: int, duration_s: float, write_ratio: float) -> dict:
    stop_at = time.perf_counter() + duration_s
    errors = []

    def worker(thread_id):
        local_ops = 0
        local_errors = 0
        seed = thread_id * 1_000_000
        while time.perf_counter() < stop_at:
            try:
                db.mixed_workload_op(seed + local_ops, write_ratio)
                local_ops += 1
            except Exception as e:
                local_errors += 1
                errors.append(str(e))
                continue
        return local_ops, local_errors

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker, i) for i in range(concurrency)]
        outcomes = [f.result() for f in futures]
    total_ops = sum(o[0] for o in outcomes)
    total_errors = sum(o[1] for o in outcomes)

    throughput = total_ops / duration_s
    return {
        "concurrency": concurrency,
        "duration_seconds": duration_s,
        "write_ratio": write_ratio,
        "total_ops": total_ops,
        "total_errors": total_errors,
        "throughput_ops_per_sec": round(throughput, 2),
        "sample_errors": errors[:5],
    }


def benchmark_platform(name: str, adapter_cls) -> dict:
    print(f"\n{'='*60}\nBenchmarking {name}\n{'='*60}")
    db = adapter_cls()
    result = {"platform": name}
    try:
        db.connect()

        start_nodes = db.sample_node_ids(SAMPLE_POOL_SIZE)
        if not start_nodes:
            raise RuntimeError("sample_node_ids returned no nodes -- is data loaded?")

        node_cycle = itertools.cycle(start_nodes)

        def next_node():
            return next(node_cycle)

        latencies = {}

        for hops in (1, 2, 3):
            label = f"{hops}-hop traversal"
            print(f"  {label}: {MEASURED_ITERATIONS} iterations after {WARMUP_ITERATIONS} warm-up...")
            times_ms, failures = time_calls(
                lambda h=hops: db.hop_query(next_node(), h),
                MEASURED_ITERATIONS, WARMUP_ITERATIONS, label,
            )
            stats = compute_latency_stats(label, times_ms).to_dict()
            stats["failed_iterations"] = failures
            latencies[f"hop_{hops}"] = stats
            print(f"    p50={stats['p50_ms']}ms  p95={stats['p95_ms']}ms")

        print(f"  point lookup: {MEASURED_ITERATIONS} iterations...")
        times_ms, failures = time_calls(lambda: db.point_lookup(next_node()), MEASURED_ITERATIONS, WARMUP_ITERATIONS, "point lookup")
        stats = compute_latency_stats("point lookup", times_ms).to_dict()
        stats["failed_iterations"] = failures
        latencies["point_lookup"] = stats
        print(f"    p50={stats['p50_ms']}ms  p95={stats['p95_ms']}ms")

        print(f"  filtered lookup (gender={FILTER_GENDER}, age>={FILTER_MIN_AGE}): {MEASURED_ITERATIONS} iterations...")
        times_ms, failures = time_calls(
            lambda: db.filtered_lookup(FILTER_GENDER, FILTER_MIN_AGE),
            MEASURED_ITERATIONS, WARMUP_ITERATIONS, "filtered lookup",
        )
        stats = compute_latency_stats("filtered lookup", times_ms).to_dict()
        stats["failed_iterations"] = failures
        latencies["filtered_lookup"] = stats
        print(f"    p50={stats['p50_ms']}ms  p95={stats['p95_ms']}ms")

        print(f"  aggregation: {MEASURED_ITERATIONS} iterations...")
        times_ms, failures = time_calls(lambda: db.aggregation(), MEASURED_ITERATIONS, WARMUP_ITERATIONS, "aggregation")
        stats = compute_latency_stats("aggregation", times_ms).to_dict()
        stats["failed_iterations"] = failures
        latencies["aggregation"] = stats
        print(f"    p50={stats['p50_ms']}ms  p95={stats['p95_ms']}ms")

        result["latencies"] = latencies

        print(f"  mixed workload sweep (concurrency={MIXED_WORKLOAD_CONCURRENCY_LEVELS}, "
              f"{MIXED_WORKLOAD_DURATION_SECONDS}s each, {int(MIXED_WORKLOAD_WRITE_RATIO*100)}% writes)...")
        mixed_results = []
        for c in MIXED_WORKLOAD_CONCURRENCY_LEVELS:
            print(f"    concurrency={c} ...")
            r = run_mixed_workload(db, c, MIXED_WORKLOAD_DURATION_SECONDS, MIXED_WORKLOAD_WRITE_RATIO)
            print(f"      throughput={r['throughput_ops_per_sec']} ops/sec")
            mixed_results.append(r)
        result["mixed_workload"] = mixed_results

        print("  collecting footprint...")
        result["footprint"] = db.footprint()

        result["status"] = "ok"

    except Exception as e:
        print(f"[{name}] FAILED: {e}")
        result["status"] = "failed"
        result["error"] = str(e)
    finally:
        db.close()

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", help="Run a single platform, e.g. neo4j")
    parser.add_argument("--all", action="store_true", help="Run every registered platform")
    args = parser.parse_args()

    if args.platform:
        if args.platform not in ADAPTER_REGISTRY:
            print(f"Unknown or unavailable platform '{args.platform}'. "
                  f"Available: {list(ADAPTER_REGISTRY.keys())}")
            sys.exit(1)
        targets = {args.platform: ADAPTER_REGISTRY[args.platform]}
    elif args.all:
        targets = ADAPTER_REGISTRY
    else:
        print("Specify --platform <name> or --all. "
              f"Available: {list(ADAPTER_REGISTRY.keys())}")
        sys.exit(1)

    os.makedirs(RESULTS_DIR, exist_ok=True)

    all_results = []
    for name, adapter_cls in targets.items():
        result = benchmark_platform(name, adapter_cls)
        all_results.append(result)
        with open(os.path.join(RESULTS_DIR, f"{name}_workload.json"), "w") as f:
            json.dump(result, f, indent=2)

    combined_path = os.path.join(RESULTS_DIR, "workload_results.json")
    existing = []
    if os.path.exists(combined_path):
        with open(combined_path) as f:
            existing = json.load(f)
    existing_by_platform = {r["platform"]: r for r in existing}
    for r in all_results:
        existing_by_platform[r["platform"]] = r
    merged = list(existing_by_platform.values())
    with open(combined_path, "w") as f:
        json.dump(merged, f, indent=2)

    print(f"\n[done] results saved to {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
