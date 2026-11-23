"""Global content qualification of installed CCP data; HTTP is worker/operator only."""

# Standard Library
import hashlib
import logging
import tempfile
import time
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

# Third Party
import requests

# Django
from django.db import DatabaseError, transaction
from django.utils import timezone

# AA Example App
from indy_hub.models import IndustryDataQualification
from indy_hub.services.industry_rule_projection import (
    MAX_ARCHIVE_BYTES,
    build_bundle,
    fingerprint,
    qualify_projection,
    read_runtime_archive,
)
from indy_hub.services.industry_rules import (
    INDUSTRY_RULES_VERSION,
    IndustryRulesError,
    _validated_data,
)

logger = logging.getLogger(__name__)
QUALIFICATION_SECTIONS = frozenset(
    {"TypeDogma", "TypeEffect", "DogmaEffect", "DogmaAttribute"}
)
CONTEXT_SECTIONS = QUALIFICATION_SECTIONS | {"ItemType", "ItemGroup", "ItemCategory"}
READY_STATUSES = frozenset(
    {"compatible_qualified", "compatible_conditional", "incompatible"}
)
ARCHIVE_URL = "https://developers.eveonline.com/static-data/tranquility/eve-online-static-data-{build}-jsonl.zip"
LEASE_SECONDS = 20 * 60
RETRY_SECONDS = 60 * 60
QUALIFIER_VERSION = 1


def qualification_context(rows):
    """Include each relevant import timestamp, including repeated build numbers."""
    relevant = sorted(
        (row[0], row[1], str(row[2])) for row in rows if row[0] in CONTEXT_SECTIONS
    )
    signature = fingerprint(relevant)
    builds = sorted(
        {
            int(build)
            for name, build, _ in relevant
            if name in QUALIFICATION_SECTIONS and build is not None
        }
    )
    return signature, builds


def qualification_key(build, signature):
    return fingerprint([INDUSTRY_RULES_VERSION, QUALIFIER_VERSION, signature, build])


def external_qualifications(rows):
    """Read committed evidence only. Never download or publish work from a page.

    Read DB generations on every call so new results invalidate warm calculation
    caches in all processes, without relying on a running worker or Redis TTL.
    """
    data = _validated_data()
    signature, builds = qualification_context(rows)
    keys = [
        qualification_key(build, signature)
        for build in builds
        if str(build) not in data["qualifications"]
    ]
    if not keys:
        return {}, "bundled"
    try:
        records = list(
            IndustryDataQualification.objects.filter(qualification_key__in=keys).values(
                "build_number", "status", "evidence", "evidence_hash", "reason"
            )
        )
    except DatabaseError:
        logger.warning("Industry qualification evidence is unavailable", exc_info=True)
        return {}, "unavailable"
    evidence, generations = {}, []
    for record in records:
        payload = record["evidence"]
        valid = (
            record["status"] in READY_STATUSES
            and isinstance(payload, dict)
            and payload.get("status") == record["status"]
            and isinstance(payload.get("source_sha256"), dict)
            and fingerprint(payload) == record["evidence_hash"]
        )
        if valid:
            evidence[str(record["build_number"])] = payload
        generations.append(
            [record["build_number"], record["status"], record["evidence_hash"], valid]
        )
    return evidence, fingerprint(sorted(generations))[:16]


def _download_archive(build, target):
    """One bounded public CCP download; redirects and retry loops are disabled."""
    started, size, digest = time.monotonic(), 0, hashlib.sha256()
    with requests.get(
        ARCHIVE_URL.format(build=build),
        stream=True,
        timeout=(10, 30),
        allow_redirects=False,
    ) as response:
        if response.status_code != 200:
            raise IndustryRulesError(
                f"CCP archive returned HTTP {response.status_code}"
            )
        if int(response.headers.get("Content-Length", 0)) > MAX_ARCHIVE_BYTES:
            raise IndustryRulesError("Official SDE archive exceeds the size limit")
        with Path(target).open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES or time.monotonic() - started > 180:
                    raise IndustryRulesError(
                        "Official SDE download exceeded its size/time limit"
                    )
                output.write(chunk)
                digest.update(chunk)
    return digest.hexdigest()


