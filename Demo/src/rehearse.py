"""Drive ordinary upload in Edge and retain reproducible check evidence."""

import argparse
import hashlib
import json
from math import isclose
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

from playwright.sync_api import Error, sync_playwright

from check_browser import EVIDENCE, check_hole, check_reference_points, check_saved_hole, launch
from make_samples import SAMPLES, digest, write_json
from paths import ROOT


def data_for(page, query_id):
    return page.evaluate("""async id => {
      const response = await fetch(`/api/queries/${id}`);
      if (!response.ok) throw Error(`Query result HTTP ${response.status}`);
      return response.json();
    }""", query_id)


def upload(page, path):
    page.evaluate("""() => {
      window.queryObserver?.disconnect();
      window.queryStates = [];
      window.queryObserver = new MutationObserver(events => {
        const state = events[0].target.dataset.state;
        if (window.queryStates.at(-1) !== state) window.queryStates.push(state);
      });
      window.queryObserver.observe(document.querySelector('#query-status'),
        { attributes: true, attributeFilter: ['data-state'] });
    }""")
    started = perf_counter()
    with page.expect_response(lambda response: response.url.endswith("/api/queries")
                              and response.request.method == "POST") as accepted:
        page.locator("#upload").set_input_files(str(path))
    assert accepted.value.status == 202, accepted.value.text()
    query_id = accepted.value.json()["id"]
    page.locator('#query-status:is([data-state="complete"], [data-state="failed"])').wait_for(timeout=150000)
    ui_seconds = perf_counter() - started
    data = data_for(page, query_id)
    phases = page.evaluate("window.queryStates")
    assert phases == (["parsing", "comparing", "complete"] if data["state"] == "complete"
                      else ["parsing", "failed"]), phases
    assert page.locator("#query-source").inner_text() == "演示样例"
    rows = page.locator("#candidates button[data-model]").evaluate_all("nodes => nodes.map(n => n.dataset.model)")
    assert rows == [entry["id"] for entry in data.get("candidates", [])] and len(rows) <= 5
    assert not data.get("candidates") or data["state"] == "complete"
    return data, {
        "file": path.name, "query_id": query_id, "state": data["state"], "phases": phases,
        "ui_seconds": ui_seconds, "timing": data.get("timing"), "top5": rows,
        "query_info": data["query"].get("info"), "coarse_order": data.get("coarse_order"),
        "fine_order": data.get("fine_order"), "review": {
            entry["id"]: entry["alignment"]["state"] for entry in data.get("review", [])
        }, "library_errors": data.get("library_errors"),
    }


def select_candidate(page, model_id, query_id, review=False):
    if review and not page.locator("#review-group").evaluate("node => node.open"):
        page.locator("#review-group summary").click()
    container = "#review" if review else "#candidates"
    started = perf_counter()
    page.locator(f'{container} button[data-model="{model_id}"]').click()
    page.locator(f'#viewer[data-ready="true"][data-candidate="{model_id}"][data-query-id="{query_id}"]').wait_for()
    page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert page.locator("#viewer").get_attribute("data-mode") == "side"
    return perf_counter() - started


def check_similarity(page, data, data_dir):
    saved = json.loads((data_dir / data["id"] / "status.json").read_text(encoding="utf-8"))
    scores = {}
    for group in ("candidates", "review"):
        for entry, archived in zip(data[group], saved[group], strict=True):
            similarity = entry["similarity"]
            assert similarity == archived["similarity"]
            parts = list(similarity["parts"].values())
            assert sum(part["weight"] for part in parts) == 100
            assert all(0 <= part["points"] <= part["weight"] for part in parts)
            total = similarity["score"]
            assert 0 <= total <= 100 and isclose(total, sum(part["points"] for part in parts))
            score = page.locator(f'#{group} li:has(button[data-model="{entry["id"]}"]) .similarity-score')
            assert score.text_content() == f"{total:.1f} / 100"
            scores[entry["id"]] = total
    return scores


