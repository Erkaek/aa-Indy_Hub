"""Check the published package contracts against actual built archives."""

# Standard Library
import sys
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

# Third Party
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet


def main(directory):
    directory = Path(directory)
    (wheel,) = directory.glob("*.whl")
    (sdist,) = directory.glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        (metadata_name,) = (
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        supported = SpecifierSet(metadata["Requires-Python"])
        assert all(v not in supported for v in ["3.10", "3.11"])
        assert all(v in supported for v in ["3.12", "3.13", "3.14"])
        requirements = [
            Requirement(value) for value in metadata.get_all("Requires-Dist")
        ]
        discord = [
            req.name
            for req in requirements
            if req.marker and req.marker.evaluate({"extra": "aadiscordbot"})
        ]
        assert "allianceauth-discordbot" in discord and "aadiscordbot" not in discord
        assert not any("temporary_prompt" in name for name in names)
        assert any(name.endswith("/chartjs/4.6.1/chart.umd.min.js") for name in names)
        assert any(name.endswith("/chartjs/4.6.1/LICENSE.md") for name in names)
    with tarfile.open(sdist) as archive:
        assert not any("temporary_prompt" in name for name in archive.getnames())
    print(
        "PASS: Python floor, Discord extra, local Chart.js/license and package contents"
    )


if __name__ == "__main__":
    main(sys.argv[1])