def _archive_evidence(path, build):
    datasets, hashes = read_runtime_archive(path, build)
    candidate, evidence = build_bundle(datasets, build)
    reference = _validated_data()
    evidence["status"] = qualify_projection(
        reference["qualifications"][str(reference["sde_build"])], evidence
    )
    if evidence["status"] == "compatible_conditional":
        evidence["source_attribute_values"] = {
            source_id: source["attribute_values"]
            for source_id, source in candidate["sources"].items()
            if source["attribute_values"]
            != reference["sources"][source_id]["attribute_values"]
        }
    return evidence, hashes


def check_installed_industry_rules(*, archive=None, force=False):
    """Qualify current unknown builds with a DB lease and a durable retry deadline."""
    # Local import avoids the calculation module's reciprocal evidence reader.
    # AA Example App
    from indy_hub.services.industry_structures import _get_industry_sde_sections

    rows = _get_industry_sde_sections()
    signature, builds = qualification_context(rows)
    present = {row[0] for row in rows if row[1] is not None}
    if CONTEXT_SECTIONS - present:
        return {
            "status": "unqualified",
            "reason": "Missing installed SDE section metadata",
        }
    reference = _validated_data()
    results = {}
    if archive is not None and len(builds) != 1:
        raise IndustryRulesError(
            "An offline archive requires a single installed Dogma build"
        )
    for build in builds:
        if str(build) in reference["qualifications"]:
            results[str(build)] = "bundled"
            continue
        now, token = timezone.now(), uuid.uuid4().hex
        key = qualification_key(build, signature)
        with transaction.atomic():
            record, _ = IndustryDataQualification.objects.get_or_create(
                qualification_key=key,
                defaults={
                    "build_number": build,
                    "rules_version": INDUSTRY_RULES_VERSION,
                    "sde_signature": signature,
                },
            )
            record = IndustryDataQualification.objects.select_for_update().get(
                pk=record.pk
            )
            if (
                record.lease_token
                and record.next_check_at
                and record.next_check_at > now
            ):
                results[str(build)] = "checking"
                continue
            if not force and (
                record.status in READY_STATUSES
                or (record.next_check_at and record.next_check_at > now)
            ):
                results[str(build)] = record.status
                continue
            record.status = "checking"
            record.lease_token = token
            record.next_check_at = now + timedelta(seconds=LEASE_SECONDS)
            record.save(update_fields=["status", "lease_token", "next_check_at"])
        evidence, hashes, archive_hash, reason = {}, {}, "", ""
        try:
            with tempfile.TemporaryDirectory(prefix="indyhub-sde-check-") as temporary:
                path = (
                    Path(archive)
                    if archive is not None
                    else Path(temporary) / "official.zip"
                )
                if archive is None:
                    archive_hash = _download_archive(build, path)
                else:
                    if path.stat().st_size > MAX_ARCHIVE_BYTES:
                        raise IndustryRulesError(
                            "Official SDE archive exceeds the size limit"
                        )
                    with path.open("rb") as stream:
                        archive_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                evidence, hashes = _archive_evidence(path, build)
            status = evidence["status"]
        except (
            IndustryRulesError,
            requests.RequestException,
            OSError,
            ValueError,
            zipfile.BadZipFile,
            UnicodeError,
        ) as exc:
            status, reason = "error", str(exc)[:1000]
            logger.warning(
                "Industry qualification failed for build %s: %s", build, reason
            )
        if qualification_context(_get_industry_sde_sections())[0] != signature:
            status, evidence, reason = (
                "error",
                {},
                "Installed SDE changed during qualification; result discarded",
            )
        updated = IndustryDataQualification.objects.filter(
            pk=record.pk, lease_token=token
        ).update(
            status=status,
            evidence=evidence,
            evidence_hash=fingerprint(evidence),
            archive_sha256=archive_hash,
            entry_sha256=hashes,
            reason=reason,
            checked_at=timezone.now(),
            lease_token="",
            next_check_at=(
                timezone.now() + timedelta(seconds=RETRY_SECONDS)
                if status == "error"
                else None
            ),
        )
        results[str(build)] = status if updated else "superseded"
    return {"status": "checked", "builds": results}
