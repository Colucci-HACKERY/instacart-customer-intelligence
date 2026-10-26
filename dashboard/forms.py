import re

from django import forms


class CustomerSearchForm(forms.Form):
    user_id = forms.CharField(
        label="Customer ID",
        max_length=10,
        error_messages={
            "required": "Enter a Customer ID to search.",
            "max_length": "Customer ID must be a positive whole number up to 2,147,483,647.",
        },
        widget=forms.TextInput(attrs={
            "inputmode": "numeric",
            "placeholder": "Enter Customer ID",
            "autocomplete": "off",
            "aria-describedby": "search-help",
        }),
    )

    def clean_user_id(self):
        value = self.cleaned_data["user_id"]
        if not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= 2147483647:
            raise forms.ValidationError(
                "Customer ID must be a positive whole number up to 2,147,483,647."
            )
        return int(value)


class ProductExplorerForm(forms.Form):
    q = forms.CharField(label="Search products", required=False, max_length=100,
                        widget=forms.TextInput(attrs={"placeholder": "Search by product name or ID", "type": "search"}))
    department = forms.IntegerField(required=False, min_value=1, max_value=2147483647)
    aisle = forms.IntegerField(required=False, min_value=1, max_value=2147483647)
    sort = forms.ChoiceField(required=False, choices=[
        ("orders", "Most containing orders"),
        ("occurrences", "Most occurrences"),
        ("reorders", "Most reorder occurrences"),
        ("reorder_rate", "Highest reorder rate"),
        ("name", "Product name A-Z"),
    ])


class BasketPairForm(forms.Form):
    q = forms.CharField(label="Product in pair", required=False, max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "Product name or ID", "type": "search"}))
    department = forms.IntegerField(required=False, min_value=1, max_value=2147483647)
    aisle = forms.IntegerField(required=False, min_value=1, max_value=2147483647)
    min_support = forms.DecimalField(label="Minimum support (%)", min_value=0, max_value=100,
        max_digits=12, decimal_places=8, widget=forms.NumberInput(attrs={"step": "any"}))
    min_orders = forms.IntegerField(label="Minimum co-orders", min_value=1, max_value=2147483647)
    min_confidence = forms.DecimalField(label="Minimum confidence (%)", required=False,
        min_value=0, max_value=100, max_digits=8, decimal_places=4,
        widget=forms.NumberInput(attrs={"step": "any"}))
    sort = forms.ChoiceField(required=False, choices=[
        ("orders", "Most co-orders"), ("lift", "Highest lift"),
        ("confidence_a", "Highest confidence A to B"),
        ("confidence_b", "Highest confidence B to A"),
    ])


class RetentionFilterForm(forms.Form):
    user_id = forms.CharField(label="Customer ID", required=False, max_length=10,
        widget=forms.TextInput(attrs={"inputmode":"numeric", "placeholder":"Search exact Customer ID"}))
    segment = forms.CharField(required=False, max_length=50)
    comparison = forms.ChoiceField(required=False, choices=[
        ("", "All cadence records"), ("longer_recorded_gap", "Longer recorded latest gap"),
        ("shorter_recorded_gap", "Shorter recorded latest gap"),
        ("same_recorded_gap", "Same recorded latest gap"),
        ("capped_comparison", "Comparison limited by 30+ cap"),
        ("insufficient_history", "Insufficient comparison history"),
        ("latest_gap_unavailable", "Latest gap missing or invalid"),
        ("invalid_sequence", "Invalid source sequence"),
    ])
    sort = forms.ChoiceField(required=False, choices=[
        ("customer", "Customer ID"), ("orders", "Most observed orders"),
        ("mean", "Highest recorded mean gap"), ("latest", "Highest recorded latest gap"),
    ])

    def clean_user_id(self):
        value = self.cleaned_data["user_id"]
        if value and (not re.fullmatch(r"[0-9]+",value) or not 1 <= int(value) <= 2147483647):
            raise forms.ValidationError("Enter a positive whole-number Customer ID up to 2,147,483,647.")
        return int(value) if value else None
