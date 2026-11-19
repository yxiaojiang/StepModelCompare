"""Build Studio.exe and the Inno Setup installer from source-controlled inputs."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import OCP

SOURCE = Path(__file__).resolve().parents[1]
DEMO = SOURCE.parent
RELEASE = DEMO / "release"
WEBVIEW_INSTALLER = "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
BUNDLE = RELEASE / "bundle" / "Studio"
BASE = RELEASE / "demo-base"


def compile_studio():
    work = RELEASE / "build"
    work.mkdir(parents=True, exist_ok=True)
    samples = DEMO / "samples"
    BASE.mkdir(exist_ok=True)
    catalog = json.loads((samples / "catalog.json").read_text(encoding="utf-8"))
    metadata = {}
    for entry in catalog:
        source = samples / entry["file"]
        shutil.copy2(source, BASE / source.name)
        metadata[f"demo/{source.name.casefold()}"] = {
            **entry, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()
        }
    if {path.name.casefold() for path in BASE.iterdir()} != {key.removeprefix("demo/") for key in metadata}:
        raise RuntimeError(f"Packaged sample directory contains stale files: {BASE}")
    metadata_path = work / "demo-models.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    ocp_dlls = Path(OCP.__file__).resolve().parent.parent / "cadquery_ocp_novtk.libs"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed", "--noupx",
        "--name", "Studio", "--paths", str(SOURCE), "--workpath", str(work), "--specpath", str(work),
        "--distpath", str(RELEASE / "bundle"),
        "--add-data", f"{SOURCE / 'web'};web", "--add-data", f"{SOURCE / 'settings.json'};.",
        "--add-data", f"{samples / 'rehearsal'};samples/rehearsal",
        "--add-data", f"{metadata_path};.",
        "--add-binary", f"{ocp_dlls / '*.dll'};{ocp_dlls.name}",
        "--collect-data", "webview", "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "webview.platforms.edgechromium", "--hidden-import", "clr",
        "--hidden-import", "uvicorn.loops.asyncio", "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.lifespan.on", "--copy-metadata", "cadquery-ocp-novtk",
        str(SOURCE / "studio.py")
    ], check=True)
    print(f"Compiled Studio executable: {BUNDLE / 'Studio.exe'}")


def package_studio(runtime, iscc):
    if not (BUNDLE / "Studio.exe").is_file() or not BASE.is_dir():
        raise RuntimeError("The Studio bundle is missing. Run Compile.bat first.")
    if not iscc.is_file():
        raise RuntimeError(f"Inno Setup compiler not found: {iscc}")
    if runtime.name != WEBVIEW_INSTALLER:
        raise ValueError(f"Use the Microsoft WebView2 x64 offline installer named {WEBVIEW_INSTALLER}")
    if not runtime.is_file():
        runtime.parent.mkdir(parents=True, exist_ok=True)
        download = runtime.with_suffix(".download")
        subprocess.run(["curl.exe", "--fail", "--location", "--retry", "2",
                        "https://go.microsoft.com/fwlink/?linkid=2124701",
                        "--output", str(download)], check=True)
        download.replace(runtime)
    subprocess.run([
        str(iscc), f"/DBundleDir={BUNDLE}", f"/DBaseDir={BASE}", f"/DWebViewInstaller={runtime}",
        f"/O{RELEASE / 'installer'}", str(SOURCE / "packaging" / "Studio.iss")
    ], check=True)


if __name__ == "__main__":
    default_iscc = Path(os.environ["ProgramFiles(x86)"]) / "Inno Setup 6" / "ISCC.exe"
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["compile", "package"])
    parser.add_argument("--iscc", type=Path, default=default_iscc)
    parser.add_argument("--webview-installer", type=Path, default=RELEASE / "dependencies" / WEBVIEW_INSTALLER)
    args = parser.parse_args()
    if args.action == "compile":
        compile_studio()
    else:
        package_studio(args.webview_installer.resolve(), args.iscc.resolve())
