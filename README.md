# Aura Frames for Home Assistant
Home Assistant custom integration to control [Aura](https://auraframes.com) digital picture frames via the unofficial REST API.

[![Static Badge](https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=white)](https://github.com/hacs/integration) 
[![GitHub Release](https://img.shields.io/github/v/release/sparrowsx/hacs-aura-frames?style=for-the-badge)](https://github.com/sparrowsx/hacs-aura-frames/releases)

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-FFDD00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/cptthejacko)

## Features

- Next / previous photo buttons
- Display on/off via schedule manipulation (no native power API)
- The wanted schedule as two editable times, sent to the frame when changed
- Display mode select: the frame's automatic mode, its schedule, or manual
- Manual mode holds the display where the switch says, poll by poll
- Online status, current photo, photo count, Wi-Fi network sensors
- Config flow with email/password login
- German and English translations, including automatic re-authentication
- Keeps serving the last reading across a brief API or DNS outage
- Shows the frame's own sleep schedule as switch attributes

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

## The wanted schedule

Two `time` entities hold the schedule the frame is meant to run -- **Wake
time** and **Sleep time**. They live in the config entry, so they survive a
restart and can be set from the dashboard or from an automation.

They exist because the frame's own record cannot be read as the intent. The
switch overwrites one of its two times whenever it forces the display, the
Aura app writes it too, and nothing in the record says which of those a value
came from. Earlier versions tried to remember the time they had overwritten,
and that note was invisible, uneditable, and wrong as soon as anything else
changed the schedule.

Changing either time sends the pair to the frame once. Nothing holds the frame
to it afterwards, and that is deliberate: a difference between the frame and
the wanted schedule cannot be told apart from a change someone made on
purpose, so there is nothing to reconcile against. What there is instead:

- the **Send schedule** button, which writes the wanted schedule again, for
  when the frame has drifted from it;
- the switch attributes, which carry both the schedule the frame is running
  and the wanted one, so the two can be compared at a glance.

An entry that has never held a wanted schedule takes up whatever the frame is
running, once, on the first poll. From then on the times are the user's.

The mode is left alone by these writes, so the times only take effect while
the frame is in schedule mode (see below).

## Display on/off behavior

Aura frames have no direct power API, so the integration works through the
display schedule the Aura app also writes. Two times delimit the frame's
**sleep window**: the display is dark from the off time until the on time.

- **Off** moves the time the frame falls asleep to now.
- **On** moves the time it wakes up to now.

This is what the switch does in automatic and schedule mode. In manual mode
it writes a window that holds the display where it is asked for instead; see
*Display mode* below.

Each direction writes the wanted time for the edge it is not moving, so that
edge stays the user's: a frame switched on in the evening still falls asleep
at the wanted bedtime, and one switched off still wakes at the wanted morning
time. It also repairs the edge an earlier press moved, instead of carrying a
forced value forward -- which is what used to walk the sleep time forward
until the frame stopped coming on at all.

The date these times carry is not decoration. The frame sleeps from the off
time until the on time, so a window that crosses midnight has its on time on
the following day -- which is what the Aura app writes. The integration
computes that date on every write; up to version 0.5.0 it dated both times
alike, which asks the frame for a window that runs backwards.

Switching the frame's *scheduled sleep* off is a different thing and not what
this switch does, although it also lights an evening frame. That field is the
mode the Aura app offers as the automatic setting, in which the frame's own
ambient light sensor decides -- so a frame switched on that way went dark
again at dusk by itself, while Home Assistant still reported the switch as on.
The mode is a select entity instead (see below).

Every press writes, even when Home Assistant already has the frame in the
state being asked for. The display can be put back to sleep behind the
integration's back -- the Aura app, the Aura cloud and the frame's own ambient
light sensor all reach it too -- and skipping the request because of what was
noted down last time would leave the switch unable to insist: on in Home
Assistant, dark on the wall. One redundant request is the cheaper mistake.

The switch carries the schedule the frame is actually running, and the wanted
one next to it, as attributes (`scheduled_sleep`, `scheduled_on_at`,
`scheduled_off_at`, `wanted_on_at`, `wanted_off_at`, `display_mode`,
`frame_time_zone`, `power_state`). These values decide whether the display is lit, so having them
in Home Assistant turns "the frame is dark and I do not know why" into
something you can look at.

## Display mode

Who decides when the display is lit is a select entity with three options:

- **Automatic** -- the frame's own ambient light sensor. It comes on in the
  morning and goes dark in the evening by itself, and the schedule is
  ignored. This is the automatic setting the Aura app offers.
- **Schedule** -- the wanted times. The frame runs them on its own; the
  switch forces one edge when it is pressed.
- **Manual** -- the switch. Home Assistant holds the frame there.

The first two leave the frame to itself between presses, and that is what
ends an evening of photos at the frame's own sleep time: the edge the switch
did not move comes round on schedule. Manual mode is for not wanting a
schedule at all.

The frame still has no power API in manual mode, so it is still told a sleep
window -- but one short enough not to matter, parked at 02:59 to 03:00. "On"
is that window; "off" is its mirror image, where the frame is dark but for
that one minute. A window of zero length would be the honest way to say
never, but the frame has never been asked what it makes of one, and a photo
frame that reads it as "always asleep" is a worse failure than a minute at
three in the morning.

And in manual mode, every poll holds the frame to that window: a frame found
running something else is put back, within one poll interval. This is the one
place where a difference between the frame and Home Assistant can be read as
drift rather than as someone's intent, because picking manual says the
schedule is not the user's to keep. Nothing is written while the frame
already matches, so a frame left alone costs one comparison per poll.

The wanted times are kept but not sent while manual mode is on -- there is no
schedule running to send them to. They are written again when the schedule is
picked. The Send schedule button follows the mode: it asserts the wanted
schedule, or, in manual mode, the state.

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

## What the frame is doing

Turn debug logging on for the integration and every poll prints the frame's
own settings:

```yaml
logger:
  logs:
    custom_components.aura_frames: debug
```

Only plain values are printed; nested photo and environment data, and
anything that looks like a credential, an address or a link, are left out.

This is how the schedule fields above were worked out -- by writing a setting
in the Aura app and diffing two polls -- and it is the first thing to reach
for when a frame does something the integration does not explain.

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
