"""Shared retrieval pipeline for the web query and development calibration."""

from time import perf_counter
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

from alignment import compare
from geometry import SETTINGS, Mesh, Surface
from library import catalog, coarse_order, mesh_blob


def worker_pool():
    return ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn"))


def evaluate(packed_new, info_new, packed_old, info_old, region, evidence_path):
    started = perf_counter()
    new, old = (Surface(Mesh.unpack(blob, info)) for blob, info in (
        (packed_new, info_new), (packed_old, info_old)
    ))
    result = compare(new, old, region, evidence_path)
    result["seconds"] = perf_counter() - started
    return result


def lists(results):
    reliable = sorted((entry for entry in results if entry["alignment"]["state"] == "aligned"),
                      key=lambda entry: (entry["alignment"]["rank_mm"], entry["coarse_score"], entry["id"]))
    review = [entry for entry in results if entry["alignment"]["state"] != "aligned"]
    return reliable, review


def retrieve(database, new, pool, k, annotation=None, evidence_path=None):
    entries = catalog(database)
    ordered = coarse_order(new, entries)
    packed = new.packed()
    futures = []
    for coarse_rank, entry in enumerate(ordered[:k], 1):
        region = annotation["hole"] if annotation and annotation["target"] == entry["id"] else None
        future = pool.submit(evaluate, packed, new.info, mesh_blob(database, entry["id"]), entry["info"],
                             region, evidence_path if region is not None else None)
        futures.append((coarse_rank, entry, future))
    comparisons = [{**entry, "coarse_rank": rank, "alignment": future.result()}
                   for rank, entry, future in futures]
    reliable, review = lists(comparisons)
    return {"candidates": reliable[:SETTINGS["top_results"]], "review": review,
            "coarse_order": [entry["id"] for entry in ordered],
            "fine_order": [entry["id"] for entry in reliable], "compared": len(comparisons),
            "library_errors": [entry["id"] for entry in entries if entry["error"]]}
