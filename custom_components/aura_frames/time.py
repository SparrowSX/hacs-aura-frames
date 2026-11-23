"""Time platform for Aura Frames."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AuraFramesConfigEntry
from .entity import AuraFrameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AuraFramesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the wanted schedule times."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            AuraFrameWantedOnTime(coordinator),
            AuraFrameWantedOffTime(coordinator),
        ]
    )


class AuraFrameWantedTime(AuraFrameEntity, TimeEntity):
    """One edge of the schedule the user wants the frame to run.

    The frame's own record cannot be read as the intent: the switch
    overwrites one of its times whenever it forces the display, and the Aura
    app writes it too. So the wanted schedule lives in Home Assistant, where
    it can be read back and written again -- which is what turned a schedule
    quietly walking out of shape into something with a value to compare
    against.

    Nothing holds the frame to it continuously. A difference cannot be told
    apart from a deliberate change, so the schedule is sent when it is
    changed here, and again on request.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def _wanted(self) -> str | None:
        """Return the wanted wall clock this entity stands for."""
        raise NotImplementedError

    @property
    def native_value(self) -> time | None:
        """Return the wanted time, or None while none has been taken up."""
        clock = self._wanted()
        if clock is None:
            return None
        try:
            return time.fromisoformat(clock)
        except ValueError:
            return None


class AuraFrameWantedOnTime(AuraFrameWantedTime):
    """The time the frame should wake up."""

    _attr_translation_key = "wanted_on"
    _attr_icon = "mdi:weather-sunset-up"

    def _wanted(self) -> str | None:
        return self.coordinator.wanted_on

    async def async_set_value(self, value: time) -> None:
        """Store the wake time and send the schedule to the frame."""
        await self.coordinator.async_set_wanted_time(on_at=f"{value:%H:%M:%S}")


class AuraFrameWantedOffTime(AuraFrameWantedTime):
    """The time the frame should go to sleep."""

    _attr_translation_key = "wanted_off"
    _attr_icon = "mdi:weather-sunset-down"

    def _wanted(self) -> str | None:
        return self.coordinator.wanted_off

    async def async_set_value(self, value: time) -> None:
        """Store the sleep time and send the schedule to the frame."""
        await self.coordinator.async_set_wanted_time(off_at=f"{value:%H:%M:%S}")

