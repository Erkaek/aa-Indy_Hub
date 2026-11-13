"""Synchronize the packaged copy of the canonical industry-rules document."""

# Standard Library
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check without writing")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "docs/INDUSTRY_RULES.md"
    target = root / "indy_hub/data/industry/INDUSTRY_RULES.md"
    content = source.read_bytes()
    if args.check:
        if not target.exists() or target.read_bytes() != content:
            parser.error(
                "Packaged documentation differs; run "
                "python scripts/sync_industry_documentation.py"
            )
    else:
        target.write_bytes(content)
    print("PASS: canonical industry documentation matches its packaged copy")


if __name__ == "__main__":
    main()
