from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0069_add_material_exchange_market_group_filters"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="cachedcorporationasset",
            table="indy_hub_corp_assets",
        ),
        migrations.AlterModelTable(
            name="cachedcharacterasset",
            table="indy_hub_char_assets",
        ),
    ]
