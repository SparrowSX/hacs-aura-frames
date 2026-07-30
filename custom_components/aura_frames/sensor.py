"""Sensor platform for Aura Frames."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import AuraFramesCoordinator
from .entity import AuraFrameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aura frame sensors."""
    coordinator: AuraFramesCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    async_add_entities(
        [
            AuraCurrentPhotoSensor(coordinator),
            AuraNumAssetsSensor(coordinator),
            AuraWifiSensor(coordinator),
        ]
    )


class AuraCurrentPhotoSensor(AuraFrameEntity, SensorEntity):
    """Sensor showing the currently displayed photo."""

    _attr_translation_key = "current_photo"
    _attr_icon = "mdi:image"

    @property
    def native_value(self) -> str | None:
        """Return the current photo file name."""
        asset = self.coordinator.current_asset()
        if not asset:
            return None
        return asset.get("original_file_name") or asset.get("file_name")


class AuraNumAssetsSensor(AuraFrameEntity, SensorEntity):
    """Sensor showing the number of photos on the frame."""

    _attr_translation_key = "num_assets"
    _attr_icon = "mdi:image-multiple"

    @property
    def native_value(self) -> int | None:
        """Return the number of assets."""
        value = self.coordinator.data.get("num_assets")
        return int(value) if value is not None else None


class AuraWifiSensor(AuraFrameEntity, SensorEntity):
    """Sensor showing the Wi-Fi network name."""

    _attr_translation_key = "wifi_network"
    _attr_icon = "mdi:wifi"

    @property
    def native_value(self) -> str | None:
        """Return the Wi-Fi network name."""
        return self.coordinator.data.get("wifi_network")
