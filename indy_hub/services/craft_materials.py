"""Helpers for craft material quantity calculations."""

# Standard Library
from math import ceil


def compute_job_material_quantity(
    base_quantity_per_run: int,
    runs: int,
    material_efficiency: int = 0,
    *,
    apply_material_efficiency: bool = True,
) -> int:
    """Return the total material quantity for a multi-run industry job.

    EVE industry applies material efficiency to the total job input, then rounds up.
    Rounding per run overstates totals for many materials when runs > 1.
    """

    base_quantity = int(base_quantity_per_run or 0)
    job_runs = int(runs or 0)
    if base_quantity <= 0 or job_runs <= 0:
        return 0

    total_quantity = base_quantity * job_runs
    if apply_material_efficiency:
        efficiency = max(0, min(int(material_efficiency or 0), 100))
        total_quantity = total_quantity * (100 - efficiency) / 100

    return int(ceil(total_quantity))


def is_base_item_material_efficiency_exempt(
    parent_product_meta_group_id: int | None,
    parent_product_category_id: int | None,
    material_meta_group_id: int | None,
    material_category_id: int | None,
) -> bool:
    """Return whether the material behaves like a T1 base item for an upgraded product.

    Typical example: a T2 ship job requiring the corresponding T1 hull.
    These inputs are not reduced by material-efficiency bonuses in-game.
    """

    if parent_product_meta_group_id is None or material_meta_group_id is None:
        return False
    if parent_product_category_id is None or material_category_id is None:
        return False

    return (
        int(parent_product_meta_group_id) > 1
        and int(material_meta_group_id) == 1
        and int(parent_product_category_id) == int(material_category_id)
    )
