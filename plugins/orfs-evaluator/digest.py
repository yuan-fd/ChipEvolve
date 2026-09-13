"""The plugin's single digest implementation.

G13 allows a plugin one implementation per concern, and no more.  Both the
evaluator and the parameter evidence need a file digest, so it lives here and
they import it rather than each carrying their own -- which is how v1 ended up
with the same function in more than a hundred places.
"""

from __future__ import annotations

from pathlib import Path

#: 1 MiB.  EDA artifacts can be large, and a single read would spike memory on a
#: machine that is simultaneously running a physical-design job.
CHUNK_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()
