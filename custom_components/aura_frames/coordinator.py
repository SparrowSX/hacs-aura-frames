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
    MAX_BRIDGED_POLL_FAILURES,
    ONLINE_GRACE_SECONDS,
    POWER_STATE_FORCED_OFF,
    POWER_STATE_FORCED_ON,
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
        self._bridged_failures = 0
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
            # Hand back the last reading rather than drop everything to
            # unavailable over one failed request. The host's DNS fails every
            # so often, and each failure cost a full interval in which every
            # entity was gone and Home Assistant discarded any service call
            # aimed at it -- so a scheduled switch-on could silently not
            # happen. The heartbeat in that stale reading keeps ageing, so
            # the online sensor still reports the frame as offline once the
            # outage outlasts its grace window.
            if self.data is not None and (
                self._bridged_failures < MAX_BRIDGED_POLL_FAILURES
            ):
                self._bridged_failures += 1
                _LOGGER.debug(
                    "Aura poll %s of at most %s failed, keeping the last "
                    "reading: %s",
                    self._bridged_failures,
                    MAX_BRIDGED_POLL_FAILURES,
                    err,
                )
                return self.data
            raise UpdateFailed(str(err)) from err

        self._bridged_failures = 0
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("Aura frame settings: %s", self._settings_of(frame))
        self._async_sync_device(frame)
        return frame

    @staticmethod
    def _settings_of(frame: dict[str, Any]) -> dict[str, Any]:
        """Return the frame's own settings, for a debug line.

        What makes the display light up is decided by fields of the frame
        record, and the record carries more of them than this integration
        knows about -- the Aura app offers an automatic mode that no
        documented field obviously maps to. Printing the plain settings on
        request is how that gets answered by measurement instead of guessing.

        Only scalars are kept: the nested parts are photo and environment
        data, and anything that looks like a credential, an address or a
        link is left out, since a log gets shared.
        """
        hidden = ("token", "secret", "password", "email", "url", "key", "id")
        return {
            name: value
            for name, value in sorted(frame.items())
            if isinstance(value, (bool, int, float, str, type(None)))
            and not any(word in name.lower() for word in hidden)
        }

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

    def _schedule_of(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Return the schedule fields of a frame record.

        This is what gets remembered the first time the switch takes the
        frame over, so that both directions have the user's own times to
        write back afterwards.
        """
        return {
            "scheduled_display_sleep": frame.get("scheduled_display_sleep", True),
            "scheduled_display_on_at": frame.get("scheduled_display_on_at"),
            "scheduled_display_off_at": frame.get("scheduled_display_off_at"),
        }

    def _remembered_time(self, saved: dict[str, Any], field: str) -> str | None:
        """Return a remembered schedule time ready to be written back.

        None means there is nothing to write: either the frame had no such
        time, or the value cannot be read. Both are left out of the payload
        rather than sent as null, which would clear the user's own time.
        """
        value = saved.get(field)
        formatted = self._format_schedule_time(value)
        if formatted is None and value is not None:
            _LOGGER.warning(
                "Aura schedule value %s=%r is unreadable; leaving the time "
                "the frame has in place instead of clearing it",
                field,
                value,
            )
        return formatted

    @staticmethod
    def _schedule_write(
        *,
        sleep: bool,
        time_zone: str,
        on_at: str | None,
        off_at: str | None,
    ) -> dict[str, Any]:
        """Return an update payload that carries only readable times.

        The PUT is a partial update, so a field left out keeps whatever the
        frame has. Only a field that is sent as null is cleared, and clearing
        a display schedule is never what the switch is for.
        """
        payload: dict[str, Any] = {
            "scheduled_display_sleep": sleep,
            "user_set_time_zone": time_zone,
        }
        if on_at is not None:
            payload["scheduled_display_on_at"] = on_at
        if off_at is not None:
            payload["scheduled_display_off_at"] = off_at
        return payload

    async def async_turn_off(self) -> None:
        """Force the frame off by moving the scheduled off time to now.

        A press writes even when the frame is already down as forced off,
        for the same reason the other direction does: what this integration
        noted down last time is not proof of what the frame is doing now.
        """
        frame = await self.client.get_frame(self.frame_id)
        time_zone = frame.get("time_zone") or "Europe/Berlin"
        now = datetime.now(ZoneInfo(time_zone))
        saved_schedule = self.saved_schedule or self._schedule_of(frame)

        await self.client.update_frame(
            self.frame_id,
            self._schedule_write(
                sleep=True,
                time_zone=time_zone,
                on_at=self._remembered_time(
                    saved_schedule, "scheduled_display_on_at"
                ),
                off_at=f"{SCHEDULE_EPOCH_DATE}T{now:%H:%M:%S}.000Z",
            ),
        )
        await self._persist_state(
            power_state=POWER_STATE_FORCED_OFF,
            saved_schedule=saved_schedule,
        )
        await self.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Force the display on and park the frame's own sleep schedule.

        Writing the remembered schedule back is not enough to light the
        display: the frame only shows photos inside that window, so a
        restore once the scheduled off time has passed -- an evening
        switch-on, which is the usual case -- leaves the frame dark while
        Home Assistant reports the switch as on. Switching the scheduled
        sleep off is what the schedule toggle in the Aura app does, and it
        takes effect whatever the time is. The remembered times are written
        alongside it, so the off time this integration moved is repaired and
        the user's own schedule is there to be switched back on in the app.

        A press repeats that write even when Home Assistant already has the
        frame down as forced on. The display can go back to sleep behind our
        back -- the Aura app, the cloud and the frame's own ambient light
        sensor all reach it too -- and refusing to write because of what was
        noted down last time leaves the switch stuck: on in Home Assistant,
        dark on the wall, and no press able to insist. One redundant request
        is the cheaper mistake.
        """
        frame = await self.client.get_frame(self.frame_id)
        time_zone = frame.get("time_zone") or "Europe/Berlin"
        saved_schedule = self.saved_schedule or self._schedule_of(frame)

        await self.client.update_frame(
            self.frame_id,
            self._schedule_write(
                sleep=False,
                time_zone=time_zone,
                on_at=self._remembered_time(
                    saved_schedule, "scheduled_display_on_at"
                ),
                off_at=self._remembered_time(
                    saved_schedule, "scheduled_display_off_at"
                ),
            ),
        )
        await self._persist_state(
            power_state=POWER_STATE_FORCED_ON,
            saved_schedule=saved_schedule,
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
        return age.total_seconds() < ONLINE_GRACE_SECONDS

    def current_asset(self) -> dict[str, Any] | None:
        """Return the currently displayed asset, if known."""
        impression = self.data.get("last_impression") or {}
        asset = impression.get("asset")
        if isinstance(asset, dict):
            return asset
        return None

    def display_schedule(self) -> dict[str, Any]:
        """Return the frame's own sleep schedule in readable form.

        These three values decide whether the display is lit, and nothing in
        Home Assistant used to show them: a frame that stayed dark could
        only be diagnosed by reading the log. They come from the frame on
        every poll, so the switch can carry them as attributes.
        """
        frame = self.data or {}
        return {
            "scheduled_sleep": frame.get("scheduled_display_sleep"),
            "scheduled_on_at": self._wall_clock(
                frame.get("scheduled_display_on_at")
            ),
            "scheduled_off_at": self._wall_clock(
                frame.get("scheduled_display_off_at")
            ),
            "frame_time_zone": frame.get("time_zone"),
            "power_state": self.power_state,
        }

    @staticmethod
    def _wall_clock(value: Any) -> str | None:
        """Return the wall-clock time out of an Aura schedule value.

        The frame sends these dated 1969-12-31 and suffixed with a ``Z`` it
        does not mean; only the time of day carries meaning.
        """
        if not isinstance(value, str):
            return None
        if match := _SCHEDULE_TIME_RE.search(value):
            return match.group(1)
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
