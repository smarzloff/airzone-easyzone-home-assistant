#!/usr/bin/env python3
"""Airzone central <-> Home Assistant bridge over MQTT, running next to the official webserver.

The Pi is a second Modbus master on the domotic bus: every request is sent in a silence of the
webserver's polling (see domo.py). Each zone appears in Home Assistant, through MQTT discovery,
as a thermostat (on/off, setpoint, temperature, humidity) plus temperature and humidity sensors.

  python3 airzone_bridge.py --config bridge.ini
"""
import argparse
import configparser
import json
import logging
import queue
import time

import paho.mqtt.client as mqtt

from capture import open_port
from domo import BusError, field_shift, field_write, mask_write, parse_zone, request, transact

log = logging.getLogger("airzone")

PREFIX = "airzone"
DISCOVERY = "homeassistant"
HA_MODES = {"chaud": "heat", "froid": "cool", "ventilation": "fan_only", "déshumidification": "dry", "stop": "off"}
MIN_SP, MAX_SP = 15.0, 30.0
# Mode codes the webserver writes in bits 8-11 of zone+0 (captures domo6 and domo7). Written on any zone, the code
# changes the system mode, hence every zone. Cool is written 9 but read back 1.
MODE_MASK = 0x0F00
MODE_CODES = {"heat": 0x5, "cool": 0x9, "fan_only": 0x3, "dry": 0x6}
SYSTEM_CODES = {"stop": 0x0, "chaud": 0x5, "froid": 0x9, "ventilation": 0x3, "déshumidification": 0x6}
PROBE_INTERVAL = 600  # s between two new attempts on zones missed at detection
HA_ACTIONS = {"heat": "heating", "cool": "cooling", "fan_only": "fan", "dry": "drying"}  # when the zone demands

# Air quality sensor block, identified on 06/11/2026 against the app's readings.
IAQ_BASE = 0x2100
IAQ_SENSORS = (  # key, register offset, scale, name, HA device_class, unit, plausible range
    ("co2", 0x07, 1, "CO2", "carbon_dioxide", "ppm", 250, 10000),
    ("tvoc", 0x08, 1, "COV totaux", "volatile_organic_compounds_parts", "ppb", 0, 10000),
    ("pm25", 0x05, 1, "PM2.5", "pm25", "µg/m³", 0, 1000),
    ("pm10", 0x06, 1, "PM10", "pm10", "µg/m³", 0, 1000),
    ("humidity", 0x09, 1, "Humidité", "humidity", "%", 0, 100),
    ("temperature", 0x0A, 0.1, "Température", "temperature", "°C", -20, 60),
    ("pressure", 0x0B, 1, "Pression", "atmospheric_pressure", "hPa", 800, 1100),
)
IAQ_COUNT = 34  # up to 0x2121 (night mode); a size the webserver never reads, so replies can't be mixed up

# Settings from the app, identified on 06/11/2026 (domo2.jsonl): one bit each, written by fn 0x16 mask write.
SWITCHES = (  # key, register, bit, name, icon
    ("led", 0x2101, 0x0100, "LED du capteur", "mdi:led-on"),
    ("cyclic", 0x2101, 0x1000, "Ventilation cyclique", "mdi:fan-clock"),
    ("freecooling", 0x2101, 0x4000, "Free-cooling", "mdi:snowflake-thermometer"),
    ("night", 0x2121, 0x0001, "Mode nuit", "mdi:weather-night"),
    ("iaq_priority", 0x0038, 0x2000, "Priorité QAI", "mdi:air-filter"),
)
PRIORITY_READ = (0x0038, 2)  # 2 registers: a size the webserver never reads

