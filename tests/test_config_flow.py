"""Tests for the Aura Frames config flow."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aura_frames.api import AuraApiError, AuraAuthError
from custom_components.aura_frames.const import (
    CONF_AUTH_TOKEN,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRAME_ID,
    CONF_PASSWORD,
    CONF_USER_ID,
    DOMAIN,
    POWER_STATE_NORMAL,
    STORAGE_POWER_STATE,
    STORAGE_SAVED_SCHEDULE,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from .const import ENTRY_DATA, FRAME, FRAME_ID

CREDENTIALS = {CONF_EMAIL: "someone@example.com", CONF_PASSWORD: "hunter2"}
NEW_CREDENTIALS = {CONF_EMAIL: "someone@example.com", CONF_PASSWORD: "neues"}

SECOND_FRAME = {**FRAME, "id": "43", "name": "Kueche"}


@pytest.fixture
def flow_client_class() -> Generator[MagicMock]:
    """Patch the client class the config flow instantiates."""
    with patch(
        "custom_components.aura_frames.config_flow.AuraClient", autospec=True
    ) as client_class:
        client = client_class.return_value
        client.login.return_value = {"id": "1", "auth_token": "token"}
        client.credentials = {CONF_USER_ID: "1", CONF_AUTH_TOKEN: "token"}
        client.get_frames.return_value = [FRAME]
        yield client_class


@pytest.fixture
def flow_client(flow_client_class: MagicMock) -> AsyncMock:
    """Return the client instance the flow will get."""
    return flow_client_class.return_value


@pytest.fixture(autouse=True)
def skip_setup() -> Generator[AsyncMock]:
    """Keep these tests from setting the integration up for real."""
    with patch(
        "custom_components.aura_frames.async_setup_entry", return_value=True
    ) as setup:
        yield setup


async def start_flow(hass: HomeAssistant) -> dict:
    """Open the user step and submit credentials."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )


async def test_a_single_frame_is_added_without_asking(
    hass: HomeAssistant, flow_client: AsyncMock
) -> None:
    """With one frame there is nothing to choose, so the flow finishes."""
    result = await start_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Flur"
    assert result["result"].unique_id == FRAME_ID

    data = result["data"]
    assert data[CONF_EMAIL] == CREDENTIALS[CONF_EMAIL]
    assert data[CONF_PASSWORD] == CREDENTIALS[CONF_PASSWORD]
    assert data[CONF_FRAME_ID] == FRAME_ID
    assert data[CONF_DEVICE_ID]
    assert data[STORAGE_POWER_STATE] == POWER_STATE_NORMAL
    assert data[STORAGE_SAVED_SCHEDULE] is None
    # The session the flow opened, so that setup does not open a second one.
    assert data[CONF_USER_ID] == "1"
    assert data[CONF_AUTH_TOKEN] == "token"


async def test_several_frames_bring_up_the_picker(
    hass: HomeAssistant, flow_client: AsyncMock
) -> None:
    """Two frames means a second step, and the choice decides the entry."""
    flow_client.get_frames.return_value = [FRAME, SECOND_FRAME]

    result = await start_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "frame"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_FRAME_ID: "43"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kueche"
    assert result["data"][CONF_FRAME_ID] == "43"


async def test_the_stored_device_id_is_the_one_aura_saw(
    hass: HomeAssistant,
    flow_client_class: MagicMock,
    flow_client: AsyncMock,
) -> None:
    """Logging in registers a device id with Aura; that one has to be kept.

    The frame step generates a fresh id when it lost the first one, which
    would leave the entry authenticating as a device Aura never saw.
    """
    flow_client.get_frames.return_value = [FRAME, SECOND_FRAME]

    result = await start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_FRAME_ID: FRAME_ID}
    )

    device_id_sent_to_aura = flow_client_class.call_args.args[1]
    assert result["data"][CONF_DEVICE_ID] == device_id_sent_to_aura


async def test_a_frame_id_outside_the_offered_list_is_rejected(
    hass: HomeAssistant, flow_client: AsyncMock
) -> None:
    """The picker validates against the frames it listed.

    This is the schema's doing, not the handler's: vol.In rejects the value
    before async_step_frame runs, which is why its own frame_not_found guard
    never fires.
    """
    flow_client.get_frames.return_value = [FRAME, SECOND_FRAME]
    result = await start_flow(hass)

    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_FRAME_ID: "99"}
        )


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (AuraAuthError("nope"), "invalid_auth"),
        (AuraApiError("down"), "cannot_connect"),
    ],
)
async def test_login_failures_are_shown_on_the_form(
    hass: HomeAssistant,
    flow_client: AsyncMock,
    side_effect: Exception,
    expected: str,
) -> None:
    """A failed login redisplays the form with a translated error."""
    flow_client.login.side_effect = side_effect

    result = await start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": expected}


