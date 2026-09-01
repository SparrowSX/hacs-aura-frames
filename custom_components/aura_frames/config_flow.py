"""Config flow for Aura Frames."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuraApiError, AuraAuthError, AuraClient
from .const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRAME_ID,
    CONF_PASSWORD,
    DOMAIN,
    POWER_STATE_NORMAL,
    STORAGE_POWER_STATE,
    STORAGE_SAVED_SCHEDULE,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class AuraFramesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Aura Frames."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._email: str | None = None
        self._password: str | None = None
        self._device_id: str | None = None
        self._credentials: dict[str, str | None] = {}
        self._frames: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]

            session = async_get_clientsession(self.hass)
            device_id = str(uuid.uuid4()).upper()
            client = AuraClient(session, device_id)

            try:
                await client.login(self._email, self._password)
                self._frames = await client.get_frames()
            except AuraAuthError:
                errors["base"] = "invalid_auth"
            except AuraApiError:
                errors["base"] = "cannot_connect"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            else:
                # Carried into the entry so that setting up, and every
                # restart after it, resumes this session instead of opening
                # another one.
                self._credentials = client.credentials
                if not self._frames:
                    errors["base"] = "no_frames"
                elif len(self._frames) == 1:
                    return await self._create_entry(self._frames[0], device_id)
                else:
                    self._device_id = device_id
                    return await self.async_step_frame()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_frame(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a frame when multiple are available."""
        errors: dict[str, str] = {}

        if user_input is not None:
            frame_id = user_input[CONF_FRAME_ID]
            frame = next(
                (item for item in self._frames if str(item["id"]) == frame_id),
                None,
            )
            if frame is None:
                errors["base"] = "frame_not_found"
            else:
                device_id = self._device_id or str(uuid.uuid4()).upper()
                return await self._create_entry(frame, device_id)

        frame_options = {
            str(frame["id"]): frame.get("name", str(frame["id"]))
            for frame in self._frames
        }
        schema = vol.Schema({vol.Required(CONF_FRAME_ID): vol.In(frame_options)})

        return self.async_show_form(
            step_id="frame",
            data_schema=schema,
            errors=errors,
        )

    async def _create_entry(
        self, frame: dict[str, Any], device_id: str
    ) -> ConfigFlowResult:
        """Create a config entry for the selected frame."""
        # Aura is not consistent about the type of the frame id, so normalize it
        # here: everything downstream compares it as a string.
        frame_id = str(frame["id"])
        await self.async_set_unique_id(frame_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=frame.get("name", "Aura Frame"),
            data={
                CONF_EMAIL: self._email,
                CONF_PASSWORD: self._password,
                CONF_FRAME_ID: frame_id,
                CONF_DEVICE_ID: device_id,
                **self._credentials,
                STORAGE_POWER_STATE: POWER_STATE_NORMAL,
                STORAGE_SAVED_SCHEDULE: None,
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an existing entry."""
        self._email = entry_data[CONF_EMAIL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm new credentials and update the existing entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]
            entry = self._get_reauth_entry()
            client = AuraClient(
                async_get_clientsession(self.hass), entry.data[CONF_DEVICE_ID]
            )
            try:
                await client.login(email, password)
                frames = await client.get_frames()
            except AuraAuthError:
                errors["base"] = "invalid_auth"
            except (AuraApiError, aiohttp.ClientError):
                errors["base"] = "cannot_connect"
            else:
                if not any(
                    str(frame.get("id")) == str(entry.data[CONF_FRAME_ID])
                    for frame in frames
                ):
                    errors["base"] = "frame_not_found"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            CONF_EMAIL: email,
                            CONF_PASSWORD: password,
                            **client.credentials,
                        },
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=self._email): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )
