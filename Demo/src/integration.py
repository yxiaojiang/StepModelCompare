"""Validate the ordinary upload workflow on the development-only catalog."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import sync_playwright

from check_browser import EVIDENCE, launch
from library import catalog, import_catalog
from make_samples import DEMO_SOURCE, SAMPLES, write_json
from rehearse import check_linked_view, select_candidate, upload


def check_library_failure():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "sample.step").write_bytes((SAMPLES / "development/DEV-BASE.step").read_bytes())
        (root / "bad.stp").write_bytes(b"Demonstration sample: invalid data\n")
        manifest, database = root / "catalog.json", root / "catalog.sqlite3"
        write_json(manifest, [
            {"id": "GOOD", "file": "sample.step", "source": DEMO_SOURCE},
            {"id": "BAD", "file": "bad.stp", "source": DEMO_SOURCE},
        ])
        import_catalog(manifest, database)
        first, second = catalog(database)
        assert first["id"] == "BAD" and first["error"]
        assert second["id"] == "GOOD" and not second["error"]
        return first["error"]


def integration(url):
    errors = []
    with sync_playwright() as playwright, launch(playwright) as browser:
        page = browser.new_page(viewport={"width": 1460, "height": 940})
        page.on("pageerror", lambda error: errors.append(str(error)))
        assert page.goto(url).ok
        data, report = upload(page, SAMPLES / "development/DEV-POSE-INCH.step")
        assert report["top5"][0] == "DEV-BASE", report
        report["first_switch"] = select_candidate(page, "DEV-BASE", data["id"])
        check_linked_view(page)
        alternate = report["top5"][1]
        report["second_switch"] = select_candidate(page, alternate, data["id"])
        assert alternate in page.locator("#old-pane .model-name").inner_text()
        assert not errors, errors
        page.screenshot(path=str(EVIDENCE / "integration-dev.png"))
    report["retained_import_error"] = check_library_failure()
    write_json(EVIDENCE / "integration-dev.json", report)
    print(report["top5"], report["ui_seconds"], report["second_switch"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/")
    integration(parser.parse_args().url)
