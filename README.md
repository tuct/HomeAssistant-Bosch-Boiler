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
- **Dashboard controls** – thresholds, times, failsafe and an automatic-control switch are entities.

## Install

HACS → ⋮ → Custom repositories → add `https://github.com/tuct/HomeAssistant-Bosch-Boiler`
(type *Integration*), install *Boiler Demand*, restart Home Assistant.

Manual: copy `custom_components/boiler_demand` to `/config/custom_components/` and restart.

Then: Settings → Devices & services → Add integration → *Boiler Demand*.

1. Name and boiler switch.
2. Valve sensors (detected ones are pre-selected).
3. Thresholds, minimum times and failsafe.

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
| `number.<name>_min_on_minutes` / `_min_off_minutes` | Anti short-cycling times |
| `select.<name>_failsafe_mode` | What to do when all valves are offline |

## Dashboard card

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
  - entity: number.heating_min_on_minutes
  - entity: number.heating_min_off_minutes
  - entity: select.heating_failsafe_mode
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

Optional: OLED screen (SH1106 / SSD1306), rotary encoder with button, or a touch screen for local
status and manual override (see `esphome/mista-heatcontrol.yaml`).

Safety: check the boiler manual for the voltage on the thermostat terminals (24 V or 230 V). For 230 V,
use a relay module rated for mains with proper isolation and an enclosure; never put mains on the ESP
side.

## ESPHome relay watchdog

`esphome/boiler-watchdog.yaml` is a package for the ESPHome node that drives the boiler relay. If Home
Assistant has not been connected for 10 minutes, it turns the relay **on** — the TRVs keep regulating
locally, so the flat stays warm while HA is down. Pair it with `restore_mode: RESTORE_DEFAULT_ON` on
the relay. See `esphome/mista-heatcontrol.yaml` for a complete node.

## Development

```
pip install pytest pytest-homeassistant-custom-component
pytest
```

`tests/test_logic.py` covers the pure decision logic; `tests/test_integration.py` runs the config flow
and controller against Home Assistant core. 
