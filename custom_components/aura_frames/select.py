"""Select platform for Aura Frames."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AuraFramesConfigEntry
from .entity import AuraFrameEntity

MODE_AUTOMATIC = "automatic"
MODE_SCHEDULE = "schedule"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuraFramesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aura frame selects."""
    coordinator = entry.runtime_data
    async_add_entities([AuraFrameDisplayModeSelect(coordinator)])


class AuraFrameDisplayModeSelect(AuraFrameEntity, SelectEntity):
    """Select for how the frame decides when to light its display.

    The Aura app offers this as a single choice, and the frame holds it in
    the field this integration long mistook for a power switch: automatic is
    the frame's own light sensor deciding, and a schedule is two times. The
    switch can only work in the latter, so having the mode in Home Assistant
    is what makes "the frame went dark by itself" explainable -- and lets an
    automation hand the frame back to its sensor when nobody wants to be in
    charge of it.
    """

    _attr_translation_key = "display_mode"
    _attr_icon = "mdi:theme-light-dark"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [MODE_AUTOMATIC, MODE_SCHEDULE]

    @property
    def current_option(self) -> str | None:
        """Return the mode the frame is in, or None while it is unknown."""
        sleep = (self.coordinator.data or {}).get("scheduled_display_sleep")
        if sleep is None:
            return None
        return MODE_SCHEDULE if sleep else MODE_AUTOMATIC

    async def async_select_option(self, option: str) -> None:
        """Put the frame into the chosen mode."""
        await self.coordinator.async_set_display_mode(option == MODE_SCHEDULE)
