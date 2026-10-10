"""Barcode checks use an independent decoder, not the generating library."""
import base64
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, TextStringObject
import pytest

from xfa_qr import PREFIX, embed_qr, packets, profile, qr_payload, qr_preview, read_pdf, xml_packet

TEST_PDF = Path(__file__).resolve().parents[1] / "testpdf" / "pptc042.pdf"
COUNTRIES = {"CANADA": "CAN", "UNITED STATES OF AMERICA": "USA"}


def decode_image(data, path):
    executable = shutil.which("zbarimg")
    if not executable:
        pytest.skip("Independent QR decoding requires zbarimg")
    path.write_bytes(data)
    result = subprocess.run([executable, "--quiet", "--raw", "--nodbus", "-Sqrcode.binary=1", str(path)], capture_output=True, check=True)
    return result.stdout.decode("iso-8859-1").rstrip("\n")


def test_profile_field_order_normalization_country_codes_and_checksum():
    values = {"SN": "  Tremblay  ", "GN": "élise", "SB": "Roy, Smith", "DOB": "20200229",
              "PBC": "Canada", "PBP": "QC", "SX": "/F", "PAN": "10", "PAS": "main st",
              "PPC": "Canada", "PAPC": "h2x1y4", "MPC": "United States of America", "MAPC": "123456789"}
    payload, hidden = qr_payload(values, COUNTRIES)
    expected = PREFIX + "$SN$TREMBLAY$GN$ÉLISE$SB$ROY SMITH$DOB$2020-02-29$PBC$CAN$PBP$QC$SX$F$PAN$10$PAS$MAIN ST$PPC$CAN$PAPC$H2X 1Y4$MPC$USA$MAPC$12345-6789"
    # Explicit independent checksum vector, including DOB's hyphen exclusion.
    check_text = "TREMBLAYÉLISEROY SMITH20200229CANQCF10MAIN STCANH2X 1Y4USA12345-6789"
    assert payload == expected + f"$cs${sum(map(ord, check_text))}$c${len(check_text)}"
    assert hidden["DOB"] == "2020-02-29"
    assert qr_payload({}, COUNTRIES)[0] == PREFIX + "$cs$0$c$0"
    custom, _ = qr_payload({"PBC": "unlisted island", "PBP": "ON"}, COUNTRIES)
    assert custom == PREFIX + "$PBC$xxx: UNLISTED ISLAND$cs$" + str(sum(map(ord, "xxx UNLISTED ISLAND"))) + "$c$19"


@pytest.mark.parametrize("values", [{"SN": "bad$value"}, {"SN": "name123"}, {"DOB": "2020-02-31"},
                                    {"PPC": "CANADA", "PAPC": "invalid"}, {"GN": "李"}])
def test_invalid_barcode_values_are_reported(values):
    with pytest.raises(ValueError):
        qr_payload(values, COUNTRIES)


def test_iso88591_qr_is_decoded_independently(tmp_path):
    payload, _ = qr_payload({"SN": "Français", "GN": "Élise", "PBC": "CANADA"}, COUNTRIES)
    png = base64.b64decode(qr_preview(payload).split(",", 1)[1])
    assert decode_image(png, tmp_path / "qr.png") == payload


def test_xml_entities_and_doctypes_are_rejected():
    with pytest.raises(ValueError):
        xml_packet(b'<!DOCTYPE data [<!ENTITY name "unsafe">]><data>&name;</data>')