# Settings with a value, identified on 06/11/2026 (domo3.jsonl): a bit field each, written by fn 0x16.
# Ranges and steps are the app's; values outside them are never sent.
NUMBERS = (  # key, register, mask, name, unit, min, max, step, icon
    ("night_start", 0x2121, 0x007C, "Mode nuit : début", "h", 0, 23, 1, "mdi:clock-start"),
    ("night_end", 0x2121, 0x0F80, "Mode nuit : fin", "h", 0, 23, 1, "mdi:clock-end"),
    ("cyclic_interval", 0x211F, 0x00FF, "Ventilation cyclique : intervalle", "min", 10, 60, 5, "mdi:timer-sync"),
    ("cyclic_minimum", 0x211F, 0xFF00, "Ventilation cyclique : durée minimale", "min", 5, 55, 5, "mdi:timer"),
    ("freecooling_min", 0x2120, 0x00FF, "Free-cooling : température minimale", "°C", 18, 30, 1, "mdi:thermometer-low"),
    ("freecooling_diff", 0x2120, 0xFF00, "Free-cooling : différentiel", "°C", 0, 10, 1, "mdi:thermometer-chevron-up"),
)

# System mode, register 0x0000 (domo3.jsonl): low byte. Read-only: the app's mode changes never
# appear as a write on this bus, so the bridge cannot change it.
SYSTEM_READ = (0x0000, 3)  # 3 registers: a size the webserver never reads
SYSTEM_MODES = {0: "stop", 1: "froid", 2: "chaud", 3: "ventilation", 6: "déshumidification"}
# Air quality level: register +0x39 of every zone, following the sensor's LED colour (domo4.jsonl).
# The app's numeric index (0-100) was not found on the bus: it seems computed by the webserver or the cloud.
LEVEL_OFFSET, LEVEL_COUNT = 0x39, 2  # 2 registers: a size the webserver never reads
LEVELS = {6: "bon", 10: "moyen", 14: "mauvais"}
# Sensor fan: bit 0 of 0x2103, set when the cyclic ventilation started the fan (domo4.jsonl).
FAN_REG, FAN_BIT = 0x2103, 0x0001


class Bus:
    def __init__(self, port, silence_ms):
        self.ser = open_port(port, 19200, "E")
        self.silence_ms = silence_ms

    def call(self, req):
        answer, _ = transact(self.ser, req, self.silence_ms)
        if answer is None:
            raise BusError("pas de réponse de la centrale")
        if answer[1] & 0x80:
            raise BusError(f"exception Modbus {answer[2]}")
        return answer

    def read_zone(self, zone):
        state = parse_zone(self.call(request("lire", zone))[3:-2])
        # Safety net: never publish an implausible value (a wrong frame once gave 6523.6 °C).
        if not (-20 <= state["temperature"] <= 60 and 0 <= state["humidity"] <= 100 and MIN_SP - 10 <= state["setpoint"] <= MAX_SP + 10):
            raise BusError(f"valeurs incohérentes pour la zone {zone} : {state}")
        return state


