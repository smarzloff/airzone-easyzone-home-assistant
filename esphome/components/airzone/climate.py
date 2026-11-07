import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import climate

from . import CONF_AIRZONE_ID, Airzone, airzone_ns

CONF_ZONE = "zone"

AirzoneClimate = airzone_ns.class_("AirzoneClimate", climate.Climate, cg.Component)

CONFIG_SCHEMA = (
    climate.climate_schema(AirzoneClimate)
    .extend(
        {
            cv.GenerateID(CONF_AIRZONE_ID): cv.use_id(Airzone),
            cv.Required(CONF_ZONE): cv.int_range(min=1, max=8),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = await climate.new_climate(config)
    await cg.register_component(var, config)
    hub = await cg.get_variable(config[CONF_AIRZONE_ID])
    cg.add(var.set_hub(hub))
    cg.add(var.set_zone(config[CONF_ZONE]))
