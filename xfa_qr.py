"""Explicit XFA barcode rules. PDF scripts are never evaluated.

The first supported profile is PPTC 042, revision 08-2026, barcode version 1.4.
A template fingerprint prevents applying its rules to a different form/version.
"""
from __future__ import annotations

import base64
from datetime import date
import hashlib
import io
import re
import secrets
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, TextStringObject
from reportlab.graphics.barcode.qrencoder import QRCode, QR8bitByte, QRErrorCorrectLevel

TEMPLATE_SHA256 = "60b9f4525b556f83cdf943320632877c13c8a70da7bb8d298d87153515839b64"
PREFIX = "$Form$042(08-2026)$V$1.4"
PERSONAL = "TitleAndNameInformation[0].PersonalInfo[0]."
MORE = "MorepersonalInformation[0]."
SUFFIXES = {
    "SN": PERSONAL + "Surname[0]", "GN": PERSONAL + "GivenName[0]",
    "SB": PERSONAL + "SurnameBirth[0]", "DOB": MORE + "DOBYear[0]",
    "PB": PERSONAL + "City[0].PlaceBirthCity[0]",
    "PBC": PERSONAL + "PlaceBirthCountry[0]", "PBP": PERSONAL + "PlaceBirthProv[0]",
    "SX": MORE + "Gender[0].Sex[0]",
}
for address, codes in (("CurrentAddress", ("PAN", "PAS", "PAA", "PAC", "PPC", "PAPC")),
                       ("MailingAddress", ("MAN", "MAS", "MAA", "MAC", "MPC", "MAPC"))):
    for field, code in zip(("Number", "Street", "Apt", "City", "Country", "PC"), codes):
        SUFFIXES[code] = MORE + f"{address}[0].{field}[0]"


def read_pdf(data: bytes, password: str = "") -> PdfReader:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(password):
        raise ValueError("Could not unlock the PDF for its QR code.")
    return reader


def xml_packet(data: bytes) -> ET.Element:
    if len(data) > 16 * 1024 * 1024 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Unsupported XFA XML packet.")
    return ET.fromstring(data)


def packets(reader: PdfReader) -> dict:
    acro = reader.root_object.get("/AcroForm")
    xfa = acro.get_object().get("/XFA") if acro else None
    if not isinstance(xfa, ArrayObject):
        return {}
    return {str(xfa[i]): xfa[i + 1].get_object() for i in range(0, len(xfa) - 1, 2)}


def profile(reader: PdfReader) -> dict | None:
    xfa = packets(reader)
    if "template" not in xfa:
        return None
    template = xfa["template"].get_data()
    if hashlib.sha256(template).hexdigest() != TEMPLATE_SHA256:
        return None
    root = xml_packet(template)
    countries = {}
    for script in root.iter():
        if script.tag.endswith("}script") and "function CountryList()" in (script.text or ""):
            literal = re.search(r"function CountryList\(\)\s*\{\s*var countries = new Array\((.*?)\);", script.text, re.S)
            if literal:
                for name, code in re.findall(r'"([^"\n]+)\+([A-Z0-9]{3})"', literal[1]):
                    countries[name.strip().upper()] = code
    fields = reader.get_fields() or {}
    bindings = {code: next((name for name in fields if name.startswith("PPTC_042[0].Page1[0].personalInformation[0].")
                           and name.endswith(suffix)), "") for code, suffix in SUFFIXES.items()}
    if not all(bindings.values()) or not countries:
        raise ValueError("The supported QR form is missing its input fields.")
    values = {}
    for code, name in bindings.items():
        value = fields[name].get("/V", fields[name].get("/DV", ""))
        # PDF.js saves even a single-select choice as an array of export values.
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if code == "SX" and re.fullmatch(r"/\d+", str(value)):
            index = int(str(value)[1:])
            options = fields[name].get("/Opt", [])
            value = options[index] if index < len(options) else ""
        values[code] = str(value or "")
    return {"id": TEMPLATE_SHA256, "bindings": bindings, "countries": countries, "values": values}


