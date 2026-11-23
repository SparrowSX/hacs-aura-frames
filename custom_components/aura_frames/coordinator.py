"""DataUpdateCoordinator for Aura Frames."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuraApiError, AuraAuthError, AuraClient
from .const import (
    CONF_FRAME_ID,
    DISPLAY_MODE_AUTOMATIC,
    DISPLAY_MODE_MANUAL,
    DISPLAY_MODE_SCHEDULE,
    DOMAIN,
    MANUAL_WINDOW_END,
    MANUAL_WINDOW_START,
    MAX_BRIDGED_POLL_FAILURES,
    ONLINE_GRACE_SECONDS,
    POWER_STATE_FORCED_OFF,
    POWER_STATE_FORCED_ON,
    POWER_STATE_NORMAL,
    SCHEDULE_EPOCH_DATE,
    SCHEDULE_NEXT_DAY_DATE,
    STORAGE_DISPLAY_MODE,
    STORAGE_POWER_STATE,
    STORAGE_WANTED_OFF,
    STORAGE_WANTED_ON,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_SCHEDULE_TIME_RE = re.compile(r"(?:T|^)(\d{2}:\d{2}(?::\d{2})?)")

# The two edges of the frame's sleep window: it is dark from the off time
# until the on time.
SCHEDULE_FIELD_OFF = "scheduled_display_off_at"
SCHEDULE_FIELD_ON = "scheduled_display_on_at"

type AuraFramesConfigEntry = ConfigEntry["AuraFramesCoordinator"]


def wall_clock(value: Any) -> str | None:
    """Return the wall-clock time out of an Aura schedule value.

    The frame sends these with an epoch date and a ``Z`` it does not mean.
    The date says which day of the sleep window the time belongs to and is
    recomputed whenever a schedule is written, so only the time of day is
    carried over. None means the value cannot be read as a time.
    """
    if not isinstance(value, str):
        return None
    if match := _SCHEDULE_TIME_RE.search(value):
        clock = match.group(1)
        return f"{clock}:00" if len(clock) == 5 else clock
    return None


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
    def wanted_mode(self) -> str:
        """Return the mode the user picked, which may not be in force yet."""
        return self.config_entry.data.get(
            STORAGE_DISPLAY_MODE, DISPLAY_MODE_SCHEDULE
        )

    @property
    def display_mode(self) -> str:
        """Return how the display is being run, as far as can be seen.

        Manual is Home Assistant's own doing, so what was picked is what it
        is. The other two live in the frame's record, and nothing holds the
        frame to them -- the Aura app may have changed the mode since -- so
        there the record wins over what was picked here.
        """
        if self.wanted_mode == DISPLAY_MODE_MANUAL:
            return DISPLAY_MODE_MANUAL
        sleep = (self.data or {}).get("scheduled_display_sleep")
        if sleep is None:
            return self.wanted_mode
        return DISPLAY_MODE_SCHEDULE if sleep else DISPLAY_MODE_AUTOMATIC

    @property
    def display_wanted_on(self) -> bool:
        """Return whether the display is meant to be lit.

        A frame the switch has never touched counts as wanted on: whether
        its own schedule has it lit at this moment is not something Home
        Assistant can see, so there is nothing to force.
        """
        return self.power_state != POWER_STATE_FORCED_OFF

    @staticmethod
    def _manual_times(wanted_on: bool) -> dict[str, str]:
        """Return the sleep window that holds the display on, or off."""
        if wanted_on:
            return {
                "off_at": MANUAL_WINDOW_START,
                "on_at": MANUAL_WINDOW_END,
            }
        return {"off_at": MANUAL_WINDOW_END, "on_at": MANUAL_WINDOW_START}

    @property
    def wanted_on(self) -> str | None:
        """Return the wall clock the user wants the frame to wake at."""
        return self.config_entry.data.get(STORAGE_WANTED_ON)

    @property
    def wanted_off(self) -> str | None:
        """Return the wall clock the user wants the frame to sleep at."""
        return self.config_entry.data.get(STORAGE_WANTED_OFF)

    def _wanted_times(self, frame: dict[str, Any]) -> dict[str, str | None]:
        """Return the schedule to write, as wall clocks.

        The wanted times are the authority. Only a wanted time that has
        never been set falls back to what the frame carries, which is what
        happens once, on the first poll after an upgrade, before the frame's
        own pair is adopted.
        """
        return {
            "on_at": self.wanted_on or wall_clock(frame.get(SCHEDULE_FIELD_ON)),
            "off_at": self.wanted_off or wall_clock(frame.get(SCHEDULE_FIELD_OFF)),
        }

    @callback
    def _adopt_wanted_times(self, frame: dict[str, Any]) -> None:
        """Take the frame's own times as the wanted schedule, once.

        An entry from before the schedule was configurable has no wanted
        times, and neither does a new one. Whatever the frame is running is
        the only guess available, and it is the right one for a frame the
        switch has not touched. From then on the wanted times are the
        user's, changed in Home Assistant rather than read back.
        """
        adopted: dict[str, str] = {}
        if self.wanted_on is None and (
            clock := wall_clock(frame.get(SCHEDULE_FIELD_ON))
        ):
            adopted[STORAGE_WANTED_ON] = clock
        if self.wanted_off is None and (
            clock := wall_clock(frame.get(SCHEDULE_FIELD_OFF))
        ):
            adopted[STORAGE_WANTED_OFF] = clock
        if adopted:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **adopted},
            )

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
        self._adopt_wanted_times(frame)
        if (held := await self._async_hold_the_manual_state(frame)) is not None:
            frame = {**frame, **held}
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

    @callback
    def _persist(self, **values: Any) -> None:
        """Persist entry-held state and tell the entities about it."""
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, **values}
        )
        # The switch reads power_state from the entry, not from the polled
        # frame data, and the time entities read the wanted schedule from
        # there too. With always_update=False a refresh that happens to
        # return identical data notifies nobody, so an entity would sit on
        # its old state until a later poll differed. Push it out here.
        self.async_update_listeners()

    @staticmethod
    def _schedule_write(
        *,
        sleep: bool | None,
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
        a display schedule is never what the switch is for. A sleep of None
        leaves the mode alone, for a write that is about the times only.
        """
        payload: dict[str, Any] = {"user_set_time_zone": time_zone}
        if sleep is not None:
            payload["scheduled_display_sleep"] = sleep
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
        """Take the display down, by whichever means the mode calls for."""
        if self.wanted_mode == DISPLAY_MODE_MANUAL:
            await self._async_write_manual(wanted_on=False)
            return
        await self._async_move_edge(
            moved="off_at", power_state=POWER_STATE_FORCED_OFF
        )

    async def async_turn_on(self) -> None:
        """Light the display, by whichever means the mode calls for."""
        if self.wanted_mode == DISPLAY_MODE_MANUAL:
            await self._async_write_manual(wanted_on=True)
            return
        await self._async_move_edge(
            moved="on_at", power_state=POWER_STATE_FORCED_ON
        )

    async def _async_write_manual(self, *, wanted_on: bool) -> None:
        """Write the sleep window that holds the display where it is wanted.

        In manual mode the schedule is not a schedule any more: it is how a
        frame with no power API is told to stay lit, or stay dark. Moving an
        edge to now would not do -- the other edge would come round again,
        which is what took the display down in the middle of an evening.
        """
        frame = await self.client.get_frame(self.frame_id)
        time_zone = frame.get("time_zone") or "Europe/Berlin"
        await self.client.update_frame(
            self.frame_id,
            self._schedule_write(
                sleep=True,
                time_zone=time_zone,
                **self._manual_times(wanted_on),
            ),
        )
        self._persist(
            **{
                STORAGE_POWER_STATE: (
                    POWER_STATE_FORCED_ON if wanted_on else POWER_STATE_FORCED_OFF
                )
            }
        )
        await self.async_request_refresh()

    async def _async_hold_the_manual_state(
        self, frame: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Write the manual window back when the frame has left it.

        This is the one place where a difference between the frame and what
        Home Assistant wants can be read as drift rather than intent: manual
        mode means the user has said the schedule is not theirs to keep. So
        here, and only here, a poll corrects the frame -- which also covers
        the Aura app, the Aura cloud and the frame's own sensor reaching it.

        Nothing is written while the frame already matches, so a frame left
        alone costs one comparison per poll.
        """
        if self.wanted_mode != DISPLAY_MODE_MANUAL:
            return None

        times = self._manual_times(self.display_wanted_on)
        if (
            frame.get("scheduled_display_sleep") is True
            and wall_clock(frame.get(SCHEDULE_FIELD_OFF)) == times["off_at"]
            and wall_clock(frame.get(SCHEDULE_FIELD_ON)) == times["on_at"]
        ):
            return None

        time_zone = frame.get("time_zone") or "Europe/Berlin"
        payload = self._schedule_write(
            sleep=True, time_zone=time_zone, **times
        )
        _LOGGER.info(
            "Holding the Aura frame to its manual state (%s); it was running "
            "sleep=%s, %s to %s",
            "on" if self.display_wanted_on else "off",
            frame.get("scheduled_display_sleep"),
            wall_clock(frame.get(SCHEDULE_FIELD_OFF)),
            wall_clock(frame.get(SCHEDULE_FIELD_ON)),
        )
        try:
            await self.client.update_frame(self.frame_id, payload)
        except AuraApiError as err:
            # A correction that fails is not worth failing the poll over:
            # the next one tries again.
            _LOGGER.warning("Could not hold the Aura frame's state: %s", err)
            return None
        return {
            key: value
            for key, value in payload.items()
            if key != "user_set_time_zone"
        }

    async def _async_move_edge(self, *, moved: str, power_state: str) -> None:
        """Move one edge of the sleep window to now and keep the other.

        Both directions work the same way, mirrored. Off moves the time the
        frame falls asleep to now; on moves the time it wakes up to now. The
        other edge is written from the wanted schedule, so the frame keeps
        falling asleep at the user's bedtime after a switch-on and keeps
        waking at their morning time after a switch-off -- and a press
        repairs the edge an earlier press moved rather than carrying it on.

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
        times = self._wanted_times(frame)
        times[moved] = f"{datetime.now(ZoneInfo(time_zone)):%H:%M:%S}"

        await self.client.update_frame(
            self.frame_id,
            self._schedule_write(sleep=True, time_zone=time_zone, **times),
        )
        self._persist(**{STORAGE_POWER_STATE: power_state})
        await self.async_request_refresh()

    async def async_set_wanted_time(
        self, *, on_at: str | None = None, off_at: str | None = None
    ) -> None:
        """Change a wanted time and send the schedule to the frame."""
        changed: dict[str, str] = {}
        if on_at is not None:
            changed[STORAGE_WANTED_ON] = on_at
        if off_at is not None:
            changed[STORAGE_WANTED_OFF] = off_at
        self._persist(**changed)
        if self.wanted_mode == DISPLAY_MODE_MANUAL:
            # Manual mode is not running a schedule, so there is nothing to
            # send: the time is kept for whenever the schedule is picked
            # again, rather than written and undone by the next poll.
            return
        await self.async_send_wanted_schedule()

    async def async_send_wanted_schedule(self) -> None:
        """Send the wanted schedule to the frame, as it stands.

        Nothing reconciles this continuously, and that is deliberate: the
        frame can be changed from the Aura app, and the switch moves one of
        the times on purpose, so a difference between the frame and the
        wanted schedule cannot be told apart from intent. What can honestly
        be done is to send the wanted schedule when it changes, and again
        whenever someone asks for it -- after which no edge is forced any
        more, so the power state goes back to normal.

        In manual mode what Home Assistant wants is not a schedule but a
        state, so that is what gets sent instead.
        """
        if self.wanted_mode == DISPLAY_MODE_MANUAL:
            await self._async_write_manual(wanted_on=self.display_wanted_on)
            return

        frame = await self.client.get_frame(self.frame_id)
        time_zone = frame.get("time_zone") or "Europe/Berlin"
        await self.client.update_frame(
            self.frame_id,
            self._schedule_write(
                sleep=None, time_zone=time_zone, **self._wanted_times(frame)
            ),
        )
        self._persist(**{STORAGE_POWER_STATE: POWER_STATE_NORMAL})
        await self.async_request_refresh()

    async def async_set_display_mode(self, mode: str) -> None:
        """Put the display under automatic, the schedule, or the switch.

        Automatic is the frame deciding for itself by ambient light, and the
        schedule is the wanted times; in both the frame is left to run on
        its own and the wanted schedule goes along with the mode change, so
        that no edge this integration forced is left behind in it. Manual
        hands the display to the switch and writes the window that holds it
        where it is wanted.
        """
        frame = await self.client.get_frame(self.frame_id)
        time_zone = frame.get("time_zone") or "Europe/Berlin"

        if mode == DISPLAY_MODE_MANUAL:
            wanted_on = self.display_wanted_on
            times = self._manual_times(wanted_on)
            sleep = True
            power_state = (
                POWER_STATE_FORCED_ON if wanted_on else POWER_STATE_FORCED_OFF
            )
        else:
            times = self._wanted_times(frame)
            sleep = mode == DISPLAY_MODE_SCHEDULE
            power_state = POWER_STATE_NORMAL

        await self.client.update_frame(
            self.frame_id,
            self._schedule_write(sleep=sleep, time_zone=time_zone, **times),
        )
        self._persist(
            **{
                STORAGE_DISPLAY_MODE: mode,
                STORAGE_POWER_STATE: power_state,
            }
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
            "scheduled_on_at": wall_clock(frame.get(SCHEDULE_FIELD_ON)),
            "scheduled_off_at": wall_clock(frame.get(SCHEDULE_FIELD_OFF)),
            "wanted_on_at": self.wanted_on,
            "wanted_off_at": self.wanted_off,
            "display_mode": self.display_mode,
            "frame_time_zone": frame.get("time_zone"),
            "power_state": self.power_state,
        }
