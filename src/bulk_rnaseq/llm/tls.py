"""Verified DeepSeek TLS with the locked CA bundle and a targeted distrust rule."""
from hashlib import sha256
from importlib.resources import files
import re
import ssl


# emSign ECC Root CA - G3 (GHSA-6q48-6c7w-9gw2).
_DISTRUSTED = "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"


def deepseek_tls_context() -> ssl.SSLContext:
    bundle = files("certifi").joinpath("cacert.pem").read_text(encoding="utf-8")
    certificates = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", bundle, re.S)
    trusted = [pem for pem in certificates
               if sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() != _DISTRUSTED]
    if not trusted:
        raise ssl.SSLError("Empty application trust store")
    # Keep Python 3.13 client verification flags without honoring SSLKEYLOGFILE.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN | ssl.VERIFY_X509_STRICT
    context.load_verify_locations(cadata="\n".join(trusted))
    return context