class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.bus = Bus(cfg.get("bus", "port", fallback="/dev/ttyUSB0"),
                       cfg.getfloat("bus", "silence_ms", fallback=400))
        self.interval = cfg.getfloat("bus", "interval", fallback=30)
        self.commands = queue.Queue()
        self.zones = {}          # zone -> last parsed state
        self.missing = []        # candidate zones that did not answer at detection, probed again later
        self.next_probe = 0.0
        self.announced = {}      # zone -> HA mode used in the discovery config
        self.failures = 0
        self.iaq = self.iaq_announced = self.system_announced = False

        try:  # paho-mqtt 2.x
            self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="airzone-bridge")
        except AttributeError:  # paho-mqtt 1.x
            self.mqtt = mqtt.Client(client_id="airzone-bridge")
        user = cfg.get("mqtt", "user", fallback="")
        if user:
            self.mqtt.username_pw_set(user, cfg.get("mqtt", "password", fallback=""))
        self.mqtt.will_set(f"{PREFIX}/status", "offline", retain=True)
        self.mqtt.on_connect = self.on_connect
        self.mqtt.on_message = self.on_message

    # ---- MQTT side (paho thread): only queues commands --------------------------------
    def on_connect(self, client, userdata, flags, rc, properties=None):
        # paho 2.x passes a ReasonCode (with is_failure), paho 1.x an int
        if getattr(rc, "is_failure", rc != 0):
            log.error("connexion MQTT refusée (code %s)", rc)
            return
        log.info("connecté au broker MQTT")
        client.subscribe(f"{PREFIX}/zone/+/+/set")
        client.subscribe(f"{PREFIX}/config/+/set")
        client.subscribe(f"{PREFIX}/system/mode/set")
        client.publish(f"{PREFIX}/status", "online", retain=True)
        self.announced.clear()  # republish discovery after a broker restart
        self.iaq_announced = self.system_announced = False

    def on_message(self, client, userdata, msg):
        try:
            parts = msg.topic.split("/")
            payload = msg.payload.decode().strip()
            if parts[1] in ("config", "system") and len(parts) == 4:
                self.commands.put((parts[1], parts[2], payload))
            else:
                _, _, zone, what, _ = parts
                self.commands.put((int(zone), what, payload))
        except ValueError:
            log.warning("message ignoré : %s", msg.topic)

    # ---- bus side (main thread) --------------------------------------------------------
    def detect_zones(self):
        listed = self.cfg.get("zones", "liste", fallback="").strip()
        candidates = [int(z) for z in listed.split(",")] if listed else range(1, 9)
        for zone in candidates:
            if not self.probe_zone(zone, listed):
                self.missing.append(zone)
        if not self.zones:
            raise SystemExit("Aucune zone trouvée sur le bus.")
        self.next_probe = time.monotonic() + PROBE_INTERVAL

    def probe_zone(self, zone, listed, again=False):
        """Read a candidate zone; keep it if it exists. A miss (bus glitch, radio thermostat not answering) is retried later."""
        try:
            state = self.bus.read_zone(zone)
        except (BusError, ValueError) as e:
            if not again:
                log.info("zone %d absente (%s)", zone, e)
            return False
        if not (state["name"] or listed):
            return True  # answers but has no name: not a zone, no need to probe it again
        self.zones[zone] = state
        log.info("zone %d%s : %s", zone, " retrouvée" if again else "", state["name"] or "(sans nom)")
        if again:
            self.publish(zone, state)
        return True

    def probe_missing(self):
        listed = bool(self.cfg.get("zones", "liste", fallback="").strip())
        self.missing = [z for z in self.missing if not self.probe_zone(z, listed, again=True)]
        self.next_probe = time.monotonic() + PROBE_INTERVAL

    def execute(self, zone, what, payload):
        if zone == "system":
            if what != "mode" or payload not in SYSTEM_CODES:
                log.warning("mode général inconnu : %s", payload)
                return False
            log.info("mode général -> %s", payload)
            self.bus.call(field_write(min(self.zones) * 256, MODE_MASK, SYSTEM_CODES[payload]))
            return True  # every zone changed mode: re-read them all
        if zone == "config":
            sw = next((s for s in SWITCHES if s[0] == what), None)
            num = next((n for n in NUMBERS if n[0] == what), None)
            if not self.iaq or (sw is None and num is None):
                log.warning("réglage inconnu : %s", what)
                return
            if sw:
                req = mask_write(sw[1], sw[2], payload == "ON")
            else:
                _, reg, mask, _, _, low, high, _, _ = num
                value = round(float(payload))
                if not low <= value <= high or (value - low) % num[7]:
                    log.warning("réglage %s hors plage (%s), ignoré", what, payload)
                    return
                req = field_write(reg, mask, value)
            log.info("réglage %s = %s", what, payload)
            self.bus.call(req)
            return
        if zone not in self.zones:
            log.warning("commande pour une zone inconnue : %d", zone)
            return
        if what == "mode" and payload not in ("off", HA_MODES.get(self.zones[zone]["mode"])):
            if payload not in MODE_CODES:
                log.warning("zone %d : mode %s inconnu", zone, payload)
                return False
            # Same sequence as the webserver's schedules: mode first, then on.
            log.info("zone %d : mode général -> %s", zone, payload)
            self.bus.call(field_write(zone * 256, MODE_MASK, MODE_CODES[payload]))
            self.bus.call(request("marche", zone))
            return True  # every zone changed mode: re-read them all
        if what == "mode":
            req = request("arret" if payload == "off" else "marche", zone)
        elif what == "setpoint":
            value = min(MAX_SP, max(MIN_SP, round(float(payload) * 2) / 2))
            req = request("consigne", zone, value)
        else:
            log.warning("commande inconnue : %s", what)
            return
        log.info("zone %d : %s = %s", zone, what, payload)
        self.bus.call(req)

    def detect_iaq(self):
        try:
            self.refresh_iaq()
            self.iaq = True
            log.info("capteur de qualité de l'air trouvé")
        except (BusError, ValueError) as e:
            log.info("pas de capteur de qualité de l'air (%s)", e)

    def refresh_iaq(self):
        # Same request as the webserver's (0x2100, 33 registers): a reply meant for it carries the same data.
        data = self.bus.call(request("registres", IAQ_BASE, IAQ_COUNT))[3:-2]
        regs = [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data) - 1, 2)]
        if len(regs) < IAQ_COUNT:
            raise ValueError(f"bloc qualité de l'air trop court ({len(regs)} registres)")
        if not self.iaq_announced:
            self.announce_iaq()
        for key, offset, scale, _, _, _, low, high in IAQ_SENSORS:
            value = round(regs[offset] * scale, 1)
            if low <= value <= high:  # an implausible value is skipped, never published
                self.mqtt.publish(f"{PREFIX}/iaq/{key}", str(value), retain=True)
            else:
                log.warning("qualité de l'air : %s incohérent (%s), ignoré", key, value)
        prio = self.bus.call(request("registres", *PRIORITY_READ))[3:-2]
        values = {IAQ_BASE + i: r for i, r in enumerate(regs)}
        values[PRIORITY_READ[0]] = int.from_bytes(prio[0:2], "big")
        for key, reg, bit, _, _ in SWITCHES:
            self.mqtt.publish(f"{PREFIX}/config/{key}", "ON" if values[reg] & bit else "OFF", retain=True)
        for key, reg, mask, *_ in NUMBERS:
            self.mqtt.publish(f"{PREFIX}/config/{key}", str((values[reg] & mask) >> field_shift(mask)), retain=True)
        self.mqtt.publish(f"{PREFIX}/iaq/fan", "ON" if values[FAN_REG] & FAN_BIT else "OFF", retain=True)
        zone = min(self.zones)
        level = self.bus.call(request("registres", zone * 256 + LEVEL_OFFSET, LEVEL_COUNT))[3:-2]
        raw = int.from_bytes(level[0:2], "big") & 0x0F  # bit 0x10 = zone on and demanding (capture domo5)
        self.mqtt.publish(f"{PREFIX}/iaq/level", LEVELS.get(raw, f"inconnu ({raw})"), retain=True)

    def refresh_system(self):
        data = self.bus.call(request("registres", *SYSTEM_READ))[3:-2]
        raw = int.from_bytes(data[0:2], "big")
        mode = SYSTEM_MODES.get(raw & 0xFF, f"inconnu ({raw})")
        self.mqtt.publish(f"{PREFIX}/system/mode", mode, retain=True)
        if not self.system_announced:
            select = {
                "name": "Mode général", "unique_id": "airzone_system_mode_select", "icon": "mdi:hvac",
                "device": {"identifiers": ["airzone_system"], "name": "Airzone", "manufacturer": "Airzone",
                           "model": "Centrale Easyzone"},
                "options": list(SYSTEM_CODES),
                "state_topic": f"{PREFIX}/system/mode", "command_topic": f"{PREFIX}/system/mode/set",
                "availability_topic": f"{PREFIX}/status",
            }
            self.mqtt.publish(f"{DISCOVERY}/select/airzone_system_mode/config", json.dumps(select), retain=True)
            # the read-only sensor of the previous version
            self.mqtt.publish(f"{DISCOVERY}/sensor/airzone_system_mode/config", "", retain=True)
            self.system_announced = True

    def announce_iaq(self):
        device = {"identifiers": ["airzone_iaq"], "name": "Qualité de l'air", "manufacturer": "Airzone", "model": "Capteur IAQ"}
        for key, _, _, label, device_class, unit, _, _ in IAQ_SENSORS:
            sensor = {
                "name": label, "unique_id": f"airzone_iaq_{key}", "device": device,
                "state_topic": f"{PREFIX}/iaq/{key}", "device_class": device_class,
                "unit_of_measurement": unit, "state_class": "measurement",
                "availability_topic": f"{PREFIX}/status",
            }
            self.mqtt.publish(f"{DISCOVERY}/sensor/airzone_iaq_{key}/config", json.dumps(sensor), retain=True)
        self.mqtt.publish(f"{DISCOVERY}/sensor/airzone_iaq_index/config", "", retain=True)  # removes the wrong candidate
        level = {
            "name": "Niveau de qualité", "unique_id": "airzone_iaq_level", "device": device,
            "icon": "mdi:air-filter", "state_topic": f"{PREFIX}/iaq/level",
            "availability_topic": f"{PREFIX}/status",
        }
        self.mqtt.publish(f"{DISCOVERY}/sensor/airzone_iaq_level/config", json.dumps(level), retain=True)
        fan = {
            "name": "Ventilateur du capteur", "unique_id": "airzone_iaq_fan", "device": device,
            "device_class": "running", "state_topic": f"{PREFIX}/iaq/fan",
            "availability_topic": f"{PREFIX}/status",
        }
        self.mqtt.publish(f"{DISCOVERY}/binary_sensor/airzone_iaq_fan/config", json.dumps(fan), retain=True)
        for key, _, _, label, unit, low, high, step, icon in NUMBERS:
            number = {
                "name": label, "unique_id": f"airzone_cfg_{key}", "device": device, "icon": icon,
                "state_topic": f"{PREFIX}/config/{key}", "command_topic": f"{PREFIX}/config/{key}/set",
                "min": low, "max": high, "step": step, "unit_of_measurement": unit, "mode": "box",
                "entity_category": "config", "availability_topic": f"{PREFIX}/status",
            }
            self.mqtt.publish(f"{DISCOVERY}/number/airzone_cfg_{key}/config", json.dumps(number), retain=True)
        for key, _, _, label, icon in SWITCHES:
            switch = {
                "name": label, "unique_id": f"airzone_cfg_{key}", "device": device, "icon": icon,
                "state_topic": f"{PREFIX}/config/{key}", "command_topic": f"{PREFIX}/config/{key}/set",
                "entity_category": "config", "availability_topic": f"{PREFIX}/status",
            }
            self.mqtt.publish(f"{DISCOVERY}/switch/airzone_cfg_{key}/config", json.dumps(switch), retain=True)
        self.iaq_announced = True

    def refresh(self, zone):
        self.zones[zone] = state = self.bus.read_zone(zone)
        self.publish(zone, state)

    def publish(self, zone, s):
        mode = HA_MODES.get(s["mode"], "heat")
        if self.announced.get(zone) != mode:
            self.announce(zone, s["name"], mode)
        base = f"{PREFIX}/zone/{zone}"
        action = "off" if not s["on"] else (HA_ACTIONS.get(mode, "idle") if s["demand"] else "idle")
        for key, value in (("mode", mode if s["on"] else "off"), ("action", action), ("setpoint", s["setpoint"]),
                           ("temperature", s["temperature"]), ("humidity", s["humidity"])):
            self.mqtt.publish(f"{base}/{key}", str(value), retain=True)

    def announce(self, zone, name, system_mode):
        """Home Assistant discovery. The zone can only be switched on in the system's current mode."""
        base = f"{PREFIX}/zone/{zone}"
        device = {"identifiers": [f"airzone_zone{zone}"], "name": name or f"Zone {zone}",
                  "manufacturer": "Airzone", "model": f"Zone {zone}"}
        avail = {"availability_topic": f"{PREFIX}/status"}
        modes = ["off", "heat", "cool", "fan_only", "dry"]
        climate = {
            "name": None, "unique_id": f"airzone_zone{zone}", "object_id": f"airzone_zone{zone}",
            "device": device,
            "modes": modes,
            "mode_state_topic": f"{base}/mode", "mode_command_topic": f"{base}/mode/set",
            "action_topic": f"{base}/action",
            "temperature_state_topic": f"{base}/setpoint", "temperature_command_topic": f"{base}/setpoint/set",
            "current_temperature_topic": f"{base}/temperature", "current_humidity_topic": f"{base}/humidity",
            "min_temp": MIN_SP, "max_temp": MAX_SP, "temp_step": 0.5, "precision": 0.1,
            "temperature_unit": "C", **avail,
        }
        self.mqtt.publish(f"{DISCOVERY}/climate/airzone_zone{zone}/config", json.dumps(climate), retain=True)
        for key, label, device_class, unit in (("temperature", "Température", "temperature", "°C"),
                                               ("humidity", "Humidité", "humidity", "%")):
            sensor = {
                "name": label, "unique_id": f"airzone_zone{zone}_{key}",
                "device": device, "state_topic": f"{base}/{key}",
                "device_class": device_class, "unit_of_measurement": unit, "state_class": "measurement", **avail,
            }
            self.mqtt.publish(f"{DISCOVERY}/sensor/airzone_zone{zone}_{key}/config", json.dumps(sensor), retain=True)
        self.announced[zone] = system_mode

    def bus_step(self, action, *args):
        """Run one bus operation; publish offline after repeated failures instead of crashing."""
        try:
            result = action(*args)
            if self.failures >= 3:
                self.mqtt.publish(f"{PREFIX}/status", "online", retain=True)
            self.failures = 0
            return result
        except (BusError, ValueError) as e:
            self.failures += 1
            log.warning("échec bus (%d d'affilée) : %s", self.failures, e)
            if self.failures == 3:
                self.mqtt.publish(f"{PREFIX}/status", "offline", retain=True)

    def run(self):
        self.mqtt.connect(self.cfg.get("mqtt", "host"), self.cfg.getint("mqtt", "port", fallback=1883))
        self.mqtt.loop_start()
        self.detect_zones()
        self.detect_iaq()
        next_poll = 0.0
        while True:
            timeout = max(0.0, next_poll - time.monotonic())
            try:
                zone, what, payload = self.commands.get(timeout=timeout)
                if self.bus_step(self.execute, zone, what, payload):
                    next_poll = 0.0  # system mode changed: full poll right after
                time.sleep(0.5)
                if zone == "config":
                    self.bus_step(self.refresh_iaq)
                elif zone == "system":
                    pass  # the full poll below re-reads every zone
                else:
                    self.bus_step(self.refresh, zone)
                continue
            except queue.Empty:
                pass
            for zone in list(self.zones):
                self.bus_step(self.refresh, zone)
                if not self.commands.empty():
                    break  # serve the user first, the poll resumes right after
            else:
                self.bus_step(self.refresh_system)
                if self.iaq:
                    self.bus_step(self.refresh_iaq)
                if self.missing and time.monotonic() >= self.next_probe:
                    self.probe_missing()
                next_poll = time.monotonic() + self.interval


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="bridge.ini")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = configparser.ConfigParser()
    if not cfg.read(args.config):
        raise SystemExit(f"Fichier de configuration introuvable : {args.config}")
    Bridge(cfg).run()


if __name__ == "__main__":
    main()
