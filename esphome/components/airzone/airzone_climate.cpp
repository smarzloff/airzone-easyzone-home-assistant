#include "airzone_climate.h"

#include "esphome/core/log.h"

namespace esphome {
namespace airzone {

static const char *const TAG = "airzone.climate";
static const uint16_t ON_BIT = 0x0004;
static const uint16_t DEMAND_BIT = 0x0080;  // register +9: the zone asks for heat or cold (confirmed 06/11/2026)

void AirzoneClimate::setup() {
  this->hub_->add_listener([this](uint16_t start, uint16_t count) {
    if (start == this->zone_ * 256)
      this->refresh_();
  });
}

void AirzoneClimate::dump_config() { LOG_CLIMATE("", "Zone Airzone", this); }

climate::ClimateTraits AirzoneClimate::traits() {
  auto traits = climate::ClimateTraits();
  traits.add_feature_flags(climate::CLIMATE_SUPPORTS_CURRENT_TEMPERATURE | climate::CLIMATE_SUPPORTS_CURRENT_HUMIDITY |
                           climate::CLIMATE_SUPPORTS_ACTION);
  // Any mode can be requested from any zone: it changes the system mode, as the app does (domo6, domo7).
  traits.set_supported_modes({climate::CLIMATE_MODE_OFF, climate::CLIMATE_MODE_HEAT, climate::CLIMATE_MODE_COOL,
                              climate::CLIMATE_MODE_FAN_ONLY, climate::CLIMATE_MODE_DRY});
  traits.set_visual_min_temperature(15);
  traits.set_visual_max_temperature(30);
  traits.set_visual_target_temperature_step(0.5);
  traits.set_visual_current_temperature_step(0.1);
  return traits;
}

optional<climate::ClimateMode> AirzoneClimate::system_mode_() const {
  int state = this->hub_->reg(this->zone_ * 256);
  if (state < 0)
    return {};
  switch ((state >> 8) & 0x0F) {
    case 1: return climate::CLIMATE_MODE_COOL;
    case 3: return climate::CLIMATE_MODE_FAN_ONLY;
    case 5: return climate::CLIMATE_MODE_HEAT;
    case 6: return climate::CLIMATE_MODE_DRY;
    default: return {};  // system stopped
  }
}

void AirzoneClimate::control(const climate::ClimateCall &call) {
  if (call.get_mode().has_value()) {
    climate::ClimateMode wanted = *call.get_mode();
    auto system = this->system_mode_();
    if (wanted == climate::CLIMATE_MODE_OFF) {
      this->hub_->zone_power(this->zone_, false);
    } else if (system.has_value() && *system == wanted) {
      this->hub_->zone_power(this->zone_, true);
    } else {
      // Same sequence as the webserver's schedules: mode first, then on. This changes the mode of every zone.
      uint8_t code = Airzone::MODE_HEAT;
      switch (wanted) {
        case climate::CLIMATE_MODE_COOL: code = Airzone::MODE_COOL; break;
        case climate::CLIMATE_MODE_FAN_ONLY: code = Airzone::MODE_FAN; break;
        case climate::CLIMATE_MODE_DRY: code = Airzone::MODE_DRY; break;
        default: break;
      }
      ESP_LOGI(TAG, "Zone %u : changement du mode général (code %u)", this->zone_, code);
      this->hub_->zone_mode(this->zone_, code);
      this->hub_->zone_power(this->zone_, true);
    }
  }
  if (call.get_target_temperature().has_value())
    this->hub_->zone_setpoint(this->zone_, *call.get_target_temperature());
  // the state is published when the central's read-back arrives
}

void AirzoneClimate::refresh_() {
  const uint16_t base = this->zone_ * 256;
  int state = this->hub_->reg(base);
  if (state < 0)
    return;
  bool on = state & ON_BIT;
  auto system = this->system_mode_();
  this->mode = on && system.has_value() ? *system : climate::CLIMATE_MODE_OFF;

  bool demand = this->hub_->reg(base + 9) & DEMAND_BIT;
  if (!on || !system.has_value()) {
    this->action = climate::CLIMATE_ACTION_OFF;
  } else if (!demand) {
    this->action = climate::CLIMATE_ACTION_IDLE;
  } else {
    switch (*system) {
      case climate::CLIMATE_MODE_HEAT: this->action = climate::CLIMATE_ACTION_HEATING; break;
      case climate::CLIMATE_MODE_COOL: this->action = climate::CLIMATE_ACTION_COOLING; break;
      case climate::CLIMATE_MODE_DRY: this->action = climate::CLIMATE_ACTION_DRYING; break;
      default: this->action = climate::CLIMATE_ACTION_FAN; break;
    }
  }
  this->target_temperature = this->hub_->reg(base + 3) / 10.0f;
  this->current_temperature = this->hub_->reg(base + 8) / 10.0f;
  this->current_humidity = this->hub_->reg(base + 0x1F);
  this->publish_state();
}

}  // namespace airzone
}  // namespace esphome
