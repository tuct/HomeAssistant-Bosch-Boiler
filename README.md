# Boiler Demand

Home Assistant integration that switches an on/off boiler (Gastherme) from how far your
radiator valves are open. Built for Bosch Smart Home TRVs (`bosch_shc`), works with any
sensor that reports valve opening in % (ZHA, Zigbee2MQTT, ...).

- **Auto-detects valves** – Bosch `…_valvetappet` sensors and common Zigbee valve sensors are pre-selected.
- **Any boiler switch** – `switch` or `input_boolean`.
- **Hysteresis** – on when any valve ≥ on threshold, off when all valves ≤ off threshold, no change in between.
- **Minimum on / off times** – prevents short cycling.
- **Offline-safe** – unavailable, unknown and negative (`-1`) values are ignored; if *all* valves are
  offline for a while, a failsafe turns the boiler on (or off / holds, configurable).
- **Rooms** – each valve becomes a room device with its valve opening and (optional) room temperature;
  the temperature sensor on the same TRV device (or the only one in its area) is found automatically.
- **Dashboard controls** – thresholds, times, failsafe and an automatic-control switch are entities.

## Install

HACS → ⋮ → Custom repositories → add `https://github.com/tuct/HomeAssistant-Bosch-Boiler`
(type *Integration*), install *Boiler Demand*, restart Home Assistant.

Manual: copy `custom_components/boiler_demand` to `/config/custom_components/` and restart.

Then: Settings → Devices & services → Add integration → *Boiler Demand*.

1. Name and boiler switch.
2. Valve sensors (detected ones are pre-selected).
3. Rooms: a temperature sensor per valve (optional, detected ones are pre-filled).
4. Thresholds, minimum times and failsafe.

Everything can be changed later under *Configure*. Disable any old automations that switch the boiler.

## Entities

| Entity | Purpose |
| --- | --- |
| `binary_sensor.<name>_heat_demand` | Demand after hysteresis. Attributes: per-valve positions, open and offline valves |
| `sensor.<name>_status` | idle, heating, waiting for min on/off time, valves offline, failsafe, switch unavailable, disabled (+ remaining minutes) |
| `sensor.<name>_max_valve_opening` | Highest valve opening in % |
| `sensor.<name>_boiler_starts_today` | Burner starts since midnight – watch for short cycling |
| `switch.<name>_automatic_control` | Off = compute demand only, never touch the boiler switch |
| `number.<name>_on_threshold` / `_off_threshold` | Hysteresis band in % |
| `number.<name>_minimum_on_time` / `_minimum_off_time` | Anti short-cycling times |
| `select.<name>_failsafe_action` | What to do when all valves are offline |

### Rooms

Every selected valve gets its own **room device** (named after the TRV, e.g. *Heizung Tobi*), linked
under the Boiler Demand device and placed in the valve's area:

| Entity | Purpose |
| --- | --- |
| `sensor.<room>_valve_opening` | Opening in % as used by the controller (unknown = offline/ignored). Attributes: `source_entity`, `role` (demand / holding / closed / offline) |
| `sensor.<room>_room_temperature` | Room temperature from the assigned sensor (only if one is set) |

Home Assistant may put the area in front of these entity ids (e.g. `sensor.kuche_heizung_kuche_valve_opening`).

## Dashboard card

The integration ships its own card – no extra HACS frontend repo or dashboard resource needed.
Edit a dashboard → *Add card* → **Boiler Demand** (or search "Boiler").

```yaml
type: custom:boiler-demand-card
device_id: <Boiler Demand device>  # optional, defaults to the first one
title: Heizung                     # optional
show_controls: true                # automatic switch + threshold chips
```

It shows the boiler state (flame), the controller status (waiting / failsafe / …), demand, max valve
opening and starts today, plus every room with temperature and a valve bar marked with the off/on
thresholds. Rooms are found automatically; tap anything for its details, tap *Automatic* to toggle.
After an update, reload the browser page once if the old card is still shown.

Built-in cards work too:

```yaml
type: entities
title: Heizung
entities:
  - entity: switch.heating_automatic_control
  - entity: sensor.heating_status
  - entity: binary_sensor.heating_heat_demand
  - entity: sensor.heating_max_valve_opening
  - entity: sensor.heating_boiler_starts_today
  - type: section
  - entity: number.heating_on_threshold
  - entity: number.heating_off_threshold
  - entity: number.heating_minimum_on_time
  - entity: number.heating_minimum_off_time
  - entity: select.heating_failsafe_action
```

One tile row per room (valve + temperature together):

```yaml
type: grid
columns: 2
square: false
cards:
  - type: tile
    entity: sensor.heizung_tobi_room_temperature
    name: Tobi
  - type: tile
    entity: sensor.heizung_tobi_valve_opening
    name: Ventil
  - type: tile
    entity: sensor.heizung_timon_room_temperature
    name: Timon
  - type: tile
    entity: sensor.heizung_timon_valve_opening
    name: Ventil
```

## Tuning