@pytest.mark.skipif(not TEST_PDF.exists(), reason="Local, ignored PPTC 042 test document is required")
def test_embedded_qr_preserves_fields_xfa_encryption_and_original(tmp_path):
    original = TEST_PDF.read_bytes()
    checksum = hashlib.sha256(original).digest()
    reader = read_pdf(original)
    spec = profile(reader)
    writer = PdfWriter(clone_from=reader)
    writer.update_page_form_field_values(writer.pages[0], {spec["bindings"]["SN"]: "Français",
                                                         spec["bindings"]["GN"]: "Élise",
                                                         spec["bindings"]["PBC"]: "CANADA"}, auto_regenerate=False)
    writer.encrypt("", owner_password="test-owner", algorithm="AES-256")
    draft = io.BytesIO(); writer.write(draft)
    result = embed_qr(draft.getvalue())
    saved = read_pdf(result)
    saved_spec = profile(saved)
    payload, _ = qr_payload(saved_spec["values"], saved_spec["countries"])
    assert saved.is_encrypted and len(saved.pages) == 8
    assert saved.get_fields()[spec["bindings"]["SN"]]["/V"] == "Français"
    widget = next(a.get_object() for a in saved.pages[0]["/Annots"] if a.get_object().get("/T") == "PaperFormsBarcode1[0]")
    assert widget["/V"] == payload
    appearance = widget["/AP"]["/N"]
    assert appearance["/Subtype"] == "/Form" and appearance.get_data().count(b"re f") > 100
    assert packets(saved)["template"].get_data() == packets(reader)["template"].get_data()
    datasets = ET.fromstring(packets(saved)["datasets"].get_data())
    hidden = next(n for n in datasets.iter() if n.tag.endswith("HiddenFieldsForBarcode"))
    assert hidden.find("SN").text == "FRANÇAIS"
    assert hidden.find("GN").text == "ÉLISE"
    assert hidden.find("PBC").text == "CAN"
    assert hashlib.sha256(TEST_PDF.read_bytes()).digest() == checksum
    if shutil.which("pdftoppm") and shutil.which("zbarimg"):
        path = tmp_path / "editable.pdf"; path.write_bytes(result)
        subprocess.run(["pdftoppm", "-f", "1", "-singlefile", "-r", "144", "-png",
                        str(path), str(tmp_path / "other-viewer")], capture_output=True, check=True)
        assert decode_image((tmp_path / "other-viewer.png").read_bytes(), tmp_path / "scanned.png") == payload
    # A new template revision must not inherit this version's calculation rules.
    changed = PdfWriter(clone_from=saved)
    template = packets(changed)["template"].get_data().replace(b"08-2026", b"09-2026")
    stream = DecodedStreamObject(); stream.set_data(template)
    xfa = changed.root_object["/AcroForm"]["/XFA"]
    for index in range(0, len(xfa), 2):
        if xfa[index] == "template":
            xfa[index + 1] = changed._add_object(stream)
    output = io.BytesIO(); changed.write(output)
    assert profile(PdfReader(output)) is None
    with pytest.raises(ValueError, match="template revision"):
        embed_qr(output.getvalue())


def test_selected_form_checks_report_multiple_errors_and_optional_fields():
    from xfa_qr import validate_form
    valid = {"SN": "Tremblay", "GN": "Élise", "DOB": "2020-02-29", "PB": "Montréal",
             "PBC": "CANADA", "SX": "F", "EYE": "BROWN", "HEIGHT": "120 cm",
             "PAN": "10", "PAS": "Main St", "PAC": "Montréal", "PPC": "CANADA", "PAPC": "H2X1Y4"}
    assert validate_form(valid, COUNTRIES) == []
    invalid = dict(valid, SN="name123", DOB="2020-02-31", PAPC="invalid", EYE="", SX="/Off")
    codes = {issue['code'] for issue in validate_form(invalid, COUNTRIES)}
    assert codes == {"SN", "DOB", "PAPC", "EYE", "SX"}
    assert {i['code'] for i in validate_form({}, COUNTRIES)} == set(valid)
    # An optional mailing address can be blank; a populated invalid ZIP is checked.
    assert validate_form(dict(valid, MPC="UNITED STATES OF AMERICA", MAPC="bad"), COUNTRIES)[0]['code'] == 'MAPC'
    with pytest.raises(ValueError):
        validate_form({'SN': []}, COUNTRIES)
