"""Select platform for Aura Frames."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DISPLAY_MODE_AUTOMATIC,
    DISPLAY_MODE_MANUAL,
    DISPLAY_MODE_SCHEDULE,
)
from .coordinator import AuraFramesConfigEntry
from .entity import AuraFrameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuraFramesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aura frame selects."""
    coordinator = entry.runtime_data
    async_add_entities([AuraFrameDisplayModeSelect(coordinator)])


class AuraFrameDisplayModeSelect(AuraFrameEntity, SelectEntity):
    """Select for who decides when the frame's display is lit.

    - *Automatic* is the frame's own ambient light sensor. It comes on in
      the morning and goes dark in the evening by itself, and the schedule
      is ignored. This is what the Aura app offers as its automatic setting.
    - *Schedule* is the wanted times. The frame runs them on its own, and
      the switch forces one edge when it is pressed.
    - *Manual* is the switch. The frame is still told a schedule, because
      that is the only language it has, but the window written is one that
      keeps the display where the switch says -- and every poll holds it
      there, whatever the Aura app or the frame has done meanwhile.

    The first two leave the frame alone between presses, which is why an
    evening of photos could end at the frame's old sleep time. Manual is for
    not wanting a schedule at all.
    """

    _attr_translation_key = "display_mode"
    _attr_icon = "mdi:theme-light-dark"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [
        DISPLAY_MODE_AUTOMATIC,
        DISPLAY_MODE_SCHEDULE,
        DISPLAY_MODE_MANUAL,
    ]

    @property
    def current_option(self) -> str | None:
        """Return the mode the display is being run in."""
        return self.coordinator.display_mode

    async def async_select_option(self, option: str) -> None:
        """Put the frame into the chosen mode."""
        await self.coordinator.async_set_display_mode(option)
