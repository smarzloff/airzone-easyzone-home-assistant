"""Airzone Easyzone central on the domotic bus (Modbus RTU 19200 8E1), next to the official webserver."""
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import uart
from esphome.const import CONF_ID

CODEOWNERS = []
DEPENDENCIES = ["uart"]
AUTO_LOAD = ["climate"]

CONF_AIRZONE_ID = "airzone_id"
CONF_ZONES = "zones"
CONF_SILENCE = "silence"
CONF_POLL_INTERVAL = "poll_interval"
CONF_WRITES_ENABLED = "writes_enabled"

airzone_ns = cg.esphome_ns.namespace("airzone")
Airzone = airzone_ns.class_("Airzone", cg.Component, uart.UARTDevice)

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(Airzone),
            cv.Required(CONF_ZONES): cv.All(cv.ensure_list(cv.int_range(min=1, max=8)), cv.Length(min=1)),
            # the webserver pauses ~0.8 s between bursts, and ~0.23 s once every 10 min: stay above
            cv.Optional(CONF_SILENCE, default="400ms"): cv.All(
                cv.positive_time_period_milliseconds,
                cv.Range(min=cv.TimePeriod(milliseconds=300), max=cv.TimePeriod(milliseconds=600)),
            ),
            cv.Optional(CONF_POLL_INTERVAL, default="30s"): cv.All(
                cv.positive_time_period_milliseconds, cv.Range(min=cv.TimePeriod(seconds=10))
            ),
            # off by default: first check the readings, then enable the writes
            cv.Optional(CONF_WRITES_ENABLED, default=False): cv.boolean,
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
    .extend(uart.UART_DEVICE_SCHEMA)
)

FINAL_VALIDATE_SCHEMA = uart.final_validate_device_schema(
    "airzone", baud_rate=19200, require_tx=True, require_rx=True, data_bits=8, parity="EVEN", stop_bits=1
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)
    for zone in config[CONF_ZONES]:
        cg.add(var.add_zone(zone))
    cg.add(var.set_silence_ms(config[CONF_SILENCE].total_milliseconds))
    cg.add(var.set_poll_interval_ms(config[CONF_POLL_INTERVAL].total_milliseconds))
    cg.add(var.set_writes_enabled(config[CONF_WRITES_ENABLED]))
