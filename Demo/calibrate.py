"""Compare prefilter K values exclusively against the development catalog."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from geometry import read_step
from make_samples import SAMPLES, write_json
from search import retrieve, worker_pool


def run(database, report, values):
    development = SAMPLES / "development"
    queries = json.loads((development / "cases.json").read_text(encoding="utf-8"))
    results = []
    with worker_pool() as pool:
        for item in queries:
            path = development / f"{item['new']}.step"
            query = read_step(path.read_bytes(), path.name)
            for k in values:
                started = perf_counter()
                outcome = retrieve(database, query, pool, k)
                found = [entry["id"] for entry in outcome["candidates"]]
                review = {entry["id"]: entry["alignment"]["state"] for entry in outcome["review"]}
                target = item["old"]
                result = {"case": item["id"], "k": k, "target": target,
                          "coarse_rank": outcome["coarse_order"].index(target) + 1,
                          "target_rank": found.index(target) + 1 if target in found else None,
                          "target_review": review.get(target), "displayed": found, "review": review,
                          "seconds": perf_counter() - started}
                print(result, flush=True)
                results.append(result)
    write_json(report, results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("db", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--k", type=int, nargs="+", default=[4, 8])
    args = parser.parse_args()
    run(args.db, args.report, args.k)
