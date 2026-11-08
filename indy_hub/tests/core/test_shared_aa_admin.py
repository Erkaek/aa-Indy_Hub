"""Exercise the real Alliance Auth admin and keep shared models under its control."""

# Standard Library
import importlib.util
from pathlib import Path
from unittest.mock import patch

# Third Party
from django_webtest import WebTest

# Django
from django.contrib import admin
from django.contrib.auth.models import AnonymousUser, Group, Permission, User
from django.test import RequestFactory
from django.urls import NoReverseMatch, reverse

# Alliance Auth
from allianceauth.authentication.admin import UserAdmin
from allianceauth.authentication.models import User as AuthUser
from allianceauth.groupmanagement.admin import GroupAdmin
from allianceauth.groupmanagement.models import Group as AuthGroup

# AA Example App
from indy_hub import admin as indy_admin
from indy_hub.models import Blueprint, MaterialExchangeSettings
from indy_hub.tests.test_smoke import assign_main_character


class SharedAllianceAuthAdminTests(WebTest):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.roles = {"anonymous": AnonymousUser()}
        for index, role in enumerate(
            ("member", "staff", "concrete", "proxy", "mixed", "superuser")
        ):
            user = User.objects.create_user(
                "shared-admin-" + role,
                is_staff=role != "member",
                is_superuser=role == "superuser",
            )
            assign_main_character(user, character_id=1999959100 + index)
            self.roles[role] = user
        for role, app_labels in (
            ("concrete", ("auth",)),
            ("proxy", ("authentication", "groupmanagement")),
            ("mixed", ("auth", "authentication", "groupmanagement")),
        ):
            self.roles[role].user_permissions.add(
                *Permission.objects.filter(
                    content_type__app_label__in=app_labels,
                    content_type__model__in=("user", "group"),
                )
            )
        self.target = self.roles["member"]
        self.indy_permissions = list(
            Permission.objects.filter(content_type__app_label="indy_hub")
            .filter(
                codename__in=(
                    "can_access_indy_hub",
                    "can_manage_corp_bp_requests",
                    "can_manage_material_hub",
                )
            )
            .order_by("codename")
        )
        self.group = Group.objects.create(name="Shared admin fixture")
        self.group.permissions.set(self.indy_permissions)
        self.target.groups.add(self.group)
        self.target.user_permissions.add(self.indy_permissions[0])

    def request_for(self, role):
        request = self.factory.get("/admin/")
        request.user = self.roles[role]
        return request

    def native(self, model):
        model_admin = admin.site.get_model_admin(model)
        obj = model_admin.get_object(
            self.request_for("superuser"),
            str(self.target.pk if model is AuthUser else self.group.pk),
        )
        return model_admin, obj

    def test_native_registration_forms_inlines_and_actions_are_preserved(self):
        self.assertFalse(admin.site.is_registered(User))
        self.assertFalse(admin.site.is_registered(Group))
        user_admin, user = self.native(AuthUser)
        group_admin, group = self.native(AuthGroup)
        self.assertIs(type(user_admin), UserAdmin)
        self.assertIs(type(group_admin), GroupAdmin)
        self.assertEqual(
            user_admin.form.__module__, "allianceauth.authentication.forms"
        )
        self.assertEqual(
            group_admin.form.__module__, "allianceauth.groupmanagement.forms"
        )
        request = self.request_for("superuser")
        self.assertIn("update_main_character_model", user_admin.get_actions(request))
        self.assertIn(
            "remove_users_not_matching_states", group_admin.get_actions(request)
        )
        self.assertTrue(user_admin.get_inline_instances(request, user))
        self.assertTrue(group_admin.get_inline_instances(request, group))

    def test_native_permission_methods_match_concrete_and_proxy_contracts(self):
        # AA deliberately uses concrete auth.* for user writes, but its group
        # admin uses groupmanagement.*. Proxy permissions are not interchangeable.
        expected = {
            AuthUser: {
                "anonymous": (False, False, False, False),
                "member": (False, False, False, False),
                "staff": (False, False, False, False),
                "concrete": (False, True, True, True),
                "proxy": (True, False, False, False),
                "mixed": (True, True, True, True),
                "superuser": (True, True, True, True),
            },
            AuthGroup: {
                role: (
                    (True,) * 4
                    if role in ("proxy", "mixed", "superuser")
                    else (False,) * 4
                )
                for role in self.roles
            },
        }
        for model, roles in expected.items():
            model_admin, obj = self.native(model)
            for role, permissions in roles.items():
                with self.subTest(model=model._meta.label, role=role):
                    request = self.request_for(role)
                    actual = (
                        model_admin.has_view_permission(request, obj),
                        model_admin.has_add_permission(request),
                        model_admin.has_change_permission(request, obj),
                        model_admin.has_delete_permission(request, obj),
                    )
                    self.assertEqual(actual, permissions)
                    readonly = model_admin.get_readonly_fields(request, obj)
                    sensitive = (
                        ("is_staff", "is_superuser", "user_permissions")
                        if model is AuthUser
                        else ("permissions",)
                    )
                    if role != "superuser":
                        for field in sensitive:
                            self.assertIn(field, readonly)

    def test_native_routes_enforce_staff_and_effective_model_permissions(self):
        for model in (AuthUser, AuthGroup):
            _, obj = self.native(model)
            prefix = (
                "admin:" + model._meta.app_label + "_" + model._meta.model_name + "_"
            )
            for role, user in self.roles.items():
                self.app.reset()
                for action in ("changelist", "add", "change", "delete", "history"):
                    with self.subTest(
                        model=model._meta.label, role=role, action=action
                    ):
                        url = reverse(
                            prefix + action,
                            args=(
                                [obj.pk]
                                if action in ("change", "delete", "history")
                                else []
                            ),
                        )
                        if role in ("anonymous", "member"):
                            status = 302
                        elif model is AuthUser:
                            allowed = role in ("concrete", "mixed", "superuser") or (
                                role == "proxy"
                                and action in ("changelist", "change", "history")
                            )
                            status = 200 if allowed else 403
                        else:
                            status = (
                                200 if role in ("proxy", "mixed", "superuser") else 403
                            )
                        self.app.get(
                            url,
                            user=None if role == "anonymous" else user,
                            status=status,
                        )

    def test_removed_concrete_routes_cannot_be_used_even_for_forged_posts(self):
        before = (self.target.is_staff, self.target.is_superuser)
        for model, pk in (("user", self.target.pk), ("group", self.group.pk)):
            for action in ("changelist", "add", "change", "delete", "history"):
                with self.assertRaises(NoReverseMatch):
                    reverse(
                        "admin:auth_" + model + "_" + action,
                        args=[pk] if action in ("change", "delete", "history") else [],
                    )
            for role, user in self.roles.items():
                self.app.reset()
                status = 302 if role in ("anonymous", "member") else 404
                for suffix in (
                    "",
                    "add/",
                    f"{pk}/change/",
                    f"{pk}/delete/",
                    f"{pk}/history/",
                ):
                    self.app.get(
                        f"/admin/auth/{model}/{suffix}",
                        user=None if role == "anonymous" else user,
                        status=status,
                    )
                self.client.logout()
                if role != "anonymous":
                    self.client.force_login(user)
                response = self.client.post(
                    f"/admin/auth/{model}/{pk}/change/",
                    {"is_superuser": "on", "permissions": self.indy_permissions[0].pk},
                )
                self.assertEqual(response.status_code, status)
        self.target.refresh_from_db()
        self.assertEqual((self.target.is_staff, self.target.is_superuser), before)

    def test_native_user_post_ignores_forged_sensitive_fields_but_saves_valid_edit(
        self,
    ):
        url = reverse("admin:authentication_user_change", args=[self.target.pk])
        actor = self.roles["concrete"]
        form = self.app.get(url, user=actor).forms["user_form"]
        for name in ("is_staff", "is_superuser", "user_permissions"):
            self.assertNotIn(name, form.fields)
        form["first_name"] = "Allowed native edit"
        before = list(self.target.user_permissions.values_list("pk", flat=True))
        params = form.submit_fields() + [
            ("_save", "Save"),
            ("is_staff", "on"),
            ("is_superuser", "on"),
            ("user_permissions", self.indy_permissions[-1].pk),
        ]
        self.app.post(url, params, user=actor, status=302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.first_name, "Allowed native edit")
        self.assertFalse(self.target.is_staff)
        self.assertFalse(self.target.is_superuser)
        self.assertEqual(
            list(self.target.user_permissions.values_list("pk", flat=True)), before
        )

    def test_native_group_post_preserves_readonly_permissions_and_restricted_flag(self):
        url = reverse("admin:groupmanagement_group_change", args=[self.group.pk])
        actor = self.roles["mixed"]
        form = self.app.get(url, user=actor).forms["group_form"]
        self.assertNotIn("permissions", form.fields)
        self.assertNotIn("authgroup-0-restricted", form.fields)
        form["name"] = "Allowed native group edit"
        before = set(self.group.permissions.values_list("pk", flat=True))
        elevated = Permission.objects.get(
            content_type__app_label="auth", codename="change_user"
        )
        params = form.submit_fields() + [
            ("_save", "Save"),
            ("permissions", elevated.pk),
            ("authgroup-0-restricted", "on"),
        ]
        self.app.post(url, params, user=actor, status=302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Allowed native group edit")
        self.assertEqual(
            set(self.group.permissions.values_list("pk", flat=True)), before
        )
        self.assertFalse(self.group.authgroup.restricted)

    def test_indy_grants_native_labels_and_business_guards_remain_effective(self):
        self.assertEqual(len(self.indy_permissions), 3)
        user_grants = set(self.target.user_permissions.values_list("pk", flat=True))
        group_grants = set(self.group.permissions.values_list("pk", flat=True))
        group_admin, group = self.native(AuthGroup)
        permission_field = group_admin.get_form(
            self.request_for("superuser"), group
        ).base_fields["permissions"]
        for permission in self.indy_permissions:
            self.assertEqual(
                permission_field.label_from_instance(permission), str(permission)
            )
            self.assertTrue(self.target.has_perm("indy_hub." + permission.codename))
        self.client.force_login(self.target)
        self.assertEqual(
            self.client.get(reverse("indy_hub:settings_hub")).status_code, 200
        )
        denied = self.client.get(reverse("indy_hub:settings_admin_users"))
        self.assertRedirects(
            denied, reverse("indy_hub:index"), fetch_redirect_response=False
        )
        self.assertEqual(
            self.client.get(
                reverse("indy_hub:settings_admin_users"), {"fragment": "1"}
            ).status_code,
            403,
        )
        toggle = reverse("indy_hub:material_exchange_toggle_active")
        self.assertEqual(self.client.post(toggle, {}).status_code, 302)
        self.assertFalse(MaterialExchangeSettings.get_solo().is_enabled)
        self.client.force_login(self.roles["concrete"])
        self.assertEqual(self.client.post(toggle, {"is_active": "on"}).status_code, 403)
        self.assertFalse(MaterialExchangeSettings.get_solo().is_enabled)
        self.assertEqual(
            set(self.target.user_permissions.values_list("pk", flat=True)), user_grants
        )
        self.assertEqual(
            set(self.group.permissions.values_list("pk", flat=True)), group_grants
        )

    def test_industrial_admin_still_reads_and_edits_its_own_models(self):
        self.assertTrue(admin.site.is_registered(Blueprint))
        self.assertTrue(admin.site.is_registered(indy_admin.MaterialExchangeConfig))
        blueprint = Blueprint.objects.create(
            owner_user=self.target,
            character_id=1999959100,
            item_id=1999959200,
            type_id=34,
            type_name="Admin fixture",
            location_id=60003760,
            location_flag="Hangar",
            quantity=-1,
            runs=0,
        )
        actor = self.roles["superuser"]
        self.app.get(
            reverse("admin:indy_hub_blueprint_changelist"), user=actor, status=200
        )
        url = reverse("admin:indy_hub_blueprint_change", args=[blueprint.pk])
        form = self.app.get(url, user=actor).forms["blueprint_form"]
        form["type_name"] = "Native industrial admin saved"
        form.submit("_save", user=actor, status=302)
        blueprint.refresh_from_db()
        self.assertEqual(blueprint.type_name, "Native industrial admin saved")
        self.assertEqual(blueprint.item_id, 1999959200)

    def test_host_owned_concrete_registrations_are_not_replaced_on_import(self):
        site = admin.AdminSite(name="host-owned-test")

        class HostUserAdmin(admin.ModelAdmin):
            readonly_fields = ("is_superuser",)

        class HostGroupAdmin(admin.ModelAdmin):
            readonly_fields = ("permissions",)

        site.register(User, HostUserAdmin)
        site.register(Group, HostGroupAdmin)
        user_admin = site.get_model_admin(User)
        group_admin = site.get_model_admin(Group)
        spec = importlib.util.spec_from_file_location(
            "indy_hub._host_admin_probe", Path(indy_admin.__file__)
        )
        module = importlib.util.module_from_spec(spec)
        with (
            patch("django.contrib.admin.site", site),
            patch("django.contrib.admin.sites.site", site),
        ):
            spec.loader.exec_module(module)
        self.assertIs(site.get_model_admin(User), user_admin)
        self.assertIs(site.get_model_admin(Group), group_admin)
        self.assertTrue(site.is_registered(Blueprint))
