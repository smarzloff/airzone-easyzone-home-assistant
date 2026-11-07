#pragma once
// One Airzone zone as a Home Assistant thermostat: on/off, setpoint, temperature, humidity, heating/idle.

#include "esphome/core/component.h"
#include "esphome/components/climate/climate.h"
#include "airzone.h"

namespace esphome {
namespace airzone {

class AirzoneClimate : public climate::Climate, public Component {
 public:
  void set_hub(Airzone *hub) { this->hub_ = hub; }
  void set_zone(uint8_t zone) { this->zone_ = zone; }

  void setup() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA - 1; }

 protected:
  climate::ClimateTraits traits() override;
  void control(const climate::ClimateCall &call) override;
  void refresh_();
  // the zone's mode follows the system mode (bits 8-11 of register +0): 1 cool, 3 fan, 5 heat, 6 dry
  optional<climate::ClimateMode> system_mode_() const;

  Airzone *hub_{nullptr};
  uint8_t zone_{1};
};

}  // namespace airzone
}  // namespace esphome
