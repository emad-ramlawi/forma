"""Local certificate signing. Key material is used in memory, never persisted."""
import base64
from datetime import datetime, timedelta, timezone
import io
import secrets

from asn1crypto import keys, x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers
from pyhanko_certvalidator.registry import SimpleCertificateStore


def load_identity(payload):
    encoded, password = payload.get('certificate', ''), payload.get('password', '')
    if not isinstance(encoded, str) or len(encoded) > 3_000_000 or not isinstance(password, str) or len(password) > 1024:
        raise ValueError('Invalid certificate or password.')
    try:
        raw = base64.b64decode(encoded, validate=True)
        key, cert, chain = pkcs12.load_key_and_certificates(raw, password.encode() if password else None)
    except (ValueError, TypeError) as exc:
        raise ValueError('Could not unlock the PKCS#12 certificate. Check the file and password.') from exc
    if key is None or cert is None:
        raise ValueError('This certificate file must include its private signing key.')
    now = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
        raise ValueError('This certificate is expired or not yet valid.')
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        if not (usage.digital_signature or usage.content_commitment):
            raise ValueError('This certificate does not permit digital signatures.')
    except x509.ExtensionNotFound:
        pass
    return key, cert, chain


def certificate_details(cert):
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return {'name': names[0].value if names else cert.subject.rfc4514_string(),
            'issuer': cert.issuer.rfc4514_string(), 'expires': cert.not_valid_after_utc.isoformat(),
            'fingerprint': cert.fingerprint(hashes.SHA256()).hex(),
            'selfIssued': cert.issuer == cert.subject}


def prepare_certificate(payload):
    mode = payload.get('mode')
    if mode == 'create':
        name, password = payload.get('name', ''), payload.get('password', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError('Enter your name (up to 100 characters).')
        if not isinstance(password, str) or not 10 <= len(password) <= 1024:
            raise ValueError('Use a certificate password of at least 10 characters.')
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name.strip())])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(True, True, False, False, False, False, False, False, False), critical=True)
                .sign(key, hashes.SHA256()))
        raw = pkcs12.serialize_key_and_certificates(name.strip().encode(), key, cert, None,
                                                   serialization.BestAvailableEncryption(password.encode()))
        encoded = base64.b64encode(raw).decode()
    elif mode == 'import':
        _, cert, _ = load_identity(payload)
        encoded = payload['certificate']
    else:
        raise ValueError('Choose an existing certificate or create a self-signed one.')
    return {'certificate': encoded, 'details': certificate_details(cert)}


def sign_pdf(data, payload):
    key, cert, chain = load_identity(payload)
    store = SimpleCertificateStore()
    store.register_multiple(asn1_x509.Certificate.load(c.public_bytes(serialization.Encoding.DER)) for c in chain or ())
    signer = signers.SimpleSigner(
        signing_cert=asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER)),
        signing_key=keys.PrivateKeyInfo.load(key.private_bytes(serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())), cert_registry=store)
    writer = IncrementalPdfFileWriter(io.BytesIO(data))
    metadata = signers.PdfSignatureMetadata(field_name='FormaDigitalSignature_' + secrets.token_hex(6),
                                           md_algorithm='sha256', reason='Signed with Forma')
    return signers.sign_pdf(writer, metadata, signer=signer).getvalue()