def check_score_detail(page, item, screenshot=None):
    model = item["id"]
    button = page.locator(f'li:has(button[data-model="{model}"]) .score-detail-button')
    selected = page.locator(".candidate-select[aria-pressed='true']").get_attribute("data-model")
    button.click()
    details = page.get_by_role("dialog", name="相似度评分详情")
    assert details.is_visible()
    assert details.locator("#score-model").inner_text() == model
    assert details.locator("#score-total").inner_text() == f'{item["similarity"]["score"]:.1f}'
    rendered = details.locator("tbody tr").all()
    for row, (name, part) in zip(rendered, item["similarity"]["parts"].items(), strict=True):
        if name.startswith("surface_"):
            error, limit = f'{part["error"]:.3f} mm', f'{part["zero_at"]:g} mm'
        else:
            error, limit = f'{part["error"] * 100:.2f}%', f'{part["zero_at"] * 100:g}%'
        assert row.locator("td").all_text_contents() == [error, f'{part["points"]:.2f} / {part["weight"]}', limit]
    assert details.locator("#score-review").is_visible() == bool(item["alignment"]["reason"])
    if screenshot:
        page.screenshot(path=str(screenshot))
    page.keyboard.press("Escape")
    assert details.is_hidden()
    assert button.evaluate("node => node === document.activeElement")
    assert page.locator(".candidate-select[aria-pressed='true']").get_attribute("data-model") == selected


def check_linked_view(page):
    canvas = page.locator("#new-pane canvas")
    box = canvas.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    offsets = """() => window.stepViewer.panes.map(view =>
      view.camera.position.clone().sub(view.control.target).toArray())"""
    before = page.evaluate(offsets)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 38, y + 20, steps=5)
    page.mouse.up()
    page.mouse.wheel(0, -100)
    page.wait_for_function("""before => {
      const v = window.stepViewer.left;
      return Math.abs(v.camera.position.distanceTo(v.control.target) - Math.hypot(...before[0])) > 1e-4;
    }""", arg=before)
    left, right = page.evaluate(offsets)
    assert max(abs(a - b) for a, b in zip(left, right)) < 1e-9
    assert max(abs(a - b) for a, b in zip(left, before[0])) > 1e-4


def miss_cause(observation, target):
    if target in observation["top5"]:
        return None
    if observation["state"] == "failed":
        return "query_parse_or_processing_failure"
    if target in observation["library_errors"]:
        return "library_import_failure"
    if target in observation["review"]:
        return f"alignment_{observation['review'][target]}"
    if target in observation["fine_order"]:
        return "below_top5"
    return "coarse_exclusion"


