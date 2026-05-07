from __future__ import annotations

# Django
from django.db import migrations

BP_TYPE_REACTION = "REACTION"
BP_TYPE_ORIGINAL = "ORIGINAL"
BP_TYPE_COPY = "COPY"


def classify_blueprint(
    *, quantity, runs, type_name, type_id, reaction_ids: set[int]
) -> str:
    try:
        normalized_type_id = int(type_id or 0)
    except (TypeError, ValueError):
        normalized_type_id = 0

    if normalized_type_id and normalized_type_id in reaction_ids:
        return BP_TYPE_REACTION

    name = (type_name or "").lower()
    if "formula" in name or "reaction" in name:
        return BP_TYPE_REACTION

    if quantity == -1 or runs == -1:
        return BP_TYPE_ORIGINAL
    if quantity == -2:
        return BP_TYPE_COPY
    if quantity and quantity > 0:
        return BP_TYPE_COPY

    return BP_TYPE_ORIGINAL


def repair_blueprint_bp_type(apps, schema_editor):
    Blueprint = apps.get_model("indy_hub", "Blueprint")

    try:
        EveIndustryActivityProduct = apps.get_model(
            "eveuniverse", "EveIndustryActivityProduct"
        )
    except LookupError:
        EveIndustryActivityProduct = None

    reaction_ids: set[int] = set()
    if EveIndustryActivityProduct is not None:
        reaction_ids = set(
            EveIndustryActivityProduct.objects.filter(
                activity_id__in=[9, 11]
            ).values_list("eve_type_id", flat=True)
        )

    to_update = []
    batch_size = 500
    queryset = Blueprint.objects.only(
        "id",
        "bp_type",
        "quantity",
        "runs",
        "type_name",
        "type_id",
    )
    for blueprint in queryset.iterator():
        desired_type = classify_blueprint(
            quantity=blueprint.quantity,
            runs=blueprint.runs,
            type_name=blueprint.type_name,
            type_id=blueprint.type_id,
            reaction_ids=reaction_ids,
        )
        if blueprint.bp_type != desired_type:
            blueprint.bp_type = desired_type
            to_update.append(blueprint)

        if len(to_update) >= batch_size:
            Blueprint.objects.bulk_update(to_update, ["bp_type"])
            to_update.clear()

    if to_update:
        Blueprint.objects.bulk_update(to_update, ["bp_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0099_align_index_names"),
    ]

    operations = [
        migrations.RunPython(repair_blueprint_bp_type, migrations.RunPython.noop),
    ]
