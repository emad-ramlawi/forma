#!/usr/bin/env python3
"""Build the single client executable at dist/forma. Build work stays ignored."""
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "single-binary"
DIST = ROOT / "dist"


def runtime_licenses() -> str:
    sections = ["Forma — original code and bundled runtime licenses", (ROOT / "LICENSE").read_text()]
    runtime = set()
    def include(name):
        normalized = name.lower().replace('_', '-')
        if normalized in runtime:
            return
        runtime.add(normalized)
        for requirement in importlib.metadata.requires(name) or ():
            parsed = Requirement(requirement)
            if parsed.marker is None or parsed.marker.evaluate({'extra': ''}):
                include(parsed.name)
    for dependency in tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['dependencies']:
        include(Requirement(dependency).name)
    runtime.add('pyinstaller')
    for name in sorted(runtime):
        package = importlib.metadata.distribution(name)
        for item in package.files or ():
            if re.search(r"(^|/)(licen[sc]e|copying|notice)", str(item), re.I):
                path = Path(package.locate_file(item))
                if path.is_file():
                    sections.extend([f"{name} {package.version} — {item}", path.read_text(errors="replace")])
    for relative in ("LICENSE", "LICENSE.txt", f"lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt"):
        path = Path(sys.base_prefix) / relative
        if path.is_file():
            sections.extend([f"Python {platform.python_version()}", path.read_text(errors="replace")])
            break
    else:
        raise RuntimeError("The bundled Python license text could not be found.")
    return "\n".join(line.rstrip() for line in "\n\n".join(sections).splitlines()) + "\n"


def record_native_assets(binaries, destination: Path) -> None:
    package = importlib.metadata.distribution("pyinstaller")
    bootloader = Path(package.locate_file("PyInstaller/bootloader/Linux-64bit-intel/run"))
    binaries = [*binaries, ("PyInstaller-bootloader", str(bootloader), "BINARY")]
    native = []
    for name, source, kind in sorted(binaries):
        path = Path(source)
        if kind == "SYMLINK" or not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        symbols = subprocess.run(["readelf", "--version-info", str(path)], capture_output=True, text=True, check=True).stdout
        glibc = sorted(set(re.findall(r"GLIBC_(\d+\.\d+)", symbols)), key=lambda v: tuple(map(int, v.split("."))))
        cpp = sorted(set(re.findall(r"GLIBCXX_(\d+(?:\.\d+)+)", symbols)), key=lambda v: tuple(map(int, v.split("."))))
        native.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "highest_required_glibc": glibc[-1] if glibc else None,
                       "highest_required_glibcxx": cpp[-1] if cpp else None})
    requirements = [entry["highest_required_glibc"] for entry in native if entry["highest_required_glibc"]]
    highest = max(requirements, key=lambda v: tuple(map(int, v.split(".")))) if requirements else None
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    destination.write_text(json.dumps({"built_at": datetime.now(timezone.utc).isoformat(),
        "application_version": version, "python": platform.python_version(),
        "architecture": platform.machine(), "build_host": platform.platform(),
        "highest_required_glibc": highest, "native_libraries": native}, indent=2) + "\n")


def main() -> None:
    if platform.machine() != "x86_64" or platform.system() != "Linux":
        sys.exit("This build currently targets Linux x86_64.")
    if not shutil.which("readelf"):
        sys.exit("Install binutils (readelf) to record native library requirements.")
    BUILD.mkdir(parents=True, exist_ok=True)
    assets = BUILD / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    web = assets / "web"
    shutil.copytree(ROOT / "web", web)
    # Include renderer assets only: no document scripts, demo PDFs, unused viewer,
    # source maps, or viewer locales in the client binary.
    for path in list(web.rglob("*.map")) + list((web / "vendor" / "pdfjs" / "build").glob("pdf.sandbox.*")):
        path.unlink(missing_ok=True)
    for relative in ("probe.html", "vendor/pdfjs/web/viewer.html", "vendor/pdfjs/web/viewer.mjs",
                     "vendor/pdfjs/web/wasm/quickjs-eval.js", "vendor/pdfjs/web/wasm/quickjs-eval.wasm"):
        (web / relative).unlink(missing_ok=True)
    for path in web.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".pdf":
            path.unlink()
    shutil.rmtree(web / "vendor" / "pdfjs" / "web" / "locale", ignore_errors=True)
    # This tracked license snapshot is also available when running from source.
    licenses = runtime_licenses()
    (ROOT / "web" / "runtime-licenses.txt").write_text(licenses)
    (web / "runtime-licenses.txt").write_text(licenses)
    docs = web / "docs"; docs.mkdir()
    for name in ("README.md", "project.md", "SECURITY.md", "LICENSE", "vendor-manifest.json", "requirements.txt"):
        shutil.copy2(ROOT / name, docs / name)
    shutil.copytree(ROOT / "security", docs / "security")
    spec = BUILD / "forma.spec"
    spec.write_text(f'''# Generated by scripts/build.py; edit the builder instead.
from pathlib import Path
import runpy
from PyInstaller.utils.hooks import collect_submodules

a = Analysis([{str(ROOT / "forma.py")!r}], pathex=[{str(ROOT)!r}],
    binaries=[], datas=[({str(web)!r}, 'web')],
    hiddenimports=collect_submodules('reportlab.graphics.barcode'),
    hookspath=[], hooksconfig={{}}, runtime_hooks=[],
    excludes=['tkinter', 'playwright', 'pytest'], noarchive=False, optimize=0)
# Use the receiving distribution's ABI libraries instead of the build host's.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name not in ('libstdc++.so.6', 'libgcc_s.so.1')]
support = runpy.run_path({str(Path(__file__).resolve())!r}, run_name='forma_build_support')
support['record_native_assets'](a.binaries, Path({str(web / "build-info.json")!r}))
a.datas.append(('web/build-info.json', {str(web / "build-info.json")!r}, 'DATA'))
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='forma',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
''')
    subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                    "--distpath", str(BUILD / "output"), "--workpath", str(BUILD / "work"), str(spec)],
                   cwd=ROOT, check=True)
    candidate = BUILD / "output" / "forma"
    if candidate.stat().st_size >= 100 * 1024 * 1024:
        sys.exit("Binary exceeds GitHub's ordinary Git file limit; reduce the bundle before publishing.")
    DIST.mkdir(exist_ok=True)
    temporary = DIST / ".forma.new"
    shutil.copy2(candidate, temporary)
    temporary.chmod(0o755)
    temporary.replace(DIST / "forma")
    # Remove only known artifacts from the old tar.gz / directory builder.
    for name in ("forma-linux-x86_64", "staging"):
        path = DIST / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
    for name in ("forma-linux-x86_64.tar.gz", "SHA256SUMS"):
        (DIST / name).unlink(missing_ok=True)
    manifest = json.loads((web / "build-info.json").read_text())
    manifest["artifact"] = {"path": "dist/forma", "sha256": hashlib.sha256((DIST / "forma").read_bytes()).hexdigest(),
                            "size_bytes": (DIST / "forma").stat().st_size}
    (ROOT / "build-info.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Built dist/forma ({candidate.stat().st_size / 1024 / 1024:.1f} MiB); highest required glibc: {manifest['highest_required_glibc']}")


if __name__ == "__main__":
    main()
