"""Certificate signatures checked independently with Poppler."""
import io
import shutil
import subprocess

import pytest
from reportlab.pdfgen import canvas
from digital_signature import prepare_certificate, sign_pdf, load_identity


def test_certificate_reuse_password_and_tamper_detection(tmp_path):
    request = {'mode': 'create', 'name': 'Forma Test Signer', 'password': 'test-password-123'}
    identity = prepare_certificate(request)
    assert identity['details']['selfIssued']
    assert identity['details']['name'] == 'Forma Test Signer'
    assert len(identity['details']['fingerprint']) == 64
    imported = prepare_certificate(dict(identity, mode='import', password=request['password']))
    assert imported == identity
    with pytest.raises(ValueError, match='unlock'):
        load_identity(dict(identity, password='wrong'))
    with pytest.raises(ValueError, match='at least 10'):
        prepare_certificate(dict(request, password='short'))
    with pytest.raises(ValueError):
        load_identity({'certificate': 'not base64', 'password': ''})
    out = io.BytesIO(); pdf = canvas.Canvas(out); pdf.drawString(30, 700, 'Sign me'); pdf.save()
    original = out.getvalue()
    signed = sign_pdf(original, dict(identity, password=request['password']))
    assert original == out.getvalue()
    verifier = shutil.which('pdfsig')
    if not verifier:
        pytest.skip('Independent signature verification requires pdfsig')
    path = tmp_path / 'signed.pdf'; path.write_bytes(signed)
    result = subprocess.run([verifier, str(path)], capture_output=True, text=True, check=True)
    assert 'Signature is Valid' in result.stdout
    assert 'Total document signed' in result.stdout
    assert 'Certificate issuer is unknown' in result.stdout
    # A harmless PDF header change leaves the file readable but breaks its hash.
    tampered = tmp_path / 'changed.pdf'; tampered.write_bytes(signed.replace(b'%PDF-1.3', b'%PDF-1.4', 1))
    assert tampered.read_bytes() != signed
    result = subprocess.run([verifier, str(tampered)], capture_output=True, text=True)
    assert 'Signature is Valid' not in result.stdout
