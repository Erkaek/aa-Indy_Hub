"""Forms for industry structure registry management."""

from __future__ import annotations

# Standard Library
import re
from decimal import Decimal

# Django
from django import forms
from django.forms import BaseFormSet

# AA Example App
from indy_hub.models import IndustryStructure
from indy_hub.services.industry_structures import (
    get_default_enabled_structure_activities,
    get_grouped_industry_rig_options,
    get_structure_type_catalog_entry,
    get_structure_type_options,
    is_rig_compatible_with_structure_type,
    resolve_item_type_reference,
    resolve_solar_system_location_reference,
    resolve_solar_system_reference,
    sde_item_types_loaded,
    structure_type_supports_rigs,
)


class IndustryStructureRegistryForm(forms.ModelForm):
    @staticmethod
    def _normalize_registry_name(value: str | None) -> str:
        return re.sub(r"\s+", "", str(value or "")).casefold()

    class Meta:
        model = IndustryStructure
        fields = [
            "name",
            "structure_type_id",
            "solar_system_name",
            "enable_manufacturing",
            "enable_manufacturing_capitals",
            "enable_manufacturing_super_capitals",
            "enable_research",
            "enable_invention",
            "enable_biochemical_reactions",
            "enable_hybrid_reactions",
            "enable_composite_reactions",
            "manufacturing_tax_percent",
            "manufacturing_capitals_tax_percent",
            "manufacturing_super_capitals_tax_percent",
            "research_tax_percent",
            "invention_tax_percent",
            "biochemical_reactions_tax_percent",
            "hybrid_reactions_tax_percent",
            "composite_reactions_tax_percent",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        activity_flag_fields = [
            field_name for field_name, _label in IndustryStructure.ACTIVITY_FIELD_LABELS
        ]
        tax_field_names = [
            field_name for field_name, _label in IndustryStructure.TAX_FIELD_LABELS
        ]
        for field_name in self.fields:
            widget_class = (
                "form-check-input"
                if field_name in activity_flag_fields
                else "form-control"
            )
            self.fields[field_name].widget.attrs.update({"class": widget_class})
        self.fields["structure_type_id"].widget = forms.Select(
            choices=[("", "---------")] + get_structure_type_options(),
            attrs={"class": "form-select"},
        )
        self.fields["name"].widget.attrs.update({"placeholder": "Raitaru Prime"})
        self.fields["structure_type_id"].required = True
        self.fields["solar_system_name"].widget.attrs.update(
            {
                "placeholder": "Jita",
                "autocomplete": "off",
                "list": "solar-system-suggestions",
            }
        )
        self.fields["solar_system_name"].required = True
        tax_placeholders = {
            "manufacturing_tax_percent": "0.00",
            "manufacturing_capitals_tax_percent": "0.00",
            "manufacturing_super_capitals_tax_percent": "0.00",
            "research_tax_percent": "0.00",
            "invention_tax_percent": "0.00",
            "biochemical_reactions_tax_percent": "0.00",
            "hybrid_reactions_tax_percent": "0.00",
            "composite_reactions_tax_percent": "0.00",
        }
        for field_name, placeholder in tax_placeholders.items():
            self.fields[field_name].required = False
            self.fields[field_name].decimal_places = 2
            self.fields[field_name].initial = Decimal("0.00")
            self.fields[field_name].widget.attrs.update(
                {"placeholder": placeholder, "step": "0.01"}
            )

        if not self.is_bound:
            for field_name in tax_field_names:
                current_value = getattr(self.instance, field_name, None)
                if current_value in {None, ""}:
                    self.initial[field_name] = Decimal("0.00")
                else:
                    self.initial[field_name] = Decimal(str(current_value)).quantize(
                        Decimal("0.01")
                    )

        structure_type_id = None
        if self.is_bound:
            structure_type_id = self.data.get(self.add_prefix("structure_type_id"))
        elif self.instance and self.instance.pk:
            structure_type_id = self.instance.structure_type_id

        try:
            resolved_structure_type_id = (
                int(structure_type_id) if structure_type_id not in {None, ""} else None
            )
        except (TypeError, ValueError):
            resolved_structure_type_id = None

        defaults = get_default_enabled_structure_activities(resolved_structure_type_id)
        for field_name in activity_flag_fields:
            if not self.is_bound:
                self.fields[field_name].initial = defaults[field_name]

    def _legacy_checkbox_is_checked(self, field_name: str) -> bool:
        raw_value = self.data.get(self.add_prefix(field_name))
        return str(raw_value).lower() not in {"", "0", "false", "none"}

    def _legacy_decimal_value(self, field_name: str) -> Decimal | None:
        raw_value = self.data.get(self.add_prefix(field_name))
        if raw_value in {None, ""}:
            return None
        try:
            return Decimal(str(raw_value))
        except Exception:
            return None

    def clean(self):
        cleaned_data = super().clean()
        if not sde_item_types_loaded():
            raise forms.ValidationError(
                "eve_sde ItemType is empty. Run esde_load_sde before using the structure registry."
            )

        cleaned_data["name"] = str(cleaned_data.get("name") or "").strip()
        structure_type_id = cleaned_data.get("structure_type_id")
        reference = resolve_item_type_reference(
            item_type_id=structure_type_id,
        )
        if reference is None:
            self.add_error(
                "structure_type_id",
                "Unknown structure type in eve_sde.",
            )
            return cleaned_data

        resolved_type_id, resolved_type_name = reference
        solar_system_name = (cleaned_data.get("solar_system_name") or "").strip()
        solar_system_reference = resolve_solar_system_reference(
            solar_system_name=solar_system_name or None,
        )
        if solar_system_reference is None:
            self.add_error("solar_system_name", "Unknown solar system in eve_sde.")
            return cleaned_data

        resolved_solar_system_id, resolved_solar_system_name, resolved_security_band = (
            solar_system_reference
        )
        solar_system_location_reference = resolve_solar_system_location_reference(
            solar_system_id=resolved_solar_system_id,
        )

        if not cleaned_data.get("enable_research") and any(
            self._legacy_checkbox_is_checked(field_name)
            for field_name in (
                "enable_te_research",
                "enable_me_research",
                "enable_copying",
            )
        ):
            cleaned_data["enable_research"] = True

        if not any(
            cleaned_data.get(field_name)
            for field_name in IndustryStructure.REACTION_ACTIVITY_FIELDS
        ):
            legacy_reactions_enabled = self._legacy_checkbox_is_checked(
                "enable_reactions"
            )
            if legacy_reactions_enabled:
                for field_name in IndustryStructure.REACTION_ACTIVITY_FIELDS:
                    cleaned_data[field_name] = True

        if cleaned_data.get("research_tax_percent") in {None, ""}:
            legacy_research_taxes = [
                self._legacy_decimal_value(field_name)
                for field_name in (
                    "te_research_tax_percent",
                    "me_research_tax_percent",
                    "copying_tax_percent",
                )
            ]
            legacy_research_taxes = [
                value for value in legacy_research_taxes if value is not None
            ]
            if legacy_research_taxes:
                cleaned_data["research_tax_percent"] = max(legacy_research_taxes)

        legacy_reactions_tax = self._legacy_decimal_value("reactions_tax_percent")
        if legacy_reactions_tax is not None:
            for field_name in (
                "biochemical_reactions_tax_percent",
                "hybrid_reactions_tax_percent",
                "composite_reactions_tax_percent",
            ):
                if cleaned_data.get(field_name) in {None, ""}:
                    cleaned_data[field_name] = legacy_reactions_tax

        for field_name, _label in IndustryStructure.TAX_FIELD_LABELS:
            if cleaned_data.get(field_name) in {None, ""}:
                cleaned_data[field_name] = Decimal("0")

        activity_flag_fields = [
            field_name for field_name, _label in IndustryStructure.ACTIVITY_FIELD_LABELS
        ]
        if not any(cleaned_data.get(field_name) for field_name in activity_flag_fields):
            raise forms.ValidationError(
                "Enable at least one activity for this structure."
            )

        structure_entry = get_structure_type_catalog_entry(int(resolved_type_id))
        rig_size = (
            None
            if structure_entry is None
            else int(structure_entry.get("rig_size") or 0)
        )
        if (
            cleaned_data.get("enable_manufacturing_capitals")
            and rig_size is not None
            and rig_size < 3
        ):
            self.add_error(
                "enable_manufacturing_capitals",
                "This structure type does not support capital manufacturing.",
            )
        if (
            cleaned_data.get("enable_manufacturing_super_capitals")
            and rig_size is not None
            and rig_size < 4
        ):
            self.add_error(
                "enable_manufacturing_super_capitals",
                "This structure type does not support super-capital manufacturing.",
            )

        cleaned_data["structure_type_id"] = resolved_type_id
        cleaned_data["structure_type_name"] = resolved_type_name
        cleaned_data["solar_system_id"] = resolved_solar_system_id
        cleaned_data["solar_system_name"] = resolved_solar_system_name
        cleaned_data["constellation_id"] = (
            None
            if solar_system_location_reference is None
            else solar_system_location_reference["constellation_id"]
        )
        cleaned_data["constellation_name"] = (
            ""
            if solar_system_location_reference is None
            else solar_system_location_reference["constellation_name"]
        )
        cleaned_data["region_id"] = (
            None
            if solar_system_location_reference is None
            else solar_system_location_reference["region_id"]
        )
        cleaned_data["region_name"] = (
            ""
            if solar_system_location_reference is None
            else solar_system_location_reference["region_name"]
        )
        cleaned_data["system_security_band"] = resolved_security_band

        normalized_name = self._normalize_registry_name(cleaned_data["name"])
        public_name_conflict = IndustryStructure.objects.filter(
            visibility_scope=IndustryStructure.VisibilityScope.PUBLIC,
        )
        if self.instance and self.instance.pk:
            public_name_conflict = public_name_conflict.exclude(pk=self.instance.pk)
        if any(
            self._normalize_registry_name(existing_name) == normalized_name
            for existing_name in public_name_conflict.values_list("name", flat=True)
        ):
            self.add_error(
                "name",
                "A shared structure with this registry name already exists, even when whitespace is ignored.",
            )
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.structure_type_name = self.cleaned_data["structure_type_name"]
        instance.solar_system_id = self.cleaned_data["solar_system_id"]
        instance.solar_system_name = self.cleaned_data["solar_system_name"]
        instance.constellation_id = self.cleaned_data["constellation_id"]
        instance.constellation_name = self.cleaned_data["constellation_name"]
        instance.region_id = self.cleaned_data["region_id"]
        instance.region_name = self.cleaned_data["region_name"]
        instance.system_security_band = self.cleaned_data["system_security_band"]
        if commit:
            instance.save()
        return instance


class IndustryStructureTaxProfileDuplicateForm(forms.ModelForm):
    personal_tag = forms.CharField(max_length=80)

    class Meta:
        model = IndustryStructure
        fields = [
            "personal_tag",
            "manufacturing_tax_percent",
            "manufacturing_capitals_tax_percent",
            "manufacturing_super_capitals_tax_percent",
            "research_tax_percent",
            "invention_tax_percent",
            "biochemical_reactions_tax_percent",
            "hybrid_reactions_tax_percent",
            "composite_reactions_tax_percent",
        ]

    def __init__(self, *args, owner_user=None, suggested_personal_tag=None, **kwargs):
        self.owner_user = owner_user
        self.suggested_personal_tag = suggested_personal_tag
        super().__init__(*args, **kwargs)
        self.fields["personal_tag"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": "structures",
            }
        )
        if not self.is_bound:
            self.initial["personal_tag"] = (
                self.instance.personal_tag
                or self.suggested_personal_tag
                or getattr(self.owner_user, "username", "")
            )
        tax_placeholders = {
            "manufacturing_tax_percent": "0.00",
            "manufacturing_capitals_tax_percent": "0.00",
            "manufacturing_super_capitals_tax_percent": "0.00",
            "research_tax_percent": "0.00",
            "invention_tax_percent": "0.00",
            "biochemical_reactions_tax_percent": "0.00",
            "hybrid_reactions_tax_percent": "0.00",
            "composite_reactions_tax_percent": "0.00",
        }
        for field_name, placeholder in tax_placeholders.items():
            self.fields[field_name].required = False
            self.fields[field_name].decimal_places = 2
            self.fields[field_name].widget.attrs.update(
                {
                    "class": "form-control",
                    "placeholder": placeholder,
                    "step": "0.01",
                }
            )
            if not self.is_bound:
                current_value = getattr(self.instance, field_name, None)
                if current_value in {None, ""}:
                    self.initial[field_name] = Decimal("0.00")
                else:
                    self.initial[field_name] = Decimal(str(current_value)).quantize(
                        Decimal("0.01")
                    )

    def clean_personal_tag(self) -> str:
        personal_tag = str(self.cleaned_data.get("personal_tag") or "").strip()
        if not personal_tag:
            raise forms.ValidationError("Enter a personal tag for this private copy.")
        return personal_tag

    def clean(self):
        cleaned_data = super().clean()
        for field_name, _label in IndustryStructure.TAX_FIELD_LABELS:
            if cleaned_data.get(field_name) in {None, ""}:
                cleaned_data[field_name] = getattr(
                    self.instance,
                    field_name,
                    Decimal("0"),
                ) or Decimal("0")

        personal_tag = str(cleaned_data.get("personal_tag") or "").strip()
        if personal_tag and self.owner_user is not None:
            name = getattr(self.instance, "name", "")
            personal_conflict = IndustryStructure.objects.filter(
                visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
                owner_user=self.owner_user,
                name=name,
                personal_tag__iexact=personal_tag,
            )
            if self.instance and self.instance.pk and self.instance.is_personal_copy():
                personal_conflict = personal_conflict.exclude(pk=self.instance.pk)
            if personal_conflict.exists():
                self.add_error(
                    "personal_tag",
                    "You already have a personal copy with this tag for this structure.",
                )
        return cleaned_data