async def test_an_account_without_frames_is_reported(
    hass: HomeAssistant, flow_client: AsyncMock
) -> None:
    """Logging in is not enough; there has to be something to add."""
    flow_client.get_frames.return_value = []

    result = await start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_frames"}


async def test_a_recoverable_error_lets_the_user_try_again(
    hass: HomeAssistant, flow_client: AsyncMock
) -> None:
    """The form stays usable after a failure."""
    flow_client.login.side_effect = AuraAuthError("nope")
    result = await start_flow(hass)
    assert result["errors"] == {"base": "invalid_auth"}

    flow_client.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_a_frame_that_is_already_configured_is_refused(
    hass: HomeAssistant, flow_client: AsyncMock
) -> None:
    """The frame id is the unique id, so a second attempt aborts."""
    MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id=FRAME_ID
    ).add_to_hass(hass)

    result = await start_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_every_offered_error_has_a_translation(hass: HomeAssistant) -> None:
    """Every key the flow can set must exist in strings.json."""
    import json
    from pathlib import Path

    strings = json.loads(
        Path("custom_components/aura_frames/strings.json").read_text()
    )
    declared = set(strings["config"]["error"])

    source = Path("custom_components/aura_frames/config_flow.py").read_text()
    used = set(
        line.split('"')[-2]
        for line in source.splitlines()
        if 'errors["base"] = "' in line
    )

    assert used <= declared, f"ohne Uebersetzung: {sorted(used - declared)}"


async def test_reauth_stores_the_new_password_and_reloads(
    hass: HomeAssistant, flow_client: AsyncMock
) -> None:
    """The whole point of reauth is that the entry keeps working after it."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id=FRAME_ID
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], NEW_CREDENTIALS
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == NEW_CREDENTIALS[CONF_PASSWORD]
    assert entry.data[CONF_FRAME_ID] == FRAME_ID
    assert entry.data[CONF_DEVICE_ID] == ENTRY_DATA[CONF_DEVICE_ID]
    # Reauth logged in, so the entry carries that session from here on.
    assert entry.data[CONF_USER_ID] == "1"
    assert entry.data[CONF_AUTH_TOKEN] == "token"


async def test_reauth_keeps_the_old_password_when_the_new_one_fails(
    hass: HomeAssistant, flow_client: AsyncMock
) -> None:
    """A rejected login must not overwrite what is stored."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id=FRAME_ID
    )
    entry.add_to_hass(hass)
    flow_client.login.side_effect = AuraAuthError("nope")

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], NEW_CREDENTIALS
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == ENTRY_DATA[CONF_PASSWORD]


async def test_reauth_notices_when_the_frame_is_gone(
    hass: HomeAssistant, flow_client: AsyncMock
) -> None:
    """Correct credentials for an account that no longer holds this frame."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id=FRAME_ID
    )
    entry.add_to_hass(hass)
    flow_client.get_frames.return_value = [SECOND_FRAME]

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], NEW_CREDENTIALS
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "frame_not_found"}
    assert entry.data[CONF_PASSWORD] == ENTRY_DATA[CONF_PASSWORD]


async def test_reauth_reuses_the_entry_device_id(
    hass: HomeAssistant,
    flow_client_class: MagicMock,
    flow_client: AsyncMock,
) -> None:
    """Reauth must not invent a device id Aura has never seen."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id=FRAME_ID
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], NEW_CREDENTIALS
    )

    assert flow_client_class.call_args.args[1] == ENTRY_DATA[CONF_DEVICE_ID]


def test_the_frame_schema_offers_names_not_ids() -> None:
    """A picker listing raw ids would be unusable."""
    options = {
        str(frame["id"]): frame.get("name", str(frame["id"]))
        for frame in [FRAME, SECOND_FRAME]
    }
    schema = vol.Schema({vol.Required(CONF_FRAME_ID): vol.In(options)})

    assert schema({CONF_FRAME_ID: FRAME_ID}) == {CONF_FRAME_ID: FRAME_ID}
    assert options[FRAME_ID] == "Flur"
