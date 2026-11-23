"""Switch platform for Aura Frames."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AuraFramesConfigEntry
from .entity import AuraFrameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuraFramesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aura frame switches."""
    coordinator = entry.runtime_data
    async_add_entities([AuraFramePowerSwitch(coordinator)])


class AuraFramePowerSwitch(AuraFrameEntity, SwitchEntity):
    """Switch that controls frame power via the display schedule."""

    _attr_translation_key = "display"
    _attr_icon = "mdi:image-frame"

    @property
    def is_on(self) -> bool:
        """Return True when the display is meant to be lit.

        In manual mode this is the frame's state, held there by every poll.
        In the other two modes it says what the last press asked for: the
        frame runs its own schedule between presses, and whether that has it
        lit at this moment is not something Home Assistant can see.
        """
        return self.coordinator.display_wanted_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the sleep schedule the frame is actually running on."""
        return self.coordinator.display_schedule()

    async def async_turn_on(self, **kwargs) -> None:
        """Force the display on."""
        await self.coordinator.async_turn_on()

    async def async_turn_off(self, **kwargs) -> None:
        """Force the frame off via schedule manipulation."""
        await self.coordinator.async_turn_off()
