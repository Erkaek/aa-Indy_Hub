# Django
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0032_alter_blueprint_options_alter_blueprint_owner_kind_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlueprintCopyChat",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("is_open", models.BooleanField(default=True)),
                ("closed_reason", models.CharField(blank=True, max_length=32)),
                (
                    "closed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "last_message_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "buyer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bp_copy_chats_as_buyer",
                        to="auth.user",
                    ),
                ),
                (
                    "offer",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat",
                        to="indy_hub.blueprintcopyoffer",
                    ),
                ),
                (
                    "request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chats",
                        to="indy_hub.blueprintcopyrequest",
                    ),
                ),
                (
                    "seller",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bp_copy_chats_as_seller",
                        to="auth.user",
                    ),
                ),
            ],
            options={
                "default_permissions": (),
            },
        ),
        migrations.CreateModel(
            name="BlueprintCopyMessage",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "sender_role",
                    models.CharField(
                        choices=[
                            ("buyer", "Buyer"),
                            ("seller", "Builder"),
                            ("system", "System"),
                        ],
                        max_length=16,
                    ),
                ),
                ("content", models.TextField()),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "chat",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="indy_hub.blueprintcopychat",
                    ),
                ),
                (
                    "sender",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bp_copy_messages",
                        to="auth.user",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
                "default_permissions": (),
            },
        ),
        migrations.AddIndex(
            model_name="blueprintcopychat",
            index=models.Index(
                fields=["request_id", "is_open"],
                name="bp_copy_chat_state",
            ),
        ),
        migrations.AddIndex(
            model_name="blueprintcopychat",
            index=models.Index(
                fields=["buyer_id"],
                name="bp_copy_chat_buyer",
            ),
        ),
        migrations.AddIndex(
            model_name="blueprintcopychat",
            index=models.Index(
                fields=["seller_id"],
                name="bp_copy_chat_seller",
            ),
        ),
        migrations.AddIndex(
            model_name="blueprintcopymessage",
            index=models.Index(
                fields=["chat_id", "created_at"],
                name="bp_copy_msg_chat",
            ),
        ),
    ]
