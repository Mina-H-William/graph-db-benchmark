from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List
import statistics


@dataclass
class LoadResult:
    node_count: int
    relationship_count: int
    load_time_seconds: float
    nodes_per_second: float
    relationships_per_second: float


@dataclass
class LatencyResult:
    label: str
    p50_ms: float
    p95_ms: float
    mean_ms: float
    min_ms: float
    max_ms: float
    samples: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "mean_ms": round(self.mean_ms, 3),
            "min_ms": round(self.min_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "samples": self.samples,
        }


def compute_latency_stats(label: str, times_ms: List[float]) -> LatencyResult:
    """Percentiles via nearest-rank on sorted samples -- simple and dependency-free."""
    if not times_ms:
        raise ValueError(f"no samples collected for '{label}'")
    s = sorted(times_ms)
    n = len(s)

    def pct(p: float) -> float:
        idx = min(n - 1, max(0, round(p / 100 * (n - 1))))
        return s[idx]

    return LatencyResult(
        label=label,
        p50_ms=pct(50),
        p95_ms=pct(95),
        mean_ms=statistics.mean(s),
        min_ms=s[0],
        max_ms=s[-1],
        samples=n,
    )


class GraphDBAdapter(ABC):
    """
    Common interface implemented by every platform under test.

    Design notes:
    - Each "single op" method (hop_query, point_lookup, filtered_lookup,
      aggregation, mixed_workload_op) performs exactly ONE query and does
      NOT time itself -- benchmark/runner.py wraps calls with
      time.perf_counter() so timing logic lives in one place and is
      identical across platforms.
    - sample_node_ids() lets the runner pick real, existing node ids to
      query against, so every platform is queried against equivalent
      "random start node" sets per section 5.2's traversal requirement.
    """

    name: str  # e.g. "neo4j", "cognodb", "arangodb"

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def clear(self) -> None:
        """Wipe all data so load() starts from empty -- makes runs repeatable."""
        ...

    @abstractmethod
    def load(self, edges_path: str, profiles_path: str) -> LoadResult:
        """Bulk load nodes + relationships from the trimmed CSVs."""
        ...

    @abstractmethod
    def create_indexes(self) -> None:
        """Create indexes used by point/filtered lookups. Log what was indexed."""
        ...

    @abstractmethod
    def sample_node_ids(self, n: int) -> List[int]:
        """Return n real node_ids present in the loaded graph, for query sampling."""
        ...

    @abstractmethod
    def hop_query(self, start_node_id: int, hops: int) -> Any:
        """Run a single {hops}-hop traversal from start_node_id."""
        ...

    @abstractmethod
    def point_lookup(self, node_id: int) -> Any:
        """Fetch a single node by its indexed node_id."""
        ...

    @abstractmethod
    def filtered_lookup(self, gender: int, min_age: int) -> Any:
        """Fetch nodes matching an indexed/filtered predicate (gender + age)."""
        ...

    @abstractmethod
    def aggregation(self) -> Any:
        """Count/group-by style query, e.g. node count grouped by gender."""
        ...

    @abstractmethod
    def mixed_workload_op(self, op_seed: int, write_ratio: float) -> Any:
        """
        One operation for the concurrent mixed workload. Uses op_seed to
        deterministically pick a read or a write per write_ratio (e.g. 0.2
        = 20% writes), and to pick which node to touch.
        """
        ...

    @abstractmethod
    def footprint(self) -> Dict[str, Any]:
        """Whatever the platform exposes: stored size, memory, etc.
        Use 'not observable' string values where the platform doesn't expose it."""
        ...
