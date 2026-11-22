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
    SCHEDULE_NEXT_DAY_DATE,
    STORAGE_POWER_STATE,
    STORAGE_SAVED_SCHEDULE,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_SCHEDULE_TIME_RE = re.compile(r"(?:T|^)(\d{2}:\d{2}(?::\d{2})?)")

# The two edges of the frame's sleep window: it is dark from the off time
# until the on time.
SCHEDULE_FIELD_OFF = "scheduled_display_off_at"
SCHEDULE_FIELD_ON = "scheduled_display_on_at"

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

    def _own_time(
        self, frame: dict[str, Any], field: str, remembered: dict[str, Any]
    ) -> str | None:
        """Return the user's own time for one edge of the sleep window.

        The edge this integration is currently holding is in the config
        entry, because the frame only carries the forced value -- reading it
        back from the frame is what used to walk the schedule forward until
        it stopped making sense. Every other edge is read from the frame, so
        a change made in the Aura app meanwhile is picked up rather than
        overwritten.

        None means there is nothing to write: either the frame has no such
        time, or the value cannot be read. Both are left out of the payload
        rather than sent as null, which would clear the user's own time.
        """
        value = remembered[field] if field in remembered else frame.get(field)
        clock = self._wall_clock(value)
        if clock is None and value is not None:
            _LOGGER.warning(
                "Aura schedule value %s=%r is unreadable; leaving the time "
                "the frame has in place instead of clearing it",
                field,
                value,
            )
        return clock

    @staticmethod
    def _schedule_write(
        *,
        sleep: bool,
        time_zone: str,
        on_at: str | None,
        off_at: str | None,
    ) -> dict[str, Any]:
        """Return an update payload for a sleep window given as wall clocks.

        The two times are dated here, and the date is not decoration: the
        frame sleeps from the off time until the on time, so an on time that
        is not after the off time belongs to the following day. Writing both
        on the same date, as this integration used to, asks for a window that
        runs backwards.

        The PUT is a partial update, so a field left out keeps whatever the
        frame has. Only a field that is sent as null is cleared, and clearing
        a display schedule is never what the switch is for.
        """
        payload: dict[str, Any] = {
            "scheduled_display_sleep": sleep,
            "user_set_time_zone": time_zone,
        }
        if off_at is not None:
            payload["scheduled_display_off_at"] = (
                f"{SCHEDULE_EPOCH_DATE}T{off_at}.000Z"
            )
        if on_at is not None:
            crosses_midnight = off_at is not None and on_at <= off_at
            date = (
                SCHEDULE_NEXT_DAY_DATE if crosses_midnight else SCHEDULE_EPOCH_DATE
            )
            payload["scheduled_display_on_at"] = f"{date}T{on_at}.000Z"
        return payload

    async def async_turn_off(self) -> None:
        """Force the frame off by moving the scheduled off time to now."""
        await self._async_move_edge(
            moved=SCHEDULE_FIELD_OFF,
            kept=SCHEDULE_FIELD_ON,
            power_state=POWER_STATE_FORCED_OFF,
        )

    async def async_turn_on(self) -> None:
        """Force the display on by moving the scheduled on time to now."""
        await self._async_move_edge(
            moved=SCHEDULE_FIELD_ON,
            kept=SCHEDULE_FIELD_OFF,
            power_state=POWER_STATE_FORCED_ON,
        )

    async def _async_move_edge(
        self, *, moved: str, kept: str, power_state: str
    ) -> None:
        """Move one edge of the sleep window to now and keep the other.

        Both directions work the same way, mirrored. Off moves the time the
        frame falls asleep to now; on moves the time it wakes up to now. The
        other edge stays the user's: the frame keeps falling asleep at their
        bedtime after a switch-on, and keeps waking at their morning time
        after a switch-off.

        Switching the frame's scheduled sleep off is not what this does, even
        though it also lights an evening frame: that field is the mode switch
        the Aura app shows as the automatic setting, where the frame's own
        light sensor decides -- which is exactly how a frame switched on in
        Home Assistant went dark again at dusk by itself. Forcing the display
        means holding the schedule, so the schedule is what gets written.

        A press writes even when Home Assistant already has the frame in the
        state being asked for. The display can be put back to sleep behind our
        back -- the Aura app, the cloud and the frame's own light sensor all
        reach it too -- and refusing to write because of what was noted down
        last time leaves the switch unable to insist: on in Home Assistant,
        dark on the wall. One redundant request is the cheaper mistake.
        """
        frame = await self.client.get_frame(self.frame_id)
        time_zone = frame.get("time_zone") or "Europe/Berlin"
        now = f"{datetime.now(ZoneInfo(time_zone)):%H:%M:%S}"

        remembered = dict(self.saved_schedule or {})
        # The edge being kept is handed back, so the opposite press undoes
        # this one; the edge being overwritten is noted down in its place.
        # Exactly one of the two is held at any time, which is why a value
        # in the entry can no longer go stale behind a schedule the user
        # changes in the Aura app.
        kept_clock = self._own_time(frame, kept, remembered)
        remembered.pop(kept, None)
        remembered.setdefault(moved, frame.get(moved))

        times = {moved: now, kept: kept_clock}
        await self.client.update_frame(
            self.frame_id,
            self._schedule_write(
                sleep=True,
                time_zone=time_zone,
                off_at=times[SCHEDULE_FIELD_OFF],
                on_at=times[SCHEDULE_FIELD_ON],
            ),
        )
        await self._persist_state(
            power_state=power_state,
            saved_schedule=remembered or None,
        )
        await self.async_request_refresh()

    async def async_set_display_mode(self, scheduled: bool) -> None:
        """Switch between the frame's automatic mode and its schedule.

        Automatic is the frame deciding for itself by ambient light; the
        schedule is the two times the switch works with. Handing the frame
        back to automatic also writes the user's own times back, so that no
        edge this integration moved is left behind in a schedule nobody
        looks at any more.
        """
        if scheduled:
            await self.client.update_frame(
                self.frame_id, {"scheduled_display_sleep": True}
            )
            await self.async_request_refresh()
            return

        frame = await self.client.get_frame(self.frame_id)
        time_zone = frame.get("time_zone") or "Europe/Berlin"
        remembered = dict(self.saved_schedule or {})
        await self.client.update_frame(
            self.frame_id,
            self._schedule_write(
                sleep=False,
                time_zone=time_zone,
                off_at=self._own_time(frame, SCHEDULE_FIELD_OFF, remembered),
                on_at=self._own_time(frame, SCHEDULE_FIELD_ON, remembered),
            ),
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

        The frame sends these with an epoch date and a ``Z`` it does not
        mean. The date says which day of the window the time belongs to and
        is recomputed when a schedule is written, so only the time of day is
        carried over.
        """
        if not isinstance(value, str):
            return None
        if match := _SCHEDULE_TIME_RE.search(value):
            clock = match.group(1)
            return f"{clock}:00" if len(clock) == 5 else clock
        return None
