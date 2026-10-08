#!/usr/bin/env python3
"""Recheck pinned Python and vendored JavaScript against current advisories."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SECURITY = ROOT / "security"
SECURITY.mkdir(exist_ok=True)
subprocess.run(["uv", "export", "--locked", "--no-dev", "--no-emit-project", "--format", "requirements-txt", "-o", "requirements.txt", "--quiet"], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
subprocess.run(["uv", "export", "--locked", "--no-emit-project", "--format", "requirements-txt", "-o", "requirements-dev.txt", "--quiet"], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
for scope, requirements in (("runtime", "requirements.txt"), ("development", "requirements-dev.txt")):
    subprocess.run(["uv", "tool", "run", "pip-audit", "-r", requirements, "--disable-pip", "--no-deps",
                    "--format", "json", "-o", str(SECURITY / f"{scope}-audit.json")], cwd=ROOT, check=True)
packages = {"pdfjs-dist": ["6.4.299"], "html2canvas": ["1.4.1"]}
request = Request("https://registry.npmjs.org/-/npm/v1/security/advisories/bulk", data=json.dumps(packages).encode(),
                  headers={"Content-Type": "application/json"})
with urlopen(request, timeout=30) as response:
    advisories = json.load(response)
(SECURITY / "javascript-audit.json").write_text(json.dumps({"packages": packages, "advisories": advisories}, indent=2) + "\n")
if advisories:
    print("JavaScript advisories found; review security/javascript-audit.json", file=sys.stderr)
    sys.exit(1)
manifest = json.loads((ROOT / "vendor-manifest.json").read_text())
for filename, expected in manifest["files"].items():
    actual = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
    if actual != expected:
        sys.exit(f"Vendor integrity mismatch: {filename}")
print("Pinned dependency audits passed; vendored asset hashes match.")
