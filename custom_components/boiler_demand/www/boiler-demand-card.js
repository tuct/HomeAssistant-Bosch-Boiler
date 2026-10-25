/**
 * Boiler Demand card – served and loaded by the boiler_demand integration.
 *
 *   type: custom:boiler-demand-card
 *   device_id: <Boiler Demand device>   # optional, defaults to the first one
 *   title: Heizung                       # optional
 *   show_controls: true                  # optional, automatic switch + thresholds
 *
 * Rooms are discovered from the device registry: every "Room" device linked to the
 * Boiler Demand device, with its "valve_opening" and "room_temperature" entities.
 */

const CARD_VERSION = "0.7.1";
const DOMAIN = "boiler_demand";
const MAIN_MODEL = "Valve-based boiler control";
const ROOM_MODEL = "Room";

const STRINGS = {
  en: {
    heating: "Heating",
    off: "Off",
    starts: "starts today",
    max: "max valve",
    auto: "Automatic",
    on_th: "on",
    off_th: "off",
    offline: "offline",
    no_device: "No Boiler Demand device found. Add the integration first.",
    left: "min left",
    device: "Boiler Demand device",
    title: "Title (optional)",
    show_controls: "Show automatic switch and thresholds",
    demand: "Demand",
    no_demand: "No demand",
    rooms: "rooms",
    room: "room",
    calling: "calling",
    avg: "avg",
  },
  de: {
    heating: "Heizt",
    off: "Aus",
    starts: "Starts heute",
    max: "max. Ventil",
    auto: "Automatik",
    on_th: "ein",
    off_th: "aus",
    offline: "offline",
    no_device: "Kein Boiler-Demand-Gerät gefunden. Zuerst die Integration hinzufügen.",
    left: "Min. übrig",
    device: "Boiler-Demand-Gerät",
    title: "Titel (optional)",
    show_controls: "Automatik-Schalter und Schwellen anzeigen",
    demand: "Anforderung",
    no_demand: "Keine Anforderung",
    rooms: "Räume",
    room: "Raum",
    calling: "heizen",
    avg: "Ø",
  },
};

