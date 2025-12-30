# Generated migration for random order references
# Changes order_reference generation from sequential (INDY-{id}) to random (INDY-XXXXXXXXXX)

# Django
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        (
            "indy_hub",
            "0055_material_exchange_order_workflow_refactoring",
        ),
    ]

    operations = [
        # No database schema changes - this is a logic-only migration
        # order_reference generation now uses random 10-digit numbers instead of sequential IDs
        # Existing order references remain unchanged
        # New orders will receive references like INDY-1234567890
    ]
