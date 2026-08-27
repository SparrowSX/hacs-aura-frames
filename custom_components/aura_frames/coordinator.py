"""DataUpdateCoordinator for Aura Frames."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuraApiError, AuraAuthError, AuraClient
from .const import (
    CONF_FRAME_ID,
    DOMAIN,
    POWER_STATE_FORCED_OFF,
    POWER_STATE_NORMAL,
    SCHEDULE_EPOCH_DATE,
    STORAGE_POWER_STATE,
    STORAGE_SAVED_SCHEDULE,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_SCHEDULE_TIME_RE = re.compile(r"(?:T|^)(\d{2}:\d{2}(?::\d{2})?)")

type AuraFramesConfigEntry = ConfigEntry["AuraFramesCoordinator"]


def frame_model(frame: dict[str, Any]) -> str | None:
    """Return what to show as the device model.

    The aspect ratio is the closest thing to a model the API is known to
    expose, so it stands in until a response carries a real model field.
    """
    return frame.get("model") or frame.get("display_aspect_ratio")


class AuraFramesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls frame state from the Aura API."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AuraFramesConfigEntry,
        client: AuraClient,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        # Entries created before the config flow normalized this may hold a
        # non-string id.
        self.frame_id: str = str(entry.data[CONF_FRAME_ID])
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
            always_update=False,
        )

    @property
    def power_state(self) -> str:
        """Return the current power override state."""
        return self.config_entry.data.get(STORAGE_POWER_STATE, POWER_STATE_NORMAL)

    @property
    def saved_schedule(self) -> dict[str, Any] | None:
        """Return saved schedule values when power is overridden."""
        return self.config_entry.data.get(STORAGE_SAVED_SCHEDULE)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch frame data from the API."""
        try:
            # Nothing on the polling path reads the schedule, so skip the
            # extra frame-list request its absence would otherwise trigger on
            # every cycle; async_turn_off and async_turn_on ask for it when
            # they need it. The first fetch still merges, because entities
            # take their device name and firmware from it once, at creation.
            frame = await self.client.get_frame(
                self.frame_id, ensure_schedule=self.data is None
            )
        except AuraAuthError as err:
            raise ConfigEntryAuthFailed("Aura credentials have expired") from err
        except AuraApiError as err:
            raise UpdateFailed(str(err)) from err

        self._async_sync_device(frame)
        return frame

    def _async_sync_device(self, frame: dict[str, Any]) -> None:
        """Carry renames and firmware updates into the device registry.

        Entities build their DeviceInfo once, when they are created, so a
        frame renamed in the Aura app would otherwise keep its old name until
        the entry is reloaded. Only fields the response actually carries are
        written: a poll that skips the frame-list merge may not include them
        at all, and absent must not mean cleared. A name the user set in Home
        Assistant lives in name_by_user and is not touched by this.
        """
        registry = dr.async_get(self.hass)
        # Looked up through the entry's own devices: async_get_device is
        # deprecated, and async_get_device_by_identifier only exists from
        # 2026.8 on. This helper works either side of that line.
        device = next(
            (
                candidate
                for candidate in dr.async_entries_for_config_entry(
                    registry, self.config_entry.entry_id
                )
                if (DOMAIN, self.frame_id) in candidate.identifiers
            ),
            None,
        )
        if device is None:
            return

        changes: dict[str, Any] = {}
        if (name := frame.get("name")) and name != device.name:
            changes["name"] = name
        if (version := frame.get("software_version")) and (
            version != device.sw_version
        ):
            changes["sw_version"] = version
        if (model := frame_model(frame)) and model != device.model:
            changes["model"] = model

        if changes:
            registry.async_update_device(device.id, **changes)

    async def async_next_photo(self) -> None:
        """Advance to the next photo."""
        await self.client.next_photo(self.frame_id)
        await self.async_request_refresh()

    async def async_previous_photo(self) -> None:
        """Go back to the previous photo."""
        await self.client.previous_photo(self.frame_id)
        await self.async_request_refresh()

    async def _persist_state(
        self,
        *,
        power_state: str,
        saved_schedule: dict[str, Any] | None,
    ) -> None:
        """Persist power override state on the config entry."""
        new_data = {
            **self.config_entry.data,
            STORAGE_POWER_STATE: power_state,
            STORAGE_SAVED_SCHEDULE: saved_schedule,
        }
        self.hass.config_entries.async_update_entry(
            self.config_entry, data=new_data
        )
        # The switch reads power_state from the entry, not from the polled
        # frame data. With always_update=False a refresh that happens to
        # return identical data notifies nobody, so the toggle would sit on
        # its old state until a later poll differed. Push it out here.
        self.async_update_listeners()

    async def async_turn_off(self) -> None:
        """Force the frame off by manipulating the display schedule."""
        if self.power_state == POWER_STATE_FORCED_OFF:
            return

        frame = await self.client.get_frame(self.frame_id)
        time_zone = frame.get("time_zone") or "Europe/Berlin"
        now = datetime.now(ZoneInfo(time_zone))
        time_str = now.strftime("%H:%M:%S")

        saved_schedule = {
            "scheduled_display_sleep": frame.get("scheduled_display_sleep", True),
            "scheduled_display_on_at": frame.get("scheduled_display_on_at"),
            "scheduled_display_off_at": frame.get("scheduled_display_off_at"),
        }
        scheduled_on_at = self._format_schedule_time(
            frame.get("scheduled_display_on_at")
        )
        if scheduled_on_at is None:
            raise AuraApiError(
                "Aura did not return a valid scheduled display start time; "
                "refusing to overwrite it (available fields: "
                f"{', '.join(sorted(frame.keys()))}; "
                "scheduled_display_on_at="
                f"{frame.get('scheduled_display_on_at')!r})"
            )

        await self.client.update_frame(
            self.frame_id,
            {
                "scheduled_display_sleep": True,
                "scheduled_display_on_at": scheduled_on_at,
                "scheduled_display_off_at": f"{SCHEDULE_EPOCH_DATE}T{time_str}.000Z",
                "user_set_time_zone": time_zone,
            },
        )
        await self._persist_state(
            power_state=POWER_STATE_FORCED_OFF,
            saved_schedule=saved_schedule,
        )
        await self.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Restore the original display schedule."""
        if self.power_state != POWER_STATE_FORCED_OFF:
            return

        saved = self.saved_schedule
        if not saved:
            raise RuntimeError("No saved schedule to restore")

        frame = await self.client.get_frame(self.frame_id)
        time_zone = frame.get("time_zone") or "Europe/Berlin"

        saved_on_at = saved.get("scheduled_display_on_at")
        saved_off_at = saved.get("scheduled_display_off_at")
        scheduled_on_at = self._format_schedule_time(saved_on_at)
        scheduled_off_at = self._format_schedule_time(saved_off_at)

        # A saved value of None means the frame had no such time, so restoring
        # None is faithful. A start time that is present but unreadable is a
        # different matter: sending null would clear the time the display turns
        # on and could leave the frame dark for good. Refuse before the write,
        # so _persist_state is never reached and the saved values survive for a
        # second attempt.
        if saved_on_at is not None and scheduled_on_at is None:
            raise AuraApiError(
                "Refusing to restore the Aura display schedule: the saved "
                "start time is unreadable, and writing null would clear it "
                f"(scheduled_display_on_at={saved_on_at!r}). The saved "
                "schedule is kept so switching on can be retried."
            )

        # An unreadable off time is not worth refusing over. The frame is off
        # right now precisely because turn_off moved that value, so bailing out
        # would strand it in the dark; restoring without one keeps the display
        # on and only costs the sleep time.
        if saved_off_at is not None and scheduled_off_at is None:
            _LOGGER.warning(
                "Saved Aura display off time %r is unreadable; turning the "
                "frame back on without a scheduled off time",
                saved_off_at,
            )

        await self.client.update_frame(
            self.frame_id,
            {
                "scheduled_display_sleep": saved.get(
                    "scheduled_display_sleep", True
                ),
                "scheduled_display_on_at": scheduled_on_at,
                "scheduled_display_off_at": scheduled_off_at,
                "user_set_time_zone": time_zone,
            },
        )
        await self._persist_state(
            power_state=POWER_STATE_NORMAL,
            saved_schedule=None,
        )
        await self.async_request_refresh()

    def is_online(self) -> bool:
        """Return whether the frame appears online."""
        env = self.data.get("frame_environment") or {}
        last_online = env.get("last_online_at")
        if not last_online:
            return False

        try:
            online_at = datetime.fromisoformat(last_online.replace("Z", "+00:00"))
        except ValueError:
            return False

        age = datetime.now(timezone.utc) - online_at
        return age.total_seconds() < UPDATE_INTERVAL_SECONDS * 3

    def current_asset(self) -> dict[str, Any] | None:
        """Return the currently displayed asset, if known."""
        impression = self.data.get("last_impression") or {}
        asset = impression.get("asset")
        if isinstance(asset, dict):
            return asset
        return None

    @staticmethod
    def _format_schedule_time(value: Any) -> str | None:
        """Return an Aura schedule value using its required epoch date.

        Aura treats these as wall-clock times, despite the trailing ``Z``. Keep
        the time portion returned by the frame and always use the epoch date
        accepted by the mobile application.
        """
        if not isinstance(value, str):
            return None
        if match := _SCHEDULE_TIME_RE.search(value):
            time_value = match.group(1)
            if len(time_value) == 5:
                time_value = f"{time_value}:00"
            return f"{SCHEDULE_EPOCH_DATE}T{time_value}.000Z"
        return None
