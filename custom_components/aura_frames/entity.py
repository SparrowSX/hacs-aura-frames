"""Base entity for Aura Frames."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AuraFramesCoordinator


class AuraFrameEntity(CoordinatorEntity[AuraFramesCoordinator]):
    """Base class for Aura frame entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AuraFramesCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        frame = coordinator.data or {}
        self._frame_id = coordinator.frame_id
        self._attr_unique_id = (
            f"{self._frame_id}_{self._attr_translation_key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._frame_id)},
            name=frame.get("name", "Aura Frame"),
            manufacturer="Aura",
            model=frame.get("display_aspect_ratio"),
            sw_version=frame.get("software_version"),
        )
