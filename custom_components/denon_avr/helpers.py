"""Naming and option helpers shared by the entity platforms.

These functions turn the profile control specs and the device published metadata
into the display names and option lists the entities need. Names always prefer
what the receiver published; a humanised id is only a last resort.
"""

from __future__ import annotations

from .avr.models import Discovery
from .avr.profile import ControlSpec


def humanize(text: str) -> str:
    """Turn an id such as 'dynamic_eq' into a readable 'Dynamic Eq'."""

    return text.replace("_", " ").replace(":", "").strip().title()


def control_name(discovery: Discovery, spec: ControlSpec) -> str:
    """Return the display name for a control, preferring the device title."""

    if spec.feature and spec.feature in discovery.feature_names:
        return discovery.feature_names[spec.feature]
    # Some controls carry a name in the profile (data, not code); use it next.
    profile_name = spec.get("name")
    if profile_name:
        return str(profile_name)
    return humanize(spec.id)


def enum_options(discovery: Discovery, spec: ControlSpec) -> tuple[
    list[str], dict[str, str], dict[str, str]
]:
    """Build the option list and label/value maps for an enum control.

    The wire values come from the profile in canonical order; the display labels
    come from the device in the same order. When the counts differ (a model with
    a different option set) the wire tokens are used as labels so a mismatch can
    never send a wrong command.
    """

    values = spec.values
    labels = discovery.option_labels.get(spec.feature or "", [])
    if labels and len(labels) == len(values):
        pairs = list(zip(labels, values))
    else:
        pairs = [(humanize(value), value) for value in values]
    options = [label for label, _ in pairs]
    label_to_value = {label: value for label, value in pairs}
    value_to_label = {value: label for label, value in pairs}
    return options, label_to_value, value_to_label
