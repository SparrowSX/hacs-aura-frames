"""Button platform for Aura Frames."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Set up Aura frame buttons."""
    coordinator: AuraFramesCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    async_add_entities(
        [
            AuraNextPhotoButton(coordinator),
            AuraPreviousPhotoButton(coordinator),
        ]
    )


class AuraNextPhotoButton(AuraFrameEntity, ButtonEntity):
    """Button to show the next photo."""

    _attr_translation_key = "next_photo"
    _attr_icon = "mdi:skip-next"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.async_next_photo()


class AuraPreviousPhotoButton(AuraFrameEntity, ButtonEntity):
    """Button to show the previous photo."""

    _attr_translation_key = "previous_photo"
    _attr_icon = "mdi:skip-previous"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.async_previous_photo()
