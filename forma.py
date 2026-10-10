#!/usr/bin/env python3
"""Forma: a loopback-only, offline PDF form workspace."""
from __future__ import annotations

import argparse
import base64
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import webbrowser

from digital_signature import prepare_certificate, sign_pdf
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from xfa_qr import embed_qr, profile, qr_payload, qr_preview, read_pdf, validate_form

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
MAX_BODY = 160 * 1024 * 1024
MAX_PAGES = 100


def safe_stem(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    stem = re.sub(r"[^\w .()-]", "_", Path(name).stem, flags=re.UNICODE)
    stem = re.sub(r"(?:-(?:editable|signed)-\d{8}-\d{6}(?:-[0-9a-f]{6}|-\d+)?)+$", "", stem)
    return (stem.strip(" .") or "document")[:120]


def save_destination(path: Path, data: bytes) -> Path:
    """Exclusive creation, including symlinks: no existing file is overwritten."""
    if not path.is_absolute() or path.suffix.lower() != ".pdf" or not path.parent.is_dir():
        raise ValueError("Choose an existing folder and a PDF filename.")
    with path.open("xb") as out:
        try:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        except OSError:
            path.unlink(missing_ok=True)
            raise
    return path


def external_environment() -> dict:
    """External desktop programs must not inherit a frozen runtime's library path."""
    environment = os.environ.copy()
    original_path = environment.pop("LD_LIBRARY_PATH_ORIG", None)
    if original_path:
        environment["LD_LIBRARY_PATH"] = original_path
    else:
        environment.pop("LD_LIBRARY_PATH", None)
    # PyInstaller's Qt hook variables must not configure the user's desktop tools.
    if getattr(sys, "frozen", False):
        environment.pop("QT_PLUGIN_PATH", None)
        environment.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
    return environment


def choose_save_path(folder: Path, filename: str, title: str) -> dict:
    """Use an installed desktop save dialog; otherwise the UI browses folders."""
    fallback = {"native": False, "folder": str(folder), "filename": filename}
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return fallback
    if executable := shutil.which("kdialog"):
        command = [executable, "--title", title, "--getsavefilename", str(folder / filename), "*.pdf"]
    elif executable := shutil.which("zenity"):
        command = [executable, "--file-selection", "--save", f"--title={title}",
                   f"--filename={folder / filename}", "--file-filter=PDF documents | *.pdf"]
    else:
        return fallback
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                env=external_environment(), timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return fallback
    if result.returncode == 1 and not result.stdout.strip():
        return {"native": True, "cancelled": True}
    if result.returncode != 0:
        return fallback
    selected = result.stdout.rstrip("\r\n")
    if not selected:
        return {"native": True, "cancelled": True}
    path = Path(selected)
    if path.suffix.lower() != ".pdf":
        path = path.with_name(path.name + ".pdf")
    return {"native": True, "path": str(path)}


def folder_contents(path: Path) -> dict:
    folder = path.expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("Choose a folder.")
    entries = []
    for item in folder.iterdir():
        if item.name.startswith("."):
            continue
        try:
            directory = item.is_dir()
            if directory or item.suffix.lower() == ".pdf":
                entries.append({"name": item.name, "path": str(item), "directory": directory})
        except OSError:
            continue
    entries.sort(key=lambda item: (not item["directory"], item["name"].casefold()))
    return {"folder": str(folder), "parent": str(folder.parent), "home": str(Path.home()),
            "entries": entries[:1000], "truncated": len(entries) > 1000}


def decode_image(value: str) -> bytes:
    if not isinstance(value, str) or not value.startswith("data:image/png;base64,"):
        raise ValueError("Expected a PNG image.")
    data = base64.b64decode(value.split(",", 1)[1], validate=True)
    try:
        with Image.open(io.BytesIO(data), formats=["PNG"]) as image:
            if image.width * image.height > 32_000_000:
                raise ValueError("Image exceeds the export limit.")
            image.verify()
    except (OSError, SyntaxError) as exc:
        raise ValueError("Could not read a PNG image in the export.") from exc
    return data


def fixed_pdf(pages: list, signatures: list) -> bytes:
    """Build a consistent, flattened PDF from rendered pages and signature images."""
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise ValueError(f"Export supports 1–{MAX_PAGES} pages.")
    if not isinstance(signatures, list) or len(signatures) > 100:
        raise ValueError("Too many signatures.")
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pageCompression=1)
    pdf.setTitle("Signed copy")
    pdf.setCreator("Forma 0.1.6 · visual signature export")
    for index, page in enumerate(pages):
        width, height = float(page["width"]), float(page["height"])
        if not 10 <= width <= 4000 or not 10 <= height <= 4000:
            raise ValueError("Invalid page dimensions.")
        pdf.setPageSize((width, height))
        image = decode_image(page["image"])
        pdf.drawImage(ImageReader(io.BytesIO(image)), 0, 0, width, height)
        for sig in signatures:
            if sig["page"] != index + 1:
                continue
            x, y, w, h = [float(sig[k]) for k in ("x", "y", "width", "height")]
            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1
                    and x + w <= 1.001 and y + h <= 1.001):
                raise ValueError("Signature must be inside its page.")
            data = decode_image(sig["image"])
            pdf.drawImage(ImageReader(io.BytesIO(data)), x * width, (1 - y - h) * height,
                          w * width, h * height, mask="auto")
        pdf.showPage()
    pdf.save()
    return output.getvalue()


