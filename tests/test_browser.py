"""Real PDF round trips and optional draw/type/image signing in Chromium."""
import hashlib
import base64
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, expect
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject, NameObject, TextStringObject
from test_qr import decode_image
import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_PDF = ROOT / "testpdf" / "pptc042.pdf"
ARTIFACTS = ROOT / "tests" / "artifacts"


@pytest.fixture
def browser_workspace(tmp_path, request):
    if not (TEST_PDF).is_file():
        pytest.skip("Browser round-trip checks require a local PDF test document (ignored by Git).")
    browser_path = os.environ.get("FORMA_TEST_BROWSER") or shutil.which("chromium") or shutil.which("google-chrome")
    if not browser_path:
        pytest.skip("Set FORMA_TEST_BROWSER to a current Chromium binary")
    launcher = os.environ.get("FORMA_TEST_LAUNCHER")
    if launcher:
        command = [launcher]
    else:
        app_folder = tmp_path / "application"; app_folder.mkdir()
        shutil.copy2(ROOT / "forma.py", app_folder / "forma.py")
        shutil.copy2(ROOT / "xfa_qr.py", app_folder / "xfa_qr.py")
        (app_folder / "web").symlink_to(ROOT / "web", target_is_directory=True)
        command = [sys.executable, str(app_folder / "forma.py")]
    environment = os.environ.copy()
    environment.pop("DISPLAY", None)
    environment.pop("WAYLAND_DISPLAY", None)
    count = getattr(request, "param", 0)
    for index in range(count):
        shutil.copy2(TEST_PDF, tmp_path / f"discovered-{index}.PDF")
    app = subprocess.Popen([*command, "--no-browser", "--output", str(tmp_path)],
                           cwd=tmp_path, stdout=subprocess.PIPE, text=True, env=environment)
    url = app.stdout.readline().strip().split("ready: ")[1]
    ARTIFACTS.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=browser_path, headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function("() => window.forma?.initialized")
        yield page, tmp_path, errors
        context.close()
        browser.close()
    app.terminate()
    app.wait(timeout=10)


def ready(page):
    page.wait_for_function("() => window.forma?.ready", timeout=60000)


def save_as(page, output, action="save", filename=None, cancel=False):
    page.locator(f"#{action}").click()
    expect(page.locator("#save-dialog")).to_be_visible()
    expect(page.locator("#save-folder")).to_have_value(str(output))
    expect(page.locator("#confirm-save")).to_be_enabled()
    if cancel:
        page.locator("#cancel-save").click()
    else:
        if filename:
            page.locator("#save-filename").fill(filename)
        page.locator("#confirm-save").click()
    ready(page)


def fill_sample(page):
    page.locator("#file").set_input_files(str(TEST_PDF))
    ready(page)
    surname = page.locator('input[name$="PersonalInfo[0].Surname[0]"]')
    surname.fill("LINUX TEST")
    page.locator('input[name$="PersonalInfo[0].GivenName[0]"]').fill("FORMA")
    country = page.locator('select[name$="PersonalInfo[0].PlaceBirthCountry[0]"]')
    country.select_option(value="UNITED STATES OF AMERICA")
    return surname


def new_signature(page, method):
    if page.locator("#new-signature").is_visible():
        page.locator("#new-signature").click()
    else:
        page.locator("#sign-tool").click()
    expect(page.locator("#signature-dialog")).to_be_visible()
    page.locator(f"#{method}-tab").click()


def place(page, x=.75, y=.46):
    page.locator("#use-signature").click()
    overlay = page.locator(".signature-overlay").first
    box = overlay.bounding_box()
    # Scroll to the relevant position if the signature falls below the viewport.
    page.locator("#main").evaluate("el => el.scrollTop = 0")
    page.mouse.click(box["x"] + box["width"] * x, box["y"] + box["height"] * y)
    expect(page.locator(".signature-placement")).not_to_have_count(0)


