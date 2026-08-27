"""Switch platform for Aura Frames."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import POWER_STATE_FORCED_OFF
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
        """Return True when the frame display is not forced off."""
        return self.coordinator.power_state != POWER_STATE_FORCED_OFF

    async def async_turn_on(self, **kwargs) -> None:
        """Restore the saved display schedule."""
        await self.coordinator.async_turn_on()

    async def async_turn_off(self, **kwargs) -> None:
        """Force the frame off via schedule manipulation."""
        await self.coordinator.async_turn_off()