class IndustryStructureBulkTaxUpdateForm(forms.Form):
    SOURCE_SCOPE_ALL = "all"
    SOURCE_SCOPE_SYNCED = "synced"
    SOURCE_SCOPE_MANUAL = "manual"

    source_scope = forms.ChoiceField(
        choices=[
            (SOURCE_SCOPE_ALL, "All structures"),
            (SOURCE_SCOPE_SYNCED, "Only synced structures"),
            (SOURCE_SCOPE_MANUAL, "Only manual structures"),
        ],
        initial=SOURCE_SCOPE_ALL,
        required=False,
    )
    solar_system_name = forms.ChoiceField(required=False)
    constellation_name = forms.ChoiceField(required=False)
    region_name = forms.ChoiceField(required=False)
    system_security_band = forms.ChoiceField(required=False)
    structure_type_id = forms.ChoiceField(required=False)
    owner_corporation_id = forms.ChoiceField(required=False)
    only_when_zero = forms.BooleanField(required=False, initial=True)
    confirm_apply = forms.BooleanField(
        required=False, initial=False, widget=forms.HiddenInput()
    )
    manufacturing_tax_percent = forms.DecimalField(
        required=False, min_value=0, max_value=100, decimal_places=3, max_digits=6
    )
    manufacturing_capitals_tax_percent = forms.DecimalField(
        required=False, min_value=0, max_value=100, decimal_places=3, max_digits=6
    )
    manufacturing_super_capitals_tax_percent = forms.DecimalField(
        required=False, min_value=0, max_value=100, decimal_places=3, max_digits=6
    )
    research_tax_percent = forms.DecimalField(
        required=False, min_value=0, max_value=100, decimal_places=3, max_digits=6
    )
    invention_tax_percent = forms.DecimalField(
        required=False, min_value=0, max_value=100, decimal_places=3, max_digits=6
    )
    biochemical_reactions_tax_percent = forms.DecimalField(
        required=False, min_value=0, max_value=100, decimal_places=3, max_digits=6
    )
    hybrid_reactions_tax_percent = forms.DecimalField(
        required=False, min_value=0, max_value=100, decimal_places=3, max_digits=6
    )
    composite_reactions_tax_percent = forms.DecimalField(
        required=False, min_value=0, max_value=100, decimal_places=3, max_digits=6
    )

    tax_field_names = [
        "manufacturing_tax_percent",
        "manufacturing_capitals_tax_percent",
        "manufacturing_super_capitals_tax_percent",
        "research_tax_percent",
        "invention_tax_percent",
        "biochemical_reactions_tax_percent",
        "hybrid_reactions_tax_percent",
        "composite_reactions_tax_percent",
    ]

    def __init__(
        self,
        *args,
        corporation_choices=None,
        solar_system_choices=None,
        constellation_choices=None,
        region_choices=None,
        enforce_tax_selection=True,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.enforce_tax_selection = bool(enforce_tax_selection)
        self.fields["source_scope"].widget.attrs.update({"class": "form-select"})
        self.fields["solar_system_name"].choices = [
            ("", "All solar systems"),
            *[
                (str(value), str(label))
                for value, label in (solar_system_choices or [])
            ],
        ]
        self.fields["solar_system_name"].widget.attrs.update({"class": "form-select"})
        self.fields["constellation_name"].choices = [
            ("", "All constellations"),
            *[
                (str(value), str(label))
                for value, label in (constellation_choices or [])
            ],
        ]
        self.fields["constellation_name"].widget.attrs.update({"class": "form-select"})
        self.fields["region_name"].choices = [
            ("", "All regions"),
            *[(str(value), str(label)) for value, label in (region_choices or [])],
        ]
        self.fields["region_name"].widget.attrs.update({"class": "form-select"})
        self.fields["system_security_band"].choices = [
            ("", "All security bands"),
            *IndustryStructure.SecurityBand.choices,
        ]
        self.fields["system_security_band"].widget.attrs.update(
            {"class": "form-select"}
        )
        self.fields["structure_type_id"].choices = [
            ("", "All structure types"),
            *[(str(type_id), label) for type_id, label in get_structure_type_options()],
        ]
        self.fields["structure_type_id"].widget.attrs.update({"class": "form-select"})
        self.fields["owner_corporation_id"].choices = [
            ("", "All corporations"),
            *[(str(corp_id), label) for corp_id, label in (corporation_choices or [])],
        ]
        self.fields["owner_corporation_id"].widget.attrs.update(
            {"class": "form-select"}
        )
        self.fields["only_when_zero"].widget.attrs.update({"class": "form-check-input"})

        for field_name in self.tax_field_names:
            self.fields[field_name].widget.attrs.update(
                {
                    "class": "form-control",
                    "placeholder": "Leave blank to keep current values",
                }
            )

    def has_any_tax_updates(self, cleaned_data=None) -> bool:
        data = (
            cleaned_data
            if cleaned_data is not None
            else getattr(self, "cleaned_data", {})
        )
        return any(
            data.get(field_name) is not None for field_name in self.tax_field_names
        )

    def clean(self):
        cleaned_data = super().clean()
        if self.enforce_tax_selection and not self.has_any_tax_updates(cleaned_data):
            raise forms.ValidationError("Set at least one tax value to apply.")
        return cleaned_data

    def get_tax_updates(self) -> dict[str, Decimal]:
        return {
            field_name: self.cleaned_data[field_name]
            for field_name in self.tax_field_names
            if self.cleaned_data.get(field_name) is not None
        }


class IndustryStructureBulkImportForm(forms.Form):
    raw_text = forms.CharField(
        widget=forms.Textarea,
        help_text="Paste the copied /indy/ structure listing here.",
    )
    update_existing_manual = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["raw_text"].widget.attrs.update(
            {
                "class": "form-control",
                "rows": 14,
                "placeholder": "Paste the copied /indy/ listing here...",
                "spellcheck": "false",
            }
        )
        self.fields["update_existing_manual"].widget.attrs.update(
            {"class": "form-check-input"}
        )

    def clean_raw_text(self):
        raw_text = str(self.cleaned_data.get("raw_text") or "").strip()
        if not raw_text:
            raise forms.ValidationError("Paste at least one structure block to import.")
        return raw_text


class IndustryStructureRigForm(forms.Form):
    slot_index = forms.IntegerField(min_value=1, max_value=3, required=False)
    rig_type_id = forms.IntegerField(required=False)

    def __init__(self, *args, **kwargs):
        self.structure_type_id = kwargs.pop("structure_type_id", None)
        super().__init__(*args, **kwargs)
        prefix = self.prefix or ""
        index = None
        if "-" in prefix:
            try:
                index = int(prefix.rsplit("-", 1)[-1]) + 1
            except ValueError:
                index = None
        if index is not None and not self.is_bound:
            self.initial.setdefault("slot_index", index)
        self.fields["slot_index"].widget = forms.HiddenInput()
        self.fields["rig_type_id"].widget = forms.Select(
            choices=[
                ("", "No rig"),
                *get_grouped_industry_rig_options(self.structure_type_id),
            ],
            attrs={"class": "form-select"},
        )

    def clean(self):
        cleaned_data = super().clean()
        slot_index = cleaned_data.get("slot_index")
        rig_type_id = cleaned_data.get("rig_type_id")
        has_data = bool(rig_type_id)
        if not has_data:
            cleaned_data["is_empty"] = True
            return cleaned_data

        cleaned_data["is_empty"] = False
        if slot_index is None:
            self.add_error("slot_index", "Rig slot is required.")

        reference = resolve_item_type_reference(
            item_type_id=rig_type_id,
        )
        if reference is None:
            self.add_error("rig_type_id", "Unknown rig type in eve_sde.")
            return cleaned_data

        resolved_type_id, resolved_type_name = reference
        cleaned_data["rig_type_id"] = resolved_type_id
        cleaned_data["rig_type_name"] = resolved_type_name
        return cleaned_data


class IndustryStructureRigBaseFormSet(BaseFormSet):
    def __init__(self, *args, **kwargs):
        self.structure_type_id = kwargs.pop("structure_type_id", None)
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["structure_type_id"] = self.structure_type_id
        return kwargs

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        structure_type_id = self.structure_type_id
        if not structure_type_id and self.is_bound:
            structure_type_id = self.data.get("structure_type_id")
        try:
            resolved_structure_type_id = (
                int(structure_type_id) if structure_type_id else None
            )
        except (TypeError, ValueError):
            resolved_structure_type_id = None

        if resolved_structure_type_id and not structure_type_supports_rigs(
            resolved_structure_type_id
        ):
            for form in self.forms:
                cleaned_data = getattr(form, "cleaned_data", None) or {}
                if not cleaned_data:
                    continue
                cleaned_data["is_empty"] = True
                cleaned_data["rig_type_id"] = None
                cleaned_data["rig_type_name"] = ""
            return

        for form in self.forms:
            cleaned_data = getattr(form, "cleaned_data", None) or {}
            if not cleaned_data or cleaned_data.get("is_empty"):
                continue
            rig_type_id = cleaned_data.get("rig_type_id")
            if not rig_type_id or not resolved_structure_type_id:
                continue
            if is_rig_compatible_with_structure_type(
                rig_type_id=int(rig_type_id),
                structure_type_id=resolved_structure_type_id,
            ):
                continue
            form.add_error(
                "rig_type_id",
                "This rig cannot be fitted on the selected structure type.",
            )


IndustryStructureRigFormSet = forms.formset_factory(
    IndustryStructureRigForm,
    formset=IndustryStructureRigBaseFormSet,
    extra=0,
    max_num=3,
    validate_max=True,
)