def test_pptc042_editable_round_trip_and_all_signature_methods(browser_workspace):
    page, output, errors = browser_workspace
    before = hashlib.sha256((TEST_PDF).read_bytes()).digest()
    page.screenshot(path=str(ARTIFACTS / "welcome.png"))
    surname = fill_sample(page)
    assert page.evaluate("window.forma.pageCount") == 8
    expect(page.locator("#signature-dialog")).not_to_be_visible()
    expect(page.locator("#export")).to_be_disabled()
    save_as(page, output)
    saved = list(output.glob("*-editable-*.pdf"))
    assert len(saved) == 1
    reader = PdfReader(saved[0]); reader.decrypt("")
    fields = reader.get_fields()
    barcode = next(v for k, v in fields.items() if k.endswith('.PaperFormsBarcode1[0]'))
    expected_qr = "$Form$042(08-2026)$V$1.4$SN$LINUX TEST$GN$FORMA$PBC$USA$cs$1358$c$18"
    assert barcode['/V'] == expected_qr
    preview = page.locator('.qr-preview')
    expect(preview).to_be_visible()
    png = base64.b64decode(preview.get_attribute('src').split(',', 1)[1])
    assert decode_image(png, output / 'qr-preview.png') == expected_qr
    key = next(k for k in fields if k.endswith("PersonalInfo[0].Surname[0]"))
    assert fields[key]["/V"] == "LINUX TEST"
    xfa = reader.trailer["/Root"]["/AcroForm"]["/XFA"]
    datasets = next(xfa[i + 1].get_object().get_data() for i in range(0, len(xfa), 2) if xfa[i] == "datasets")
    assert "LINUX TEST" in datasets.decode()
    page.locator("#file").set_input_files(str(saved[0])); expect(page.locator("#filename")).to_have_text(saved[0].name); ready(page)
    expect(page.locator('input[name$="PersonalInfo[0].Surname[0]"]')).to_have_value("LINUX TEST")
    # Zoom rebuilds the interactive layers while retaining changed values.
    page.locator("#zoom").select_option("1"); ready(page)
    expect(page.locator('input[name$="PersonalInfo[0].Surname[0]"]')).to_have_value("LINUX TEST")
    page.locator("#zoom").select_option("fit"); ready(page)
    page.screenshot(path=str(ARTIFACTS / "form.png"))
    save_as(page, output)
    assert len(list(output.glob("*-editable-*.pdf"))) == 2
    # Draw is optional and cancellable.
    new_signature(page, "draw")
    page.locator("#cancel-signature").click()
    assert page.evaluate("window.forma.signatureCount") == 0
    new_signature(page, "draw")
    box = page.locator("#signature-pad").bounding_box()
    page.mouse.move(box["x"] + 80, box["y"] + 100); page.mouse.down()
    for x, y in [(130, 40), (110, 115), (220, 65), (280, 110), (350, 90)]:
        page.mouse.move(box["x"] + x, box["y"] + y, steps=4)
    page.mouse.up(); place(page, .7, .42)
    # Type uses a bundled font, and the same placement flow.
    new_signature(page, "type")
    page.locator("#typed-name").fill("Forma Linux")
    expect(page.locator("#use-signature")).to_be_enabled()
    page.screenshot(path=str(ARTIFACTS / "signature-wizard.png"))
    place(page, .7, .46)
    # Imported scan, including removal of its white background.
    image_path = output / "signature.png"
    image = Image.new("RGB", (400, 130), "white")
    ImageDraw.Draw(image).line([(20, 90), (100, 20), (80, 100), (190, 60), (310, 100), (370, 50)], fill="black", width=5)
    image.save(image_path)
    new_signature(page, "image")
    page.locator("#signature-file").set_input_files(str(image_path))
    expect(page.locator("#use-signature")).to_be_enabled()
    place(page, .7, .5)
    assert page.evaluate("window.forma.signatureCount") == 3
    # Drag and proportional resizing stay inside the page.
    placement = page.locator(".signature-placement").last
    box = placement.bounding_box()
    page.mouse.move(box["x"] + 20, box["y"] + 10); page.mouse.down(); page.mouse.move(box["x"] + 40, box["y"] + 20); page.mouse.up()
    handle = placement.locator(".resize-handle").bounding_box()
    page.mouse.move(handle["x"] + 5, handle["y"] + 5); page.mouse.down(); page.mouse.move(handle["x"] + 40, handle["y"] + 10); page.mouse.up()
    page.screenshot(path=str(ARTIFACTS / "signed-preview.png"))
    save_as(page, output, "export", cancel=True)
    assert not list(output.glob("*-signed-*.pdf"))
    assert page.evaluate("window.forma.signatureCount") == 3
    assert page.evaluate("window.forma.dirty")
    save_as(page, output, "export")
    assert len(list(output.glob("*-signed-*.pdf"))) == 1
    assert len(list(output.glob("*-editable-*.pdf"))) == 2
    signed = PdfReader(next(output.glob("*-signed-*.pdf")))
    assert len(signed.pages) == 8
    assert "/AcroForm" not in signed.trailer["/Root"]
    assert len(signed.pages[0]["/Resources"]["/XObject"]) >= 4
    background = max(signed.pages[0].images, key=lambda image: image.image.width * image.image.height)
    assert decode_image(background.data, output / 'signed-qr.png') == expected_qr
    assert hashlib.sha256((TEST_PDF).read_bytes()).digest() == before
    assert errors == []