function tr(hass, key) {
  const lang = (hass?.locale?.language || hass?.language || "en").split("-")[0];
  return (STRINGS[lang] || STRINGS.en)[key] ?? STRINGS.en[key] ?? key;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function mainDevices(hass) {
  return Object.values(hass?.devices || {})
    .filter((d) => d.model === MAIN_MODEL && (d.identifiers || []).some((i) => i[0] === DOMAIN))
    .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
}

/** translation_key -> entity_id for the entities of one device. */
function deviceEntities(hass, deviceId) {
  const out = {};
  for (const entry of Object.values(hass.entities || {})) {
    if (entry.device_id === deviceId && entry.platform === DOMAIN && entry.translation_key) {
      out[entry.translation_key] = entry.entity_id;
    }
  }
  return out;
}

function deviceName(device) {
  return device.name_by_user || device.name || "";
}

/** Drop leading words all room names share ("Heizung Tobi", "Heizung Timon" -> "Tobi", "Timon"). */
function shortNames(names) {
  if (names.length < 2) return names;
  const split = names.map((n) => n.split(/\s+/));
  let common = 0;
  while (split.every((w) => w.length > common + 1 && w[common] === split[0][common])) common += 1;
  return split.map((w) => w.slice(common).join(" "));
}

function numberState(hass, entityId) {
  const value = parseFloat(hass.states[entityId]?.state);
  return Number.isFinite(value) ? value : null;
}

class BoilerDemandCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("boiler-demand-card-editor");
  }

  static getStubConfig(hass) {
    const device = mainDevices(hass)[0];
    return device ? { device_id: device.id } : {};
  }

  setConfig(config) {
    this._config = { show_controls: true, ...config };
    this._lastKey = undefined;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const key = this._stateKey();
    if (key !== this._lastKey) {
      this._lastKey = key;
      this._render();
    }
  }

  getCardSize() {
    return 3 + (this._model()?.rooms.length || 0);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  /** Main device, its entities and its rooms – or null. */
  _model() {
    const hass = this._hass;
    if (!hass?.devices || !hass?.entities) return null;
    const devices = mainDevices(hass);
    const main = devices.find((d) => d.id === this._config?.device_id) || devices[0];
    if (!main) return null;
    const rooms = Object.values(hass.devices)
      .filter((d) => d.via_device_id === main.id && d.model === ROOM_MODEL)
      .map((d) => ({ device: d, name: deviceName(d), entities: deviceEntities(hass, d.id) }))
      .sort((a, b) => a.name.localeCompare(b.name));
    shortNames(rooms.map((r) => r.name)).forEach((name, i) => { rooms[i].name = name; });
    return { main, entities: deviceEntities(hass, main.id), rooms };
  }

  /** Re-render only when something shown on the card changed. */
  _stateKey() {
    const model = this._model();
    if (!model) return "none";
    const ids = [
      ...Object.values(model.entities),
      ...model.rooms.flatMap((r) => Object.values(r.entities)),
    ];
    const boiler = this._hass.states[model.entities.status]?.attributes?.boiler_switch;
    if (boiler) ids.push(boiler);
    return ids
      .map((id) => {
        const s = this._hass.states[id];
        return s ? `${id}=${s.state}|${JSON.stringify(s.attributes)}` : id;
      })
      .join(";") + `;${this._hass.locale?.language}`;
  }

  _fmt(entityId) {
    const stateObj = this._hass.states[entityId];
    if (!stateObj) return "–";
    return this._hass.formatEntityState ? this._hass.formatEntityState(stateObj) : stateObj.state;
  }

  _render() {
    if (!this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const hass = this._hass;
    if (!hass) return;

    const model = this._model();
    if (!model) {
      this.shadowRoot.innerHTML = `${STYLE}<ha-card><div class="empty">${esc(tr(hass, "no_device"))}</div></ha-card>`;
      return;
    }

    const e = model.entities;
    const statusObj = hass.states[e.status];
    const boilerId = statusObj?.attributes?.boiler_switch;
    const boilerOn = hass.states[boilerId]?.state === "on";
    const demandOn = hass.states[e.heat_demand]?.state === "on";
    const remaining = statusObj?.attributes?.remaining_minutes;
    const onTh = numberState(hass, e.on_threshold);
    const offTh = numberState(hass, e.off_threshold);
    const starts = hass.states[e.boiler_starts_today]?.state ?? "–";
    const maxValve = numberState(hass, e.max_valve_opening);
    const title = this._config.title || deviceName(model.main);
    const statusClass = statusObj?.state === "failsafe" || statusObj?.state === "valves_offline"
      ? "warn" : statusObj?.state === "disabled" ? "muted" : "";
    // "Heating"/"Idle" just repeat the big boiler line; show the status only when it adds something.
    const redundant = (boilerOn && statusObj?.state === "heating") || (!boilerOn && statusObj?.state === "idle");
    const statusLine = redundant ? "" : `
            <div class="status ${statusClass}" data-entity="${esc(e.status || "")}">
              ${esc(this._fmt(e.status))}${remaining != null ? ` · ${esc(remaining)} ${esc(tr(hass, "left"))}` : ""}
            </div>`;

    // Room summary: count, calling for heat (demand/holding), offline, average temperature.
    const roles = model.rooms.map((room) => {
      const id = room.entities.valve_opening;
      return (id && hass.states[id]?.attributes?.role) || "offline";
    });
    const calling = roles.filter((r) => r === "demand" || r === "holding").length;
    const offline = roles.filter((r) => r === "offline").length;
    const temps = model.rooms
      .map((room) => (room.entities.room_temperature ? numberState(hass, room.entities.room_temperature) : null))
      .filter((t) => t !== null);
    const tempUnit = model.rooms
      .map((room) => hass.states[room.entities.room_temperature]?.attributes?.unit_of_measurement)
      .find(Boolean) || "°C";
    const avgTemp = temps.length ? (temps.reduce((a, b) => a + b, 0) / temps.length).toFixed(1) : null;
    const roomStat = model.rooms.length ? `
          <div class="stat"><b>${model.rooms.length}</b> ${esc(tr(hass, model.rooms.length === 1 ? "room" : "rooms"))}${
            calling ? ` · <b>${calling}</b> ${esc(tr(hass, "calling"))}` : ""}${
            offline ? ` · <span class="warn-text"><b>${offline}</b> ${esc(tr(hass, "offline"))}</span>` : ""}</div>
          ${avgTemp !== null ? `<div class="stat">${esc(tr(hass, "avg"))} <b>${esc(avgTemp)} ${esc(tempUnit)}</b></div>` : ""}` : "";

    const rooms = model.rooms.map((room) => {
      const valveId = room.entities.valve_opening;
      const tempId = room.entities.room_temperature;
      const valve = valveId ? numberState(hass, valveId) : null;
      const roleRaw = valveId ? hass.states[valveId]?.attributes?.role : undefined;
      const role = roleRaw || (valve === null ? "offline" : "closed");
      const temp = tempId ? this._fmt(tempId) : "";
      const marks = [
        offTh !== null ? `<div class="mark" style="left:${offTh}%" title="${esc(tr(hass, "off_th"))} ${offTh}%"></div>` : "",
        onTh !== null ? `<div class="mark on" style="left:${onTh}%" title="${esc(tr(hass, "on_th"))} ${onTh}%"></div>` : "",
      ].join("");
      return `
        <div class="room" data-entity="${esc(valveId || tempId || "")}">
          <div class="room-name">${esc(room.name)}</div>
          <div class="room-temp" data-entity="${esc(tempId || "")}">${esc(temp)}</div>
          <div class="bar role-${esc(role)}">
            <div class="fill" style="width:${valve === null ? 0 : Math.min(valve, 100)}%"></div>
            ${marks}
          </div>
          <div class="room-valve">${valve === null ? esc(tr(hass, "offline")) : `${Math.round(valve)} %`}</div>
        </div>`;
    }).join("");

    const controls = this._config.show_controls ? `
      <div class="controls">
        <button class="chip ${hass.states[e.automatic_control]?.state === "on" ? "active" : ""}" data-toggle="${esc(e.automatic_control || "")}">
          <ha-icon icon="mdi:robot"></ha-icon>${esc(tr(hass, "auto"))}
        </button>
        <button class="chip" data-entity="${esc(e.on_threshold || "")}">${esc(tr(hass, "on_th"))} ${onTh ?? "–"} %</button>
        <button class="chip" data-entity="${esc(e.off_threshold || "")}">${esc(tr(hass, "off_th"))} ${offTh ?? "–"} %</button>
      </div>` : "";

    this.shadowRoot.innerHTML = `${STYLE}
      <ha-card>
        <div class="header">
          <div class="title">${esc(title)}</div>
        </div>
        <div class="hero" data-entity="${esc(boilerId || "")}">
          <div class="flame ${boilerOn ? "on" : ""}"><ha-icon icon="${boilerOn ? "mdi:fire" : "mdi:fire-off"}"></ha-icon></div>
          <div class="hero-text">
            <div class="boiler">${esc(tr(hass, boilerOn ? "heating" : "off"))}</div>${statusLine}
          </div>
        </div>
        <div class="stats">
          <div class="stat" data-entity="${esc(e.heat_demand || "")}"><span class="dot ${demandOn ? "on" : ""}"></span>${esc(tr(hass, demandOn ? "demand" : "no_demand"))}</div>
          <div class="stat" data-entity="${esc(e.max_valve_opening || "")}">${esc(tr(hass, "max"))} <b>${maxValve === null ? "–" : `${Math.round(maxValve)} %`}</b></div>
          <div class="stat" data-entity="${esc(e.boiler_starts_today || "")}"><b>${esc(starts)}</b> ${esc(tr(hass, "starts"))}</div>
          ${roomStat}
        </div>
        ${rooms ? `<div class="rooms">${rooms}</div>` : ""}
        ${controls}
      </ha-card>`;

    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) => {
      if (!el.dataset.entity) return;
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._moreInfo(el.dataset.entity);
      });
    });
    this.shadowRoot.querySelectorAll("[data-toggle]").forEach((el) => {
      if (!el.dataset.toggle) return;
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._hass.callService("switch", "toggle", { entity_id: el.dataset.toggle });
      });
    });
  }

  _moreInfo(entityId) {
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      detail: { entityId }, bubbles: true, composed: true,
    }));
  }
}

