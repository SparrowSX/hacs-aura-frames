"""Config flow for Aura Frames."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_CLIENT_ID,
    CONF_FRAME_ID,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)


class AuraFramesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Aura Frames."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> FlowResult:
        """Handle the initial step."""

        if user_input is not None:

            await self.async_set_unique_id(
                f"{user_input[CONF_USER_ID]}_{user_input[CONF_FRAME_ID]}"
            )

            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Aura Frame ({user_input[CONF_FRAME_ID]})",
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_USER_ID): str,
                vol.Required(CONF_TOKEN): str,
                vol.Required(CONF_CLIENT_ID): str,
                vol.Required(CONF_FRAME_ID): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
        )
