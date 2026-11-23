"""Inspect or run the global industrial compatibility check."""

# Standard Library
import json

# Django
from django.core.management.base import BaseCommand, CommandError

# AA Example App
from indy_hub.models import IndustryDataQualification
from indy_hub.services.industry_qualification import (
    check_installed_industry_rules,
    qualification_context,
    qualification_key,
)
from indy_hub.services.industry_rules import IndustryRulesError
from indy_hub.services.industry_structures import (
    _get_industry_sde_sections,
    get_industry_sde_qualification,
)


class Command(BaseCommand):
    help = "Qualify installed industrial rules against the official CCP SDE archive."

    def add_arguments(self, parser):
        parser.add_argument(
            "--archive", help="Use an existing official JSONL ZIP without HTTP."
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Recheck completed/failed evidence; an active lease is retained.",
        )
        parser.add_argument(
            "--status",
            action="store_true",
            help="Show current evidence without downloading or scheduling work.",
        )

    def handle(self, *args, **options):
        try:
            checked = (
                {}
                if options["status"]
                else check_installed_industry_rules(
                    archive=options["archive"], force=options["force"]
                )
            )
            signature, builds = qualification_context(_get_industry_sde_sections())
            records = list(
                IndustryDataQualification.objects.filter(
                    qualification_key__in=[
                        qualification_key(build, signature) for build in builds
                    ]
                ).values(
                    "build_number",
                    "status",
                    "reason",
                    "checked_at",
                    "next_check_at",
                    "archive_sha256",
                    "evidence_hash",
                )
            )
            qualification = get_industry_sde_qualification()
            qualification.pop("extra_qualifications", None)
            result = {
                "check": checked,
                "qualification": qualification,
                "records": records,
            }
        except IndustryRulesError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, default=str, indent=2))
        if not options["status"] and qualification["status"] not in (
            "compatible_qualified",
            "compatible_conditional",
        ):
            raise CommandError(
                "Industrial compatibility is not established; see the diagnostics above."
            )