- Start with on 25 %, off 12 %, min on 10 min, min off 8 min.
- Off threshold must sit above the valves' idle "maintenance" opening (often 5–15 %), otherwise
  the boiler never stops.
- More than ~3–4 starts per hour in mild weather: lower the boiler's flow temperature first, then
  widen the band.

## Hardware (ESPHome boiler relay)

Minimum:

| Part | Notes |
| --- | --- |
| ESP32 board, e.g. Seeed XIAO ESP32-C3 | Any ESP32 with Wi-Fi. Config: `esphome/boiler-relay-c3.yaml` |
| 3.3 V relay module, e.g. Relay-T73 V2.1 (SRD-03VDC) | VCC → 3V3, GND → GND, IN → GPIO. Contacts **COM + NC** on the boiler's room-thermostat terminals, pin `inverted: true` |
| *Only for a bare SRD-03VDC:* NPN (2N2222 / BC337) + 1 kΩ + 1N4148 | The ~120 mA coil is too much for a GPIO; a module already has this driver |
| USB power supply 5 V | |

Why COM + NC: the contact is closed while the relay coil is unpowered, so a dead ESP, a power cut
or an unflashed board means *boiler on* — the TRVs keep regulating the rooms. The pin is inverted so
the ESPHome switch still reads naturally (ON = boiler on = coil off). The coil draws ~100 mA while
the boiler is off. Verify with a multimeter on COM/NC: switch ON must read closed; if not, the module
is low-level triggered (H/L jumper) → use `inverted: false`.

Optional:

| Part | Notes |
| --- | --- |
| 1.8" TFT, ST7735, 128×160, SPI | LVGL UI. Status page: boiler state, Boiler Demand status, max valve opening, average temperature, rooms (count / calling / offline), HA connection / FAILSAFE, time. Room page: one room at a time (temperature, valve bar, role) – the room list comes from HA at runtime, no reflash when rooms change. Menu: Automatik on/off. Package `esphome/boiler-tft-st7735.yaml`, complete node `esphome/boiler-relay-c3-tft.yaml` (wiring in the file) |
| Rotary encoder with button (KY-040 / EC11), on top of the TFT | Turn to flip Status → rooms, press for the Automatik menu. Package `esphome/boiler-rotary.yaml`, complete node `esphome/boiler-relay-c3-tft-rotary.yaml`. Needs the ESPHome device option *Allow the device to perform Home Assistant actions* |
| OLED (SH1106 / SSD1306), touch screen | Local status and manual override, see `esphome/mista-heatcontrol.yaml` |

The ESPHome files are layered packages – pick the node that matches your hardware:

| Node | Layers |
| --- | --- |
| `boiler-relay-c3.yaml` | base: relay + watchdog |
| `boiler-relay-c3-tft.yaml` | base → TFT status screen |
| `boiler-relay-c3-tft-rotary.yaml` | base → TFT → rotary menu |

Rotary controls: turn flips Status → room 1 → … → room N → Status; press opens the menu with the
*Automatik* switch (set `allow_automatic_toggle: "false"` in the TFT package to disable it). In the
menu, turn moves between *Automatik* and *OK*; 15 s idle returns to the status page. The screen
turns off after 30 s without input (`screen_timeout`); the first turn or press only wakes it, and it
wakes by itself when the failsafe engages. If Home Assistant
does not allow the device's actions, the switch flips back to the real state.

The room list is the `rooms` attribute of `sensor.<name>_status` – a compact JSON list
(`n` name, `t` temperature, `v` valve %, `r` role: d demand / h holding / c closed / o offline) that any
display can use.

Safety: check the boiler manual for the voltage on the thermostat terminals (24 V or 230 V). For 230 V,
use a relay module rated for mains with proper isolation and an enclosure; never put mains on the ESP
side.

## ESPHome relay watchdog

`esphome/boiler-watchdog.yaml` is a package for the ESPHome node that drives the boiler relay. If Home
Assistant has not been connected for 10 minutes, it turns the relay **on** — the TRVs keep regulating
locally, so the flat stays warm while HA is down. Pair it with `restore_mode: RESTORE_DEFAULT_ON` on
the relay. See `esphome/mista-heatcontrol.yaml` for a complete node.

## Development

### Try the UI locally

`dev/` runs a throw-away Home Assistant in Docker with this integration mounted and simulated rooms
(valve and temperature sliders, a fake boiler switch):

```
docker compose -f dev/docker-compose.yml up -d
```

Open http://localhost:8123, create a user, then add *Boiler Demand* (boiler: *Therme (simulated relay)*).
Move the sliders on the Overview dashboard; `-1` simulates a valve that is not ready. After changing
the code: `docker compose -f dev/docker-compose.yml restart`. The simulated sensors have no device, so
assign them to areas (or pick temperatures by hand in the Rooms step) to try temperature detection.

### Tests

```
pip install pytest pytest-homeassistant-custom-component
pytest
```

`tests/test_logic.py` covers the pure decision logic; `tests/test_integration.py` runs the config flow
and controller against Home Assistant core. 
