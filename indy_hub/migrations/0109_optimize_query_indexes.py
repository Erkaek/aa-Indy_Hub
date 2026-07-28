# Django
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0108_remove_character_online_status"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="blueprintcopyrequest",
            index=models.Index(
                fields=["requested_by", "fulfilled", "delivered"],
                name="indy_copy_req_user_deliv",
            ),
        ),
        migrations.AddIndex(
            model_name="blueprintcopyrequest",
            index=models.Index(
                fields=[
                    "type_id",
                    "material_efficiency",
                    "time_efficiency",
                    "fulfilled",
                    "delivered",
                ],
                name="indy_copy_req_key_state",
            ),
        ),
        migrations.AddIndex(
            model_name="materialexchangebuyorder",
            index=models.Index(
                fields=["buyer", "status", "-created_at"],
                name="indy_me_buy_user_state_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="materialexchangebuyorder",
            index=models.Index(
                fields=["config", "status", "-created_at"],
                name="indy_me_buy_cfg_state_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="materialexchangesellorder",
            index=models.Index(
                fields=["seller", "status", "-created_at"],
                name="indy_me_sell_user_state_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="materialexchangesellorder",
            index=models.Index(
                fields=["config", "status", "-created_at"],
                name="indy_me_sell_cfg_state_idx",
            ),
        ),
    ]