def rehearsal(url, data_dir, report, visible=False):
    EVIDENCE.mkdir(exist_ok=True)
    folder = SAMPLES / "rehearsal"
    cases = json.loads((folder / "cases.json").read_text(encoding="utf-8"))
    requests, errors, observations = [], [], []
    successes = 0
    with sync_playwright() as playwright, launch(playwright, visible=visible, offline=True) as browser:
        context = browser.new_context(viewport={"width": 1460, "height": 940}, service_workers="block")
        probe = context.new_page()
        try:
            probe.goto("http://example.com/", timeout=5000)
        except Error as error:
            canary = str(error).splitlines()[0]
            assert "ERR_PROXY_CONNECTION_FAILED" in canary or "ERR_NAME_NOT_RESOLVED" in canary, canary
        else:
            raise AssertionError("browser outbound canary unexpectedly succeeded")
        probe.close()
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(url)
        page.locator("#open-library").click()
        page.locator("#library-body tr").first.wait_for()
        library_size = page.locator("#library-body tr").count()
        assert 30 <= library_size <= 50
        assert page.locator("#library-body .source-cell").all_text_contents() == ["演示样例"] * library_size
        assert "未知" in page.locator("#library-body").inner_text()
        page.get_by_role("button", name="关闭").click()
        for case in cases:
            path = folder / "queries" / case["file"]
            assert digest(path) == case["sha256"]
            data, observation = upload(page, path)
            observations.append(observation)
            observation["case"] = case["id"]
            observation["passed"] = False
            if data["state"] == "complete":
                observation["similarity_scores"] = check_similarity(page, data, data_dir)
            write_json(report, {"cases": observations})
            if target := case.get("target"):
                observation["target"] = target
                observation["miss"] = miss_cause(observation, target)
                successes += observation["miss"] is None
                write_json(report, {"cases": observations})
                assert observation["miss"] is None, observation
                observation["coarse_rank"] = observation["coarse_order"].index(target) + 1
                observation["rank"] = observation["top5"].index(target) + 1
                if len(observation["top5"]) > 1:
                    alternative = next(id for id in observation["top5"] if id != target)
                    observation["alternative_switch_seconds"] = select_candidate(page, alternative, data["id"])
                    assert alternative in page.locator("#old-pane .model-name").inner_text()
                    assert page.locator("#hole-value").is_hidden()
                observation["switch_seconds"] = select_candidate(page, target, data["id"])
                candidate = next(entry for entry in data["candidates"] if entry["id"] == target)
                observation["overall"] = candidate["alignment"]["overall"]
                if case["id"] == "pose":
                    assert candidate["similarity"]["score"] >= 99.9
                    check_linked_view(page)
                    page.locator("#view-mode").select_option("overlay")
                    observation["pose"] = check_reference_points(page, case["references"], case["max_error_mm"])
                    assert candidate["alignment"]["rank_mm"] <= case["max_rms_mm"]
                    page.locator("#opacity").press("Home")
                    for _ in range(4):
                        page.locator("#opacity").press("ArrowRight")
                    assert page.evaluate("""() => {
                      let count = 0;
                      window.stepViewer.oldModel.traverse(n => {
                        if (n.isMesh && n.material.opacity === 0.35) count++;
                      });
                      return count > 0;
                    }""")
                    page.screenshot(path=str(EVIDENCE / "rehearsal-pose.png"))
                else:
                    assert 65 <= candidate["similarity"]["score"] < 95
                    check_score_detail(page, data["candidates"][1])
                    hole = candidate["alignment"]["hole"]
                    assert hole["status"] == "ok"
                    observation["hole"] = check_hole(page, hole, "rehearsal")
                    observation["hole"]["directional_max_mm"] = check_saved_hole(
                        data_dir / data["id"] / "hole-samples.npz", hole)
                    low, high = case["hole_range_mm"]
                    assert low <= observation["hole"]["page_mm"] <= high
                    page.locator("#visibility").select_option("both")
                    page.screenshot(path=str(EVIDENCE / "rehearsal-hole.png"))
                    check_score_detail(page, candidate, EVIDENCE / "score-details.png")
            elif target := case.get("review_target"):
                assert target not in observation["top5"] and observation["review"][target] == case["state"]
                observation["switch_seconds"] = select_candidate(page, target, data["id"], review=True)
                result = next(entry["alignment"] for entry in data["review"] if entry["id"] == target)
                observation["accepted"] = result["accepted"]
                observation["disagreements"] = result["disagreements"]
                observation["starts"] = result["starts"]
                observation["unique"] = result["unique"]
                assert len(result["accepted"]) >= 2 and result["disagreements"]
                assert result["scene_matrix"] is None and page.locator("#view-mode").is_disabled()
                assert observation["similarity_scores"][target] >= 99.9
                assert page.locator("#new-pane").is_visible() and page.locator("#old-pane").is_visible()
                check_score_detail(page, next(entry for entry in data["review"] if entry["id"] == target))
                page.screenshot(path=str(EVIDENCE / "rehearsal-ambiguity.png"))
            else:
                assert data["state"] == "failed" and "无法读取 STEP" in data["reason"]
                assert page.locator("#viewer").get_attribute("data-ready") == "false"
                assert page.locator("#review-group").is_hidden() and not observation["top5"]
                observation["error"] = data["reason"]
                page.screenshot(path=str(EVIDENCE / "rehearsal-damaged.png"))
            observation["passed"] = True
            write_json(report, {"cases": observations})
            print(case["id"], observation["top5"], observation["ui_seconds"], flush=True)

        data, retry = upload(page, folder / "queries/SHOW-POSE-RETRY.stp")
        assert data["state"] == "complete" and "SHOW-P01" in retry["top5"]
        retry["switch_seconds"] = select_candidate(page, "SHOW-P01", data["id"])
        assert not errors, errors
        remote = [request for request in requests if urlparse(request).hostname != "127.0.0.1"]
        assert not remote, remote
        summary = {
            "browser": browser.version, "external_canary": canary,
            "local_requests": len(requests), "off_host_requests": remote, "library_size": library_size,
            "registered_found": successes, "registered_total": sum("target" in item for item in cases),
            "cases": observations, "retry": retry,
        }
        assert hashlib.sha256((data_dir / retry["query_id"] / "source.step").read_bytes()).hexdigest() == cases[0]["sha256"]
        write_json(report, summary)
        print("Rehearsal passed.", report, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/")
    parser.add_argument("--queries", type=Path, default=ROOT / "data/queries")
    parser.add_argument("--report", type=Path, default=EVIDENCE / "rehearsal.json")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    rehearsal(args.url, args.queries, args.report, args.visible)