def barcode_values(values: dict, countries: dict) -> dict:
    """Mirror the profile's formatting, field order, and barcode validation."""
    if not isinstance(values, dict) or not isinstance(countries, dict):
        raise ValueError("Invalid QR form values.")
    result = {}
    letters = "a-zàâéêëèïîôüùûç"
    for code in SUFFIXES:
        raw = values.get(code, "")
        if not isinstance(raw, str) or len(raw) > 1000:
            raise ValueError("QR field exceeds the supported limit.")
        value = re.sub(r"\s{2,}", " ", raw.strip()).upper()
        if not value or value == "/OFF":
            continue
        if "$" in value:
            raise ValueError("Remove '$' from the form values before generating its QR code.")
        if code in ("SN", "GN", "SB"):
            pattern = rf"[\s{letters}',-]+" if code == "SB" else rf"[\s{letters}'-]+"
            if not re.fullmatch(pattern, value, re.I):
                raise ValueError("A name contains characters unsupported by this form's QR code.")
            if code == "SB":
                value = re.sub(r"\s{2,}", " ", value.replace(",", " ")).strip()
        elif code == "DOB":
            if re.fullmatch(r"\d{8}", value):
                value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
            try:
                birth = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Enter the child's birth date as YYYY-MM-DD for the QR code.") from exc
            today = date.today()
            cutoff = date(today.year - 16, today.month, min(today.day, 28) if today.month == 2 else today.day)
            if not cutoff <= birth <= today:
                raise ValueError("The birth date must be within this child form's age range for its QR code.")
            value = birth.isoformat()
        elif code in ("PBC", "PPC", "MPC"):
            if value == "----------":
                continue
            country = countries.get(value)
            # Only the birth-country script supports an unlisted country.
            if not country and code != "PBC":
                raise ValueError("Choose a listed address country for this form's QR code.")
            value = country or f"xxx: {value}"
        elif code == "PBP":
            if re.sub(r"\s+", " ", str(values.get("PBC", "")).strip()).upper() != "CANADA":
                continue
        elif code == "SX":
            value = value.removeprefix("/")
            if value not in ("F", "M", "X"):
                continue
        elif code in ("PAPC", "MAPC"):
            country = str(values.get("PPC" if code == "PAPC" else "MPC", "")).strip().upper()
            if country == "CANADA" and not re.fullmatch(r"[A-Z]\d[A-Z]\s?\d[A-Z]\d", value):
                raise ValueError("Enter a valid Canadian postal code for the QR code.")
            if country == "UNITED STATES OF AMERICA" and not re.fullmatch(r"\d{5}([\s-]?\d{4})?", value):
                raise ValueError("Enter a valid US ZIP code for the QR code.")
            if country not in ("CANADA", "UNITED STATES OF AMERICA") and not re.fullmatch(rf"[\s\d{letters}/.,!'-]+", value, re.I):
                raise ValueError("A postal code contains characters unsupported by this form's QR code.")
            if re.fullmatch(r"[A-Z]\d[A-Z]\d[A-Z]\d", value):
                value = value[:3] + " " + value[3:]
            elif re.fullmatch(r"\d{9}|\d{5} \d{4}", value):
                value = value[:5] + "-" + value[-4:]
        elif not re.fullmatch(rf"[\s\da-zàâéêëèïîôüùûç/.,!'-]+", value, re.I):
            raise ValueError("An address or birthplace contains characters unsupported by this form's QR code.")
        result[code] = value
    return result


def qr_payload(values: dict, countries: dict) -> tuple[str, dict]:
    formatted = barcode_values(values, countries)
    checksum = count = 0
    payload = PREFIX
    for code, value in formatted.items():
        payload += f"${code}${value}"
        check = value.replace(":", "", 1) if code == "PBC" else value.replace("-", "") if code == "DOB" else value
        checksum += sum(ord(char) for char in check)
        count += len(check)
    payload += f"$cs${checksum}$c${count}"
    try:
        payload.encode("iso-8859-1")
    except UnicodeEncodeError as exc:
        raise ValueError("This form's QR code only supports ISO-8859-1 text.") from exc
    return payload, formatted


