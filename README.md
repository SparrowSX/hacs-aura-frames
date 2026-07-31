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

If Aura expires or rejects the saved login, Home Assistant starts its normal re-authentication flow. Enter the current Aura credentials there; the selected frame and its display schedule remain unchanged.

## Display on/off behavior

Aura frames have no direct power API. The integration turns the display on/off by setting the schedule time to the current time, and restores the previous schedule when turned back off/on. The original schedule is stored in the config entry. Do not change the frame's display schedule in the Aura app while this switch is off, since turning it back on restores the schedule saved when it was turned off.

## API notice

This integration uses a reverse-engineered API used by the Aura mobile app. It is not officially supported by Aura and may break if Aura changes their API.

## Development

```
~/Projects/homeassistant-aura-frames/
├── custom_components/aura_frames/
├── hacs.json
├── logo.svg
└── README.md
```


## License

MIT
