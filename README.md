# Aura Frames for Home Assistant
Home Assistant custom integration to control [Aura](https://auraframes.com) digital picture frames via the unofficial REST API.

[![Static Badge](https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=white)](https://github.com/hacs/integration) 
[![GitHub Release](https://img.shields.io/github/v/release/sparrowsx/hacs-aura-frames?style=for-the-badge)](https://github.com/sparrowsx/hacs-aura-frames/releases)

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-FFDD00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/cptthejacko)

## Features

- Next / previous photo buttons
- Display on/off via schedule manipulation (no native power API)
- Online status, current photo, photo count, Wi-Fi network sensors
- Config flow with email/password login
- German and English translations, including automatic re-authentication
- Keeps serving the last reading across a brief API or DNS outage

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=sparrowsx&repository=hacs-aura-frames&category=Integration)

### Manual (development)

1. Copy `custom_components/aura_frames` to your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and search for **Aura Frames**.

### HACS (custom repository)

1. Open HACS → Integrations → Custom repositories.
2. Add this repository URL and category **Integration**.
3. Install **Aura Frames** and restart Home Assistant.

## Configuration

During setup, enter your Aura account email and password. If your account has multiple frames, select the one to control.

Credentials are stored in the Home Assistant config entry. A stable device UUID is generated per integration instance for API authentication.

The session Aura hands out at login is stored alongside them and reused across restarts, so the integration logs in once rather than on every setup. Every login opens a new session on the Aura account, and a frame reacts to that churn by dropping its own session: it restarts and briefly shows its pairing code before coming back.

If Aura rejects the stored session, the integration opens a new one once and carries on. When that one is rejected too, Home Assistant starts its normal re-authentication flow instead of logging in over and over. Enter the current Aura credentials there; the selected frame and its display schedule remain unchanged.

## Display on/off behavior

Aura frames have no direct power API, so the integration works through the display schedule the Aura app also writes.

- **Off** moves the scheduled off time to the current time and leaves the scheduled on time alone, so the display goes dark right away.
- **On** switches the frame's scheduled sleep off, which is what the schedule toggle in the Aura app does. Writing the old schedule back is not enough: the frame only shows photos between its on and off time, so a restore after the off time has passed -- an evening switch-on -- would leave the frame dark while Home Assistant reported the switch as on.

The times the frame had before the switch first took it over are remembered in the config entry and written back on every switch-on, which repairs the off time that switching off moved. They are never cleared, and a time the API reports in a shape the integration cannot read is left out of the update rather than sent as null, so the frame keeps whatever it has.

To hand the frame back to its own schedule, switch its sleep schedule on again in the Aura app. Do not change the schedule there while the switch is off, since the remembered times are written back on the way on.

## Dropouts

The frame is polled every five minutes. A failed request does not take the
entities down with it: the coordinator serves the last reading for up to three
polls in a row, and only then reports the frame as unavailable.

This matters beyond the gap in the history. While an entity is unavailable,
Home Assistant discards service calls aimed at it -- "Referenced entities are
missing or not currently available" -- so a scheduled switch-on simply never
happens. A frame's state hardly moves between polls, so a reading one interval
old is both closer to the truth and far less disruptive than going dark.

The online sensor is not fooled by this: the heartbeat in a stale reading keeps
ageing, so it reports the frame as offline once an outage outlasts its grace
window.

## API notice

This integration uses a reverse-engineered API used by the Aura mobile app. It is not officially supported by Aura and may break if Aura changes their API.

## Development

```
~/Projects/homeassistant-aura-frames/
├── custom_components/aura_frames/
├── tests/
├── hacs.json
├── logo.svg
├── pytest.ini
├── requirements-test.txt
└── README.md
```

### Tests

The tests run against a real Home Assistant core, supplied by
[`pytest-homeassistant-custom-component`][phcc]. It pins the core version it
tests against, so `requirements-test.txt` is the single place to bump when
testing against a newer release. Python 3.14 or later is required.

```
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest
```

[phcc]: https://github.com/MatthewFlamm/pytest-homeassistant-custom-component


## License

MIT
