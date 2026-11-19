"""Score measured geometric closeness separately from alignment status."""

from geometry import SETTINGS


def relative_error(new, old):
    return abs(new - old) / max(new, old)


def similarity_score(new, old, alignment):
    directions = alignment["overall"].values()
    errors = {
        "surface_rms": max(item["rms_mm"] for item in directions),
        "surface_p95": max(item["p95_mm"] for item in directions),
        "surface_max": max(item["max_mm"] for item in directions),
        "size": max(relative_error(a, b) for a, b in zip(new["size_mm"], old["size_mm"])),
        "area": relative_error(new["area_mm2"], old["area_mm2"]),
        "volume": relative_error(new["volume_mm3"], old["volume_mm3"]),
    }
    parts = {}
    for key, scale in SETTINGS["similarity"].items():
        error = errors[key]
        points = scale["weight"] * max(0.0, 1.0 - error / scale["zero_at"])
        parts[key] = {**scale, "points": points, "error": error}
    return {"version": 1, "score": sum(part["points"] for part in parts.values()), "parts": parts}
