"""Restore one JSON baseline without extracting any archive paths."""

# Standard Library
import argparse
import json
import zipfile
from pathlib import Path


def restore(archive, target):
    if not archive.exists():
        return
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if (
            len(entries) != 1
            or entries[0].filename != target.name
            or entries[0].file_size > 3 * 1024 * 1024
        ):
            raise ValueError("Unexpected baseline archive contents")
        data = json.loads(source.read(entries[0]))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    restore(args.archive, args.target)
