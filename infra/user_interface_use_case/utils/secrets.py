"""
Reversible obfuscation for the dev_key field.

IMPORTANT: base64 is NOT encryption. It only prevents a dev key from being
immediately human-readable when a use case JSON file is opened, previewed,
or diffed. Anyone with access to the file can trivially decode it. If real
secrecy is required later, replace this with an actual secrets manager or
authenticated encryption keyed outside of the stored file.
"""
from __future__ import annotations

import base64


def encode_dev_key(raw_dev_key: str) -> str:
    """Obfuscate a dev key before it is written to disk."""
    return base64.b64encode(raw_dev_key.encode("utf-8")).decode("ascii")


def decode_dev_key(encoded_dev_key: str) -> str:
    """Reverse encode_dev_key so a stored value can be shown/edited."""
    return base64.b64decode(encoded_dev_key.encode("ascii")).decode("utf-8")
