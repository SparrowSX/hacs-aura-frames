"""Binary sensor platform for Aura Frames."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AuraFramesConfigEntry
from .entity import AuraFrameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuraFramesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aura frame binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities([AuraFrameOnlineSensor(coordinator)])


class AuraFrameOnlineSensor(AuraFrameEntity, BinarySensorEntity):
    """Binary sensor for frame online status."""

    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool:
        """Return True if the frame is online."""
        return self.coordinator.is_online()
