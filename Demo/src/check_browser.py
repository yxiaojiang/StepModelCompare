"""Browser verification against actual GLTFLoader meshes and persisted evidence."""

import argparse
import json
import os
from pathlib import Path

import numpy as np
from playwright.sync_api import sync_playwright

from make_samples import write_json
from paths import ROOT, WEB

EVIDENCE = ROOT / "evidence"
EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / (
    "Microsoft/Edge/Application/msedge.exe"
)


def launch(playwright, visible=False, offline=False):
    proxy = (["--proxy-server=http://127.0.0.1:1", "--proxy-bypass-list=127.0.0.1;localhost",
              "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1", "--disable-quic"]
             if offline else ["--no-proxy-server"])
    return playwright.chromium.launch(
        executable_path=str(EDGE), headless=not visible,
        args=["--enable-unsafe-swiftshader", "--disable-background-networking", *proxy],
    )


def check_reference_points(page, references, tolerance_mm):
    measured = page.evaluate("""async ref => {
      const { Vector3 } = await import('three');
      const viewer = window.stepViewer;
      const mmToWorld = ([x,y,z]) => new Vector3(x/1000, z/1000, -y/1000);
      function find(group, target) {
        group.updateMatrixWorld(true);
        let nearest, distance = Infinity;
        group.traverse(node => {
          if (!node.isMesh) return;
          const positions = node.geometry.attributes.position;
          for (let i = 0; i < positions.count; i++) {
            const p = new Vector3().fromBufferAttribute(positions, i).applyMatrix4(node.matrixWorld);
            if (p.distanceToSquared(target) < distance) {
              distance = p.distanceToSquared(target);
              nearest = p;
            }
          }
        });
        return nearest;
      }
      const target = ref.new_mm.map(mmToWorld);
      const points = [viewer.newModel, viewer.oldModel].map(group => target.map(p => find(group, p)));
      return {
        query_error_mm: points[0].map((p, i) => 1000 * p.distanceTo(target[i])),
        old_error_mm: points[1].map((p, i) => 1000 * p.distanceTo(target[i])),
        pair_error_mm: points[0].map((p, i) => 1000 * p.distanceTo(points[1][i])),
        length_mm: points.map(([a,b]) => 1000 * a.distanceTo(b)),
        cross_product_mm2: points.map(([a,b,c]) => 1e6 * b.clone().sub(a).cross(c.clone().sub(a)).length()),
      };
    }""", references)
    for key in ("query_error_mm", "old_error_mm", "pair_error_mm"):
        assert max(measured[key]) <= tolerance_mm, measured
    assert min(measured["cross_product_mm2"]) > 1
    assert max(abs(length - references["length_mm"]) for length in measured["length_mm"]) <= tolerance_mm
    return measured


def check_hole(page, hole, screen_prefix):
    page.locator("#hole-view").click()
    saved = float(page.locator("#hole-value").get_attribute("data-mm"))
    assert saved == hole["max_mm"]
    assert f"{saved:.3f} mm" in page.locator("#hole-value").inner_text()
    assert page.locator("#viewer").get_attribute("data-mode") == "overlay"
    geometry = page.evaluate("""async region => {
      const { Vector3, Raycaster } = await import('three');
      const viewer = window.stepViewer;
      const [x, y, z] = region.center_mm;
      const delta = region.size_mm[0] - region.size_mm[1];
      const world = (x, y, z) => new Vector3(x/1000,z/1000,-y/1000);
      const cameraDirection = viewer.left.camera.getWorldDirection(new Vector3());
      const target = viewer.left.control.target;
      const hits = [viewer.newModel, viewer.oldModel].map(model =>
        [x - delta, x + delta].map(at => new Raycaster(world(at,y,z+10),
          new Vector3(0,-1,0)).intersectObject(model, true).length));
      return {angle_to_top: cameraDirection.angleTo(new Vector3(0,-1,0)),
        target_error_mm: target.distanceTo(world(x,y,z))*1000, hits, marker: viewer.marker.visible};
    }""", hole["region"])
    assert geometry["angle_to_top"] < 1e-5 and geometry["target_error_mm"] < 1e-6
    assert geometry["marker"] and geometry["hits"][0][0] > 0 and geometry["hits"][0][1] == 0, geometry
    assert geometry["hits"][1][0] == 0 and geometry["hits"][1][1] > 0, geometry
    images = []
    for mode in ("new", "old"):
        page.locator("#visibility").select_option(mode)
        page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        path = EVIDENCE / f"{screen_prefix}-hole-{mode}.png"
        images.append(page.locator("#viewer").screenshot(path=str(path)))
    assert images[0] != images[1]
    return {"page_mm": saved, "counts": hole["counts"], "preset": geometry}


def check_saved_hole(path, hole):
    bounds = np.asarray(hole["region"]["bounds_mm"])
    with np.load(path) as samples:
        maxima, counts = [], []
        for points_key, distance_key in [("old_points_in_new_mm", "old_to_new_mm"),
                                          ("new_points_mm", "new_to_old_mm")]:
            points = samples[points_key]
            mask = ((points >= bounds[0]) & (points <= bounds[1])).all(axis=1)
            maxima.append(float(samples[distance_key][mask].max()))
            counts.append(int(mask.sum()))
    assert max(maxima) == hole["max_mm"] and counts == hole["counts"]
    return maxima


def check_development(url):
    EVIDENCE.mkdir(exist_ok=True)
    data_dir = WEB / "development"
    cases = json.loads((data_dir / "results.json").read_text(encoding="utf-8"))["cases"]
    by_id = {item["id"]: item for item in cases}
    errors = []
    result = {}
    with sync_playwright() as playwright, launch(playwright) as browser:
        page = browser.new_page(viewport={"width": 1370, "height": 820})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.locator('#viewer[data-ready="true"]').wait_for()
        assert page.locator("#viewer").get_attribute("data-mode") == "side"
        page.locator("#view-mode").select_option("overlay")
        pose = by_id["pose"]
        result["pose"] = check_reference_points(page, pose["references"], pose["max_error_mm"])
        page.locator("#viewer").screenshot(path=str(EVIDENCE / "development-pose.png"))

        page.select_option("#case", "hole")
        page.locator('#viewer[data-ready="true"]').wait_for()
        hole = by_id["hole"]["result"]["hole"]
        result["hole"] = check_hole(page, hole, "development")
        result["hole"]["directional_max_mm"] = check_saved_hole(data_dir / "hole-samples.npz", hole)

        page.select_option("#case", "symmetric")
        page.locator('#viewer[data-ready="true"]').wait_for()
        assert page.locator("#view-mode").is_disabled()
        assert page.locator("#viewer").get_attribute("data-mode") == "side"
        assert page.locator("#new-pane").is_visible() and page.locator("#old-pane").is_visible()
        assert "待复核" in page.locator("#state").inner_text()
        result["ambiguity"] = {"accepted": len(by_id["symmetric"]["result"]["accepted"]), "side_only": True}
        page.locator("#viewer").screenshot(path=str(EVIDENCE / "development-ambiguity.png"))
        result["browser"] = browser.version
    assert not errors, errors
    write_json(EVIDENCE / "development-browser.json", result)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8766/development.html")
    check_development(parser.parse_args().url)