@pytest.mark.parametrize("browser_workspace", [1, 2], indirect=True)
def test_startup_stays_blank_with_pdfs_in_launch_folder(browser_workspace):
    page, output, errors = browser_workspace
    expect(page.locator("#welcome")).to_be_visible()
    expect(page.locator("#pages")).not_to_be_visible()
    assert not page.evaluate("window.forma.ready")
    assert page.locator("#detected-documents").count() == 0
    expect(page.locator("#open")).to_be_enabled()
    page.locator("#file").set_input_files(str(output / "discovered-0.PDF"))
    ready(page)
    expect(page.locator("#filename")).to_have_text("discovered-0.PDF")
    assert errors == []


def test_qr_updates_before_save_and_reopens_with_date_gender_and_postal_code(browser_workspace):
    page, output, errors = browser_workspace
    fill_sample(page)
    preview = page.locator('.qr-preview')
    expect(preview).to_be_visible()
    old_image = preview.get_attribute('src')
    page.locator('input[name$="PersonalInfo[0].Surname[0]"]').fill("Français")
    page.locator('input[name$="MorepersonalInformation[0].DOBYear[0]"]').fill("2020-02-29")
    page.locator('input[type=radio][name$="Gender[0].Sex[0]"][data-qr-value="F"]').check()
    page.locator('select[name$="CurrentAddress[0].Country[0]"]').select_option(value="CANADA")
    page.locator('input[name$="CurrentAddress[0].PC[0]"]').fill("h2x1y4")
    page.locator('#filename').click()
    expect(preview).to_be_visible()
    assert preview.get_attribute('src') != old_image
    value = decode_image(base64.b64decode(preview.get_attribute('src').split(',', 1)[1]), output / 'live-qr.png')
    assert "$SN$FRANÇAIS$GN$FORMA$DOB$2020-02-29$PBC$USA$SX$F$PPC$CAN$PAPC$H2X 1Y4$cs$" in value
    page.screenshot(path=str(ARTIFACTS / 'qr-form.png'))
    save_as(page, output, filename="with-qr.pdf")
    pdf = PdfReader(output / 'with-qr.pdf'); pdf.decrypt('')
    barcode = next(v for k, v in pdf.get_fields().items() if k.endswith('.PaperFormsBarcode1[0]'))
    assert barcode['/V'] == value
    page.locator('#file').set_input_files(str(output / 'with-qr.pdf'))
    expect(page.locator('#filename')).to_have_text('with-qr.pdf'); ready(page)
    expect(page.locator('.qr-preview')).to_be_visible()
    reopened = decode_image(base64.b64decode(page.locator('.qr-preview').get_attribute('src').split(',', 1)[1]), output / 'reopened-qr.png')
    assert reopened == value
    # Invalid edits remove the old QR and prevent saving a misleading barcode.
    page.locator('input[name$="PersonalInfo[0].Surname[0]"]').fill('BAD$NAME')
    expect(page.locator('.qr-preview')).not_to_be_visible()
    save_as(page, output, filename="invalid-qr.pdf")
    assert not (output / 'invalid-qr.pdf').exists()
    expect(page.locator('#notification')).to_contain_text("Remove '$'")
    assert not errors