const STYLE = `<style>
  ha-card { padding: 16px; }
  .empty { color: var(--secondary-text-color); }
  .header { display: flex; justify-content: space-between; align-items: baseline; }
  .title { font-size: 1.1em; font-weight: 500; color: var(--primary-text-color); }
  [data-entity], [data-toggle] { cursor: pointer; }
  .hero { display: flex; align-items: center; gap: 14px; margin: 14px 0 10px; }
  .flame {
    width: 52px; height: 52px; border-radius: 50%; flex: none;
    display: flex; align-items: center; justify-content: center;
    background: var(--secondary-background-color); color: var(--disabled-text-color, #9e9e9e);
  }
  .flame.on {
    background: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 18%, transparent);
    color: var(--state-climate-heat-color, #ff8100);
  }
  .flame ha-icon { --mdc-icon-size: 30px; }
  .boiler { font-size: 1.6em; font-weight: 600; line-height: 1.1; color: var(--primary-text-color); }
  .status { color: var(--secondary-text-color); margin-top: 2px; }
  .status.warn { color: var(--warning-color, #f5a623); font-weight: 500; }
  .status.muted { color: var(--disabled-text-color, #9e9e9e); }
  .stats { display: flex; flex-wrap: wrap; gap: 6px 16px; color: var(--secondary-text-color); font-size: 0.9em; }
  .stat b { color: var(--primary-text-color); font-weight: 500; }
  .warn-text, .warn-text b { color: var(--warning-color, #f5a623); }
  .dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px;
    background: var(--disabled-text-color, #9e9e9e);
  }
  .dot.on { background: var(--state-climate-heat-color, #ff8100); }
  .rooms { margin-top: 14px; border-top: 1px solid var(--divider-color); padding-top: 10px; display: grid; gap: 10px; }
  .room {
    display: grid; grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "name temp" "bar valve";
    gap: 4px 10px; align-items: center;
  }
  .room-name { grid-area: name; color: var(--primary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .room-temp { grid-area: temp; color: var(--primary-text-color); font-weight: 500; text-align: right; }
  .room-valve { grid-area: valve; color: var(--secondary-text-color); font-size: 0.85em; min-width: 44px; text-align: right; }
  .bar {
    grid-area: bar; position: relative; height: 8px; border-radius: 4px;
    background: var(--secondary-background-color); overflow: visible;
  }
  .fill { height: 100%; border-radius: 4px; background: var(--disabled-text-color, #9e9e9e); transition: width 0.4s; }
  .role-demand .fill { background: var(--state-climate-heat-color, #ff8100); }
  .role-holding .fill { background: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 55%, var(--secondary-background-color)); }
  .role-offline { background: repeating-linear-gradient(45deg, var(--secondary-background-color) 0 6px, transparent 6px 12px); }
  .mark {
    position: absolute; top: -3px; width: 2px; height: 14px; margin-left: -1px;
    background: var(--secondary-text-color); opacity: 0.5; border-radius: 1px;
  }
  .mark.on { opacity: 0.9; }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  .chip {
    display: inline-flex; align-items: center; gap: 4px; font: inherit; font-size: 0.85em;
    color: var(--primary-text-color); background: var(--secondary-background-color);
    border: 1px solid var(--divider-color); border-radius: 16px; padding: 4px 10px; cursor: pointer;
  }
  .chip ha-icon { --mdc-icon-size: 16px; }
  .chip.active { border-color: var(--primary-color); color: var(--primary-color); }
</style>`;

class BoilerDemandCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) => tr(this._hass, schema.name === "device_id" ? "device" : schema.name);
      this._form.addEventListener("value-changed", (ev) => {
        this._config = ev.detail.value;
        this.dispatchEvent(new CustomEvent("config-changed", {
          detail: { config: this._config }, bubbles: true, composed: true,
        }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = { show_controls: true, ...this._config };
    this._form.schema = [
      { name: "device_id", selector: { device: { filter: [{ integration: DOMAIN, model: MAIN_MODEL }] } } },
      { name: "title", selector: { text: {} } },
      { name: "show_controls", selector: { boolean: {} } },
    ];
  }
}

if (!customElements.get("boiler-demand-card")) {
  customElements.define("boiler-demand-card", BoilerDemandCard);
  customElements.define("boiler-demand-card-editor", BoilerDemandCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "boiler-demand-card",
    name: "Boiler Demand",
    description: "Boiler state, demand status and every room with temperature and valve opening.",
    preview: true,
    documentationURL: "https://github.com/tuct/HomeAssistant-Bosch-Boiler",
  });
  console.info(`%c BOILER-DEMAND-CARD %c ${CARD_VERSION} `, "background:#ff8100;color:#fff", "");
}