def qr_matrix(payload: str) -> list:
    # XFA errorCorrectionLevel 1 corresponds to level M (the XFA enum differs
    # from QR format bits). The profile's initialize event
    # explicitly selects ISO-8859-1, so passing a Unicode string would be wrong.
    qr = QRCode(None, QRErrorCorrectLevel.M)
    qr.addData(QR8bitByte(payload.encode("iso-8859-1")))
    qr.make()
    return qr.modules


def qr_preview(payload: str) -> str:
    matrix = qr_matrix(payload)
    pitch, border = 6, 4
    image = Image.new("RGB", ((len(matrix) + border * 2) * pitch,) * 2, "white")
    draw = ImageDraw.Draw(image)
    for y, row in enumerate(matrix):
        for x, black in enumerate(row):
            if black:
                left, top = (x + border) * pitch, (y + border) * pitch
                draw.rectangle((left, top, left + pitch - 1, top + pitch - 1), fill="black")
    output = io.BytesIO(); image.save(output, "PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def embed_qr(data: bytes, password: str = "") -> bytes:
    """Save the vector QR appearance and matching XFA hidden fields in a copy."""
    reader = read_pdf(data, password)
    spec = profile(reader)
    if not spec:
        raise ValueError("QR calculation is not supported for this XFA template revision.")
    payload, hidden = qr_payload(spec["values"], spec["countries"])
    matrix = qr_matrix(payload)
    writer = PdfWriter(clone_from=reader)
    found = False
    for page in writer.pages:
        for reference in page.get("/Annots", []):
            widget = reference.get_object()
            if widget.get("/T") != "PaperFormsBarcode1[0]":
                continue
            x1, y1, x2, y2 = map(float, widget["/Rect"])
            width, height = x2 - x1, y2 - y1
            pitch = min(width, height) / (len(matrix) + 8)
            left, bottom = (width - pitch * len(matrix)) / 2, (height - pitch * len(matrix)) / 2
            commands = [f"q 1 g 0 0 {width:.6f} {height:.6f} re f 0 g"]
            for y, row in enumerate(matrix):
                for x, black in enumerate(row):
                    if black:
                        commands.append(f"{left + x * pitch:.6f} {bottom + (len(matrix)-y-1)*pitch:.6f} {pitch:.6f} {pitch:.6f} re f")
            commands.append("Q")
            appearance = DecodedStreamObject()
            appearance.set_data("\n".join(commands).encode("ascii"))
            appearance.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                               NameObject("/BBox"): ArrayObject([FloatObject(n) for n in (0, 0, width, height)]),
                               NameObject("/Resources"): DictionaryObject()})
            widget[NameObject("/AP")] = DictionaryObject({NameObject("/N"): writer._add_object(appearance)})
            widget[NameObject("/V")] = TextStringObject(payload)
            found = True
    if not found:
        raise ValueError("The QR barcode widget could not be found.")
    xfa = packets(writer)
    datasets = xml_packet(xfa["datasets"].get_data())
    target = next((node for node in datasets.iter() if node.tag.split("}")[-1] == "HiddenFieldsForBarcode"), None)
    if target is None:
        raise ValueError("The XFA barcode dataset could not be found.")
    for node in target:
        code = node.tag.split("}")[-1]
        if code in SUFFIXES:
            node.text = hidden.get(code, "")
    replacement = DecodedStreamObject(); replacement.set_data(ET.tostring(datasets, encoding="utf-8"))
    form = writer.root_object["/AcroForm"]
    form[NameObject("/NeedAppearances")] = BooleanObject(False)
    array = form["/XFA"]
    for i in range(0, len(array), 2):
        if array[i] == "datasets":
            array[i+1] = writer._add_object(replacement)
    # pypdf cannot encrypt incremental updates. A complete copy preserves the
    # encryption requirement and permissions without changing the source file.
    if reader.is_encrypted:
        writer.encrypt(password, owner_password=secrets.token_urlsafe(32),
                       permissions_flag=reader.user_access_permissions, algorithm="AES-256")
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()
