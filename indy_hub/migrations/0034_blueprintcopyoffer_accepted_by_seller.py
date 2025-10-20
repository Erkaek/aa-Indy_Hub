from django.db import migrations, models


def mark_existing_offers(apps, schema_editor):
    Offer = apps.get_model('indy_hub', 'BlueprintCopyOffer')
    Offer.objects.filter(status='accepted').update(accepted_by_seller=True, accepted_by_buyer=True)


def unmark_existing_offers(apps, schema_editor):
    Offer = apps.get_model('indy_hub', 'BlueprintCopyOffer')
    Offer.objects.update(accepted_by_seller=False)


class Migration(migrations.Migration):
    dependencies = [
        ('indy_hub', '0033_blueprintcopychat'),
    ]

    operations = [
        migrations.AddField(
            model_name='blueprintcopyoffer',
            name='accepted_by_seller',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_existing_offers, unmark_existing_offers),
    ]