def pure_xfa(path):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    packets = {
      "preamble": '<xdp:xdp xmlns:xdp="http://ns.adobe.com/xdp/">',
      "config": '<config xmlns="http://www.xfa.org/schema/xci/3.0/"><present><pdf><dynamicRender>required</dynamicRender></pdf></present></config>',
      "template": '''<template xmlns="http://www.xfa.org/schema/xfa-template/3.3/"><subform name="form1" layout="tb"><pageSet><pageArea name="Page1"><medium short="612pt" long="792pt"/><contentArea x="30pt" y="30pt" w="552pt" h="732pt"/></pageArea></pageSet><subform name="body" layout="tb"><draw name="title" w="400pt" h="35pt"><value><text>Pure XFA test</text></value><font size="20pt" typeface="Helvetica"/></draw><field name="fullName" w="350pt" h="30pt"><ui><textEdit/></ui><font size="14pt" typeface="Helvetica"/><value><text/></value></field></subform></subform></template>''',
      "datasets": '<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/"><xfa:data><form1><body><fullName/></body></form1></xfa:data></xfa:datasets>',
      "postamble": '</xdp:xdp>',
    }
    array = ArrayObject()
    for name, data in packets.items():
        stream = DecodedStreamObject(); stream.set_data(data.encode())
        array.extend([TextStringObject(name), writer._add_object(stream)])
    writer._root_object[NameObject("/AcroForm")] = writer._add_object(DictionaryObject({NameObject("/XFA"): array, NameObject("/Fields"): ArrayObject()}))
    writer._root_object[NameObject("/NeedsRendering")] = BooleanObject(True)
    writer.write(path)


def test_pure_xfa_round_trip_and_signed_export(browser_workspace):
    page, output, errors = browser_workspace
    source = output / "pure-xfa.pdf"; pure_xfa(source)
    page.locator("#file").set_input_files(str(source)); ready(page)
    field = page.locator(".xfaLayer input").first
    expect(field).to_be_visible()
    field.fill("PURE XFA ROUND TRIP")
    save_as(page, output)
    saved = next(output.glob("*-editable-*.pdf"))
    page.locator("#file").set_input_files(str(saved)); expect(page.locator("#filename")).to_have_text(saved.name); ready(page)
    expect(page.locator(".xfaLayer input").first).to_have_value("PURE XFA ROUND TRIP")
    new_signature(page, "type"); page.locator("#typed-name").fill("XFA Signature")
    expect(page.locator("#use-signature")).to_be_enabled(); place(page, .5, .2)
    save_as(page, output, "export")
    assert len(list(output.glob("*-signed-*.pdf"))) == 1
    signed = PdfReader(next(output.glob("*-signed-*.pdf")))
    assert len(signed.pages) == 1 and "/AcroForm" not in signed.trailer["/Root"]
    assert errors == []


def test_save_as_selected_folder_filename_and_cancellation(browser_workspace):
    page, output, errors = browser_workspace
    fill_sample(page)
    save_as(page, output, cancel=True)
    assert not list(output.glob("*.pdf"))
    assert page.evaluate("window.forma.dirty")
    expect(page.locator('input[name$="PersonalInfo[0].Surname[0]"]')).to_have_value("LINUX TEST")
    chosen = output / "My chosen folder"; chosen.mkdir()
    page.locator("#save").click()
    expect(page.locator("#save-dialog")).to_be_visible()
    expect(page.locator("#confirm-save")).to_be_enabled()
    page.locator("button.folder-entry").filter(has_text="My chosen folder").click()
    expect(page.locator("#save-folder")).to_have_value(str(chosen))
    expect(page.locator("#confirm-save")).to_be_enabled()
    page.locator("#save-filename").fill("My form copy")
    page.screenshot(path=str(ARTIFACTS / "save-as.png"))
    page.locator("#confirm-save").click(); ready(page)
    destination = chosen / "My form copy.pdf"
    assert destination.is_file()
    assert not list(output.glob("*.pdf"))
    previous = destination.read_bytes()
    page.locator('input[name$="PersonalInfo[0].Surname[0]"]').fill("CHANGED AGAIN")
    save_as(page, chosen, filename=destination.name)
    expect(page.locator("#notification")).to_contain_text("already exists")
    assert destination.read_bytes() == previous
    assert page.evaluate("window.forma.dirty")
    assert errors == []