class Workspace:
    def __init__(self, source: Path | None, output: Path):
        self.source = source
        self.protected_sources = set()
        self.qr_profiles = {}
        if self.source:
            self.protected_sources.add(self.source)
        candidate = output.expanduser().resolve()
        self.initial_folder = candidate if candidate.is_dir() else Path.home()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.close_on_window = False
        self.window_lock = threading.Lock()
        self.window_clients = set()
        self.close_deadline = None

    def window_event(self, client: str, action: str):
        if not isinstance(client, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", client) or action not in ("open", "close"):
            raise ValueError("Invalid window event.")
        with self.window_lock:
            if action == "open":
                self.window_clients.add(client)
                self.close_deadline = None
            elif client in self.window_clients:
                self.window_clients.remove(client)
                if not self.window_clients:
                    # A reload replaces the page; allow its new document to register.
                    self.close_deadline = time.monotonic() + 3

    def window_closed(self):
        with self.window_lock:
            return self.close_on_window and self.close_deadline is not None and time.monotonic() >= self.close_deadline


class Handler(BaseHTTPRequestHandler):
    server_version = "Forma/0.1"

    @property
    def workspace(self) -> Workspace:
        return self.server.workspace

    def log_message(self, *_):
        pass  # Document names and personal data never enter logs.

    def respond(self, data: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                         "font-src 'self' data: blob:; worker-src 'self' blob:; "
                         "connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def json(self, value, status=200):
        self.respond(json.dumps(value).encode(), "application/json", status)

    def trusted_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def authorized(self):
        origin = self.headers.get("Origin")
        expected = f"http://127.0.0.1:{self.server.server_port}"
        return (self.trusted_host() and (origin is None or origin == expected)
                and secrets.compare_digest(self.headers.get("X-Forma-Token", ""),
                                           self.workspace.token))

    def do_GET(self):
        if not self.trusted_host():
            self.json({"error": "Invalid host."}, 403)
            return
        route = unquote(urlsplit(self.path).path)
        if route.startswith("/api/"):
            if not self.authorized():
                self.json({"error": "This workspace requires its session key."}, 403)
            elif route == "/api/session":
                self.json({"initialFolder": str(self.workspace.initial_folder),
                           "source": self.workspace.source.name if self.workspace.source else None,
                           "closeOnWindow": self.workspace.close_on_window,
                           "version": "0.1.6"})
            elif route == "/api/folders":
                try:
                    path = Path(unquote(self.headers.get("X-Forma-Folder", "")))
                    if not path.is_absolute():
                        raise ValueError("Choose an absolute folder path.")
                    self.json(folder_contents(path))
                except (ValueError, OSError) as exc:
                    self.json({"error": str(exc)}, 400)
            elif route == "/api/source":
                path = self.workspace.source
                if path and path.is_file():
                    self.respond(path.read_bytes(), "application/pdf")
                else:
                    self.json({"error": "Document not found."}, 404)
            else:
                self.json({"error": "Not found."}, 404)
            return
        web = (ROOT / "web").resolve()
        path = (web / (route.lstrip("/") or "index.html")).resolve()
        if not path.is_relative_to(web) or not path.is_file():
            self.json({"error": "Not found."}, 404)
            return
        mime = "text/javascript" if path.suffix == ".mjs" else mimetypes.guess_type(path)[0]
        self.respond(path.read_bytes(), mime or "application/octet-stream")

    def do_POST(self):
        if not self.authorized():
            self.json({"error": "Unauthorized request."}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                self.json({"error": "This export exceeds the 160 MB limit."}, 413)
                return
            data = self.rfile.read(size)
            if len(data) != size:
                raise ValueError("Incomplete export. Try again.")
            route = urlsplit(self.path).path
            if route == "/api/window":
                payload = json.loads(data)
                self.workspace.window_event(payload["client"], payload["action"])
                self.json({"ok": True})
                return
            with self.workspace.lock:
                if route == "/api/barcodes":
                    spec = profile(read_pdf(data, unquote(self.headers.get("X-Forma-Password", ""))))
                    self.workspace.qr_profiles.clear()
                    if spec:
                        self.workspace.qr_profiles[spec["id"]] = spec["countries"]
                    self.json({"profile": {key: value for key, value in spec.items() if key != "countries"} if spec else None})
                elif route in ("/api/qr", "/api/validate"):
                    payload = json.loads(data)
                    countries = self.workspace.qr_profiles.get(payload["profile"])
                    if countries is None:
                        raise ValueError("Reopen this PDF to load its QR rules.")
                    if route == "/api/validate":
                        self.json({"issues": validate_form(payload["values"], countries)})
                    else:
                        value, _ = qr_payload(payload["values"], countries)
                        self.json({"image": qr_preview(value), "payload": value})
                elif route == "/api/qr-pdf":
                    self.respond(embed_qr(data, unquote(self.headers.get("X-Forma-Password", ""))), "application/pdf")
                elif route == "/api/choose-save":
                    payload = json.loads(data)
                    kind = payload["kind"]
                    if kind not in ("editable", "signed", "digital"):
                        raise ValueError("Unknown save action.")
                    filename = f"{safe_stem(payload['name'])}-{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.pdf"
                    title = {"editable": "Save editable copy", "signed": "Export signed PDF", "digital": "Export digitally signed PDF"}[kind]
                    self.json(choose_save_path(self.workspace.initial_folder, filename, title))
                elif route == "/api/save":
                    if not data.startswith(b"%PDF-"):
                        raise ValueError("Expected a PDF document.")
                    selected = unquote(self.headers.get("X-Forma-Destination", ""))
                    if not selected:
                        raise ValueError("Choose where to save the PDF first.")
                    path = Path(selected)
                    if path.resolve() in self.workspace.protected_sources:
                        raise ValueError("Choose a new filename to keep the original unchanged.")
                    path = save_destination(path, data)
                    self.workspace.initial_folder = path.parent
                    self.json({"paths": [str(path)]})
                elif route == "/api/certificate":
                    self.json(prepare_certificate(json.loads(data)))
                elif route == "/api/digital-sign":
                    payload = json.loads(data)
                    self.respond(sign_pdf(fixed_pdf(payload["pages"], payload["signatures"]), payload), "application/pdf")
                elif route == "/api/sign":
                    payload = json.loads(data)
                    signed = fixed_pdf(payload["pages"], payload["signatures"])
                    # Rendering produces bytes only. Saving is a separate, chosen destination.
                    self.respond(signed, "application/pdf")
                else:
                    self.json({"error": "Not found."}, 404)
        except FileExistsError:
            self.json({"error": "That filename already exists. Choose a new name; existing files are preserved."}, 409)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.json({"error": str(exc)}, 400)
        except Exception:
            self.json({"error": "Export failed. Your source file is unchanged."}, 500)


def main():
    parser = argparse.ArgumentParser(description="Forma — offline PDF forms and visual signatures")
    parser.add_argument("pdf", nargs="?", type=Path, help="PDF to open (never modified)")
    parser.add_argument("--output", type=Path, default=Path.home(),
                        help="Starting folder for the Save As chooser; every save still asks")
    parser.add_argument("--no-browser", action="store_true", help="Print the local URL")
    parser.add_argument("--port", type=int, default=0, help="Local port; default chooses a free port")
    args = parser.parse_args()
    if args.pdf and not args.pdf.is_file():
        parser.error(f"File not found: {args.pdf}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.workspace = Workspace(args.pdf.resolve() if args.pdf else None, args.output)
    stop = threading.Event()
    # Explicitly replace SIGINT even if a launcher inherited SIG_IGN.
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    browser = None if args.no_browser else next((shutil.which(b) for b in
        ("chromium", "chromium-browser", "google-chrome", "brave-browser") if shutil.which(b)), None)
    server.workspace.close_on_window = not args.no_browser and browser is None
    url = f"http://127.0.0.1:{server.server_port}/#{server.workspace.token}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .2}, daemon=True)
    thread.start()
    process = None
    profile_directory = None
    try:
        print(f"Forma is ready: {url}", flush=True)
        print("Choose the folder and filename on every save.\nPress Ctrl+C to quit.", flush=True)
        if not args.no_browser:
            browser_env = external_environment()
            if browser:
                # A separate profile prevents Chromium handing this window to an
                # existing browser process whose lifetime we cannot observe.
                profile_directory = tempfile.TemporaryDirectory(prefix="forma-browser-")
                process = subprocess.Popen([browser, f"--app={url}", "--new-window",
                    f"--user-data-dir={profile_directory.name}", "--no-first-run",
                    "--no-default-browser-check", "--disable-background-mode"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=browser_env,
                    start_new_session=True)
            elif shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env=browser_env, start_new_session=True)
            else:
                webbrowser.open(url)
        while not stop.wait(.2):
            if process is not None and process.poll() is not None:
                if process.returncode:
                    print("The app browser exited unexpectedly. Try --no-browser to open Forma manually.", file=sys.stderr)
                break
            if server.workspace.window_closed():
                break
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if process is not None:
            # This is only Forma's own isolated browser group, never the user's
            # existing browser. Clean up its children on terminal shutdown too.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
        if profile_directory is not None:
            profile_directory.cleanup()
        print("Forma closed.", flush=True)


if __name__ == "__main__":
    main()
