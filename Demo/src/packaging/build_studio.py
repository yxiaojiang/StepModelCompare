"""Build Studio.exe and the Inno Setup installer from source-controlled inputs."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
DEMO = SOURCE.parent
RELEASE = DEMO / "release"
WEBVIEW_INSTALLER = "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
BUNDLE = RELEASE / "bundle" / "Studio"
STAGING_DIST = RELEASE / "bundle-staging"
BASE = RELEASE / "demo-base"
TEST = RELEASE / "demo-test"
TEST_SEED = DEMO / "samples" / "rehearsal" / "queries"
DATA_DIR_NAME = "Data"
TEST_DIR_NAME = "test"
PUBLISH_PREFIX = ".studio-publish-"
TEST_MODELS = (
    "SHOW-DAMAGED.stp",
    "SHOW-HOLE.step",
    "SHOW-POSE-INCH.step",
    "SHOW-POSE-RETRY.stp",
    "SHOW-SYMMETRIC.step",
)


def _is_data_path(path):
    return path.name.casefold() == DATA_DIR_NAME.casefold()


def _is_publish_temp(path):
    return path.name.startswith(PUBLISH_PREFIX)


def _remove(path):
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _replace_item(src, dest):
    """Replace dest with a copy of src, without touching sibling Data.

    The new copy is written to a temporary name first. The previous item is
    renamed aside only after that copy succeeds. If the final swap fails, the
    previous item is restored when possible; otherwise the backup path is kept
    and included in the error.
    """
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{os.urandom(4).hex()}"
    tmp = parent / f"{PUBLISH_PREFIX}new-{dest.name}-{token}"
    bak = parent / f"{PUBLISH_PREFIX}old-{dest.name}-{token}"
    _remove(tmp)
    _remove(bak)
    try:
        if src.is_dir():
            shutil.copytree(src, tmp, symlinks=True)
        else:
            shutil.copy2(src, tmp)
        if dest.exists() or dest.is_symlink():
            dest.rename(bak)
        try:
            tmp.rename(dest)
        except BaseException:
            if not dest.exists() and bak.exists():
                try:
                    bak.rename(dest)
                except BaseException as restore_error:
                    raise RuntimeError(
                        f"Failed to publish {dest}. The previous item was left at {bak}."
                    ) from restore_error
            raise
        _remove(bak)
    except BaseException:
        raise


def prepare_test_models(dest=None):
    """Copy the tracked rehearsal query models into a packaging seed directory."""
    dest = Path(dest) if dest is not None else TEST
    dest.mkdir(parents=True, exist_ok=True)
    expected = {name.casefold(): name for name in TEST_MODELS}
    for path in list(dest.iterdir()):
        if not path.is_file() or path.name.casefold() not in expected:
            _remove(path)
    for name in TEST_MODELS:
        source = TEST_SEED / name
        if not source.is_file():
            raise RuntimeError(f"Missing packaged test model: {source}")
        if source.suffix.lower() not in {".step", ".stp"}:
            raise RuntimeError(f"Refusing to package a non-STEP test file: {source}")
        shutil.copy2(source, dest / name)
    found = {path.name.casefold() for path in dest.iterdir() if path.is_file()}
    if found != set(expected):
        raise RuntimeError(f"Packaged test directory contains unexpected files: {dest}")
    return dest


def seed_test_into_data(data_dir, source_dir=None):
    """Create Data/test and copy seed models only when the destination file is missing."""
    source_dir = Path(source_dir) if source_dir is not None else TEST
    dest = Path(data_dir) / TEST_DIR_NAME
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    skipped = []
    for name in TEST_MODELS:
        source = source_dir / name
        if not source.is_file():
            raise RuntimeError(f"Missing test seed model: {source}")
        target = dest / name
        if target.exists():
            skipped.append(name)
            continue
        shutil.copy2(source, target)
        copied.append(name)
    return copied, skipped


def publish_studio_bundle(built, dest):
    """Install a PyInstaller onedir build into dest without modifying dest/Data."""
    built = Path(built).resolve()
    dest = Path(dest).resolve()
    if not (built / "Studio.exe").is_file():
        raise RuntimeError(f"Studio build is missing Studio.exe: {built}")
    dest.mkdir(parents=True, exist_ok=True)
    built_names = {item.name for item in built.iterdir() if not _is_data_path(item)}
    for item in list(dest.iterdir()):
        if _is_data_path(item) or _is_publish_temp(item):
            continue
        if item.name not in built_names:
            _remove(item)
    for item in built.iterdir():
        if _is_data_path(item) or _is_publish_temp(item):
            continue
        _replace_item(item, dest / item.name)


def compile_studio():
    import OCP

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

    # PyInstaller --noconfirm --onedir deletes the entire dist/<name> directory,
    # including any Studio/Data next to the executable. Build into a staging
    # tree, then publish program files onto the real bundle and leave Data alone.
    if STAGING_DIST.exists():
        shutil.rmtree(STAGING_DIST)
    STAGING_DIST.mkdir(parents=True, exist_ok=True)
    ocp_dlls = Path(OCP.__file__).resolve().parent.parent / "cadquery_ocp_novtk.libs"
    try:
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed", "--noupx",
            "--name", "Studio", "--paths", str(SOURCE), "--workpath", str(work), "--specpath", str(work),
            "--distpath", str(STAGING_DIST),
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
        publish_studio_bundle(STAGING_DIST / "Studio", BUNDLE)
        prepare_test_models(TEST)
        seed_test_into_data(BUNDLE / "Data", TEST)
    except BaseException:
        # Keep the staging tree for diagnosis. BUNDLE/Data was not used as a
        # scratch directory. The next compile removes leftover staging first.
        raise
    shutil.rmtree(STAGING_DIST)
    print(f"Compiled Studio executable: {BUNDLE / 'Studio.exe'}")


def package_studio(runtime, iscc):
    if not (BUNDLE / "Studio.exe").is_file() or not BASE.is_dir():
        raise RuntimeError("The Studio bundle is missing. Run Compile.bat first.")
    prepare_test_models(TEST)
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
        str(iscc), f"/DBundleDir={BUNDLE}", f"/DBaseDir={BASE}", f"/DTestDir={TEST}",
        f"/DWebViewInstaller={runtime}", f"/O{RELEASE / 'installer'}",
        str(SOURCE / "packaging" / "Studio.iss")
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
