import base64
import hashlib
import io
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
from pypdf import PdfReader
import pytest

from forma import Handler, ThreadingHTTPServer, Workspace, choose_save_path, fixed_pdf, save_destination


def image_data(color="white"):
    stream = io.BytesIO()
    Image.new("RGBA", (40, 20), color).save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def test_copies_preserve_original_and_existing_symlinks(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"untouched source")
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = tmp_path / "My chosen copy.pdf"
    assert save_destination(destination, b"new PDF") == destination
    with pytest.raises(FileExistsError):
        save_destination(destination, b"replacement")
    assert destination.read_bytes() == b"new PDF"
    link = tmp_path / "link.pdf"; link.symlink_to(source)
    for path in (source, link):
        with pytest.raises(FileExistsError):
            save_destination(path, b"replacement")
    with pytest.raises(ValueError):
        save_destination(Path("relative.pdf"), b"new PDF")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_signed_export_has_expected_pages_and_no_form_or_scripts():
    page = {"width": 612, "height": 792, "image": image_data()}
    sig = {"page": 2, "x": .2, "y": .5, "width": .3, "height": .1, "image": image_data("black")}
    result = fixed_pdf([page, page], [sig])
    pdf = PdfReader(io.BytesIO(result))
    assert len(pdf.pages) == 2
    assert list(pdf.pages[0].mediabox) == [0, 0, 612, 792]
    assert len(pdf.pages[0]["/Resources"]["/XObject"]) == 1
    assert len(pdf.pages[1]["/Resources"]["/XObject"]) == 2
    assert "/AcroForm" not in pdf.trailer["/Root"]
    assert "/Names" not in pdf.trailer["/Root"]


def test_invalid_image_and_off_page_signature_rejected():
    page = {"width": 612, "height": 792, "image": image_data()}
    sig = {"page": 1, "x": .9, "y": .5, "width": .3, "height": .1, "image": image_data()}
    with pytest.raises(ValueError):
        fixed_pdf([page], [sig])
    with pytest.raises(ValueError):
        fixed_pdf([{**page, "image": "data:image/png;base64,dGhpcyBpcyBub3QgYSBQTkc="}], [])


@pytest.fixture
def server(tmp_path):
    service = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    service.workspace = Workspace(None, tmp_path)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    yield service
    service.shutdown()
    service.server_close()
    thread.join()


def request(server, path, body=None, token=True, **headers):
    if token:
        headers["X-Forma-Token"] = server.workspace.token
    return urlopen(Request(f"http://127.0.0.1:{server.server_port}{path}", data=body, headers=headers))


def test_http_requires_key_and_blocks_cross_origin_and_traversal(server):
    for path in ("/api/save", "/api/barcodes", "/api/qr", "/api/qr-pdf"):
        for kwargs in ({"token": False}, {"Origin": "https://malicious.example"}, {"Host": "attacker.example"}):
            with pytest.raises(HTTPError) as error:
                request(server, path, b"%PDF-test", **kwargs)
            assert error.value.code == 403
    assert not list(server.workspace.initial_folder.glob("*.pdf"))
    with pytest.raises(HTTPError) as error:
        request(server, "/%2e%2e/forma.py")
    assert error.value.code == 404
    data = json.load(request(server, "/api/session"))
    assert data["initialFolder"] == str(server.workspace.initial_folder)
    assert data["source"] is None and "documents" not in data
    with pytest.raises(HTTPError) as error:
        request(server, "/api/save", b"invalid")
    assert error.value.code == 400


def test_signing_returns_one_pdf_and_saves_only_at_explicit_destination(server):
    page = {"width": 612, "height": 792, "image": image_data()}
    payload = json.dumps({"pages": [page], "signatures": []}).encode()
    response = request(server, "/api/sign", payload, **{"Content-Type": "application/json"})
    assert response.headers["Content-Type"] == "application/pdf"
    pdf = response.read()
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 1
    assert not list(server.workspace.initial_folder.iterdir())
    with pytest.raises(HTTPError) as error:
        request(server, "/api/save", pdf)
    assert error.value.code == 400
    assert not list(server.workspace.initial_folder.iterdir())
    destination = server.workspace.initial_folder / "only signed document.pdf"
    headers = {"X-Forma-Destination": str(destination)}
    assert json.load(request(server, "/api/save", pdf, **headers))["paths"] == [str(destination)]
    assert list(server.workspace.initial_folder.iterdir()) == [destination]
    with pytest.raises(HTTPError) as error:
        request(server, "/api/save", b"%PDF-replacement", **headers)
    assert error.value.code == 409
    assert destination.read_bytes() == pdf


def test_native_save_dialog_selection_and_cancellation(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import forma
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundled/runtime")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system/libraries")
    monkeypatch.setattr(forma.shutil, "which", lambda name: "/usr/bin/kdialog" if name == "kdialog" else None)
    destination = tmp_path / "A selected file"
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=str(destination) + "\n", stderr="")
    monkeypatch.setattr(forma.subprocess, "run", run)
    selected = choose_save_path(tmp_path, "suggested.pdf", "Save editable copy")
    assert selected == {"native": True, "path": str(destination) + ".pdf"}
    assert not list(tmp_path.iterdir())
    assert "--getsavefilename" in calls[0][0]
    assert calls[0][1]["env"]["LD_LIBRARY_PATH"] == "/system/libraries"
    monkeypatch.setattr(forma.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""))
    assert choose_save_path(tmp_path, "suggested.pdf", "Save editable copy") == {"native": True, "cancelled": True}
