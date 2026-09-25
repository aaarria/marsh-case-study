from __future__ import annotations

import hashlib
import uuid


def stable_id(*parts: str, length: int = 12) -> str:
    h = hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()
    return h[:length]


def new_id(prefix: str = "") -> str:
    u = uuid.uuid4().hex[:12]
    return f"{prefix}_{u}" if prefix else u
