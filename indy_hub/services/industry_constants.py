"""Shared industry charges and registry service vocabulary.

Charges are percentages of the job-cost base. Fraction aliases are derived here
for craft calculations; service flags remain Indy Hub's registry policy.
"""

# Standard Library
from decimal import Decimal

PERCENT_FACTOR = Decimal("100")
SCC_SURCHARGE_PERCENT = Decimal("4")
COPYING_JOB_COST_BASE_PERCENT = Decimal("2")
SCC_SURCHARGE_RATE = SCC_SURCHARGE_PERCENT / PERCENT_FACTOR
COPYING_JOB_COST_BASE_RATE = COPYING_JOB_COST_BASE_PERCENT / PERCENT_FACTOR
EVE_JOB_LAUNCH_WINDOW_SECONDS = 30 * 24 * 60 * 60


def get_craft_calculation_parameters() -> dict[str, int | float]:
    """Serialize shared scalar inputs with the existing craft payload."""
    return {
        "job_launch_window_seconds": EVE_JOB_LAUNCH_WINDOW_SECONDS,
        "scc_surcharge_percent": float(SCC_SURCHARGE_PERCENT),
    }


INDUSTRY_SERVICE_ACTIVITY_FLAGS = {
    "material efficiency research": "enable_research",
    "blueprint copying": "enable_research",
    "time efficiency research": "enable_research",
    "composite reactions": "enable_composite_reactions",
    "biochemical reactions": "enable_biochemical_reactions",
    "hybrid reactions": "enable_hybrid_reactions",
    "invention": "enable_invention",
    "manufacturing (standard)": "enable_manufacturing",
    "manufacturing (capitals)": "enable_manufacturing_capitals",
    "manufacturing (super capitals)": "enable_manufacturing_super_capitals",
}
DEFAULT_DISABLED_ACTIVITY_FLAGS = dict.fromkeys(
    INDUSTRY_SERVICE_ACTIVITY_FLAGS.values(), False
)
