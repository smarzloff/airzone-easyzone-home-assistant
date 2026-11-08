#pragma once
// Airzone Easyzone central <-> ESPHome, on the domotic bus (Modbus RTU 19200 8E1), next to the official webserver.
// Port of airzone_bridge.py / domo.py: the ESP32 is a second Modbus master that only speaks in the silences
// of the webserver's polling. See ../../../airzone-sniffer/LISEZMOI.md for the register map.

#include "esphome/core/component.h"
#include "esphome/core/optional.h"
#include "esphome/components/uart/uart.h"

#include <deque>
#include <functional>
#include <map>
#include <string>
#include <vector>

namespace esphome {
namespace airzone {

struct Request {
  std::vector<uint8_t> frame;
  uint16_t refresh_start{0};  // block to read back after a write (count 0 = none)
  uint16_t refresh_count{0};
};

class Airzone : public Component, public uart::UARTDevice {
 public:
  void add_zone(uint8_t zone) { this->zones_.push_back(zone); }
  void set_silence_ms(uint32_t ms) { this->silence_ms_ = ms; }
  void set_poll_interval_ms(uint32_t ms) { this->poll_interval_ms_ = ms; }
  void set_writes_enabled(bool enabled) { this->writes_enabled_ = enabled; }

  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  // ---- cached registers (filled by the poll), for template entities ----
  bool has(uint16_t reg) const { return this->regs_.count(reg) > 0; }
  int reg(uint16_t reg) const;  // -1 if never read
  optional<bool> flag(uint16_t reg, uint16_t mask) const;
  optional<float> field(uint16_t reg, uint16_t mask) const;
  // reg * scale, or NAN if unknown or outside [low, high] (an implausible value is never published)
  float value(uint16_t reg, float scale, float low, float high) const;
  optional<std::string> system_mode() const;
  optional<std::string> air_quality_level() const;
  bool online() const;

  // ---- commands: the app's own frames (fn 0x16 mask write, fn 06) ----
  void mask_write(uint16_t reg, uint16_t mask, bool on);
  void field_write(uint16_t reg, uint16_t mask, int value, int low, int high, int step);
  void zone_power(uint8_t zone, bool on) { this->mask_write(zone * 256, 0x0004, on); }
  void zone_setpoint(uint8_t zone, float celsius);
  // Mode codes the webserver writes in bits 8-11 of zone+0 (captures domo6 and domo7). Written on any zone, the
  // code changes the system mode, hence every zone. Cool is written 9 but read back 1.
  static constexpr uint8_t MODE_STOP = 0x0;
  static constexpr uint8_t MODE_FAN = 0x3;
  static constexpr uint8_t MODE_HEAT = 0x5;
  static constexpr uint8_t MODE_DRY = 0x6;
  static constexpr uint8_t MODE_COOL = 0x9;
  void zone_mode(uint8_t zone, uint8_t code);
  // From the "Mode général" select: stop, chaud, froid, ventilation, déshumidification. Written on the first zone.
  void set_system_mode(const std::string &mode);

  // called after each successful read with (start, count)
  void add_listener(std::function<void(uint16_t, uint16_t)> &&f) { this->listeners_.push_back(std::move(f)); }

  static std::vector<uint8_t> build_read(uint16_t start, uint16_t count);
  static std::vector<uint8_t> build_mask(uint16_t reg, uint16_t and_mask, uint16_t or_mask);
  static std::vector<uint8_t> build_write(uint16_t reg, uint16_t value);

 protected:
  enum class State { IDLE, WAITING };

  void queue_command_(std::vector<uint8_t> frame, uint16_t refresh_start, uint16_t refresh_count);
  void queue_polls_();
  void send_(const Request &req);
  void finish_();
  void fail_(const char *why);
  bool store_(const Request &req, const std::vector<uint8_t> &answer);
  void refresh_block_for_(uint16_t reg, uint16_t *start, uint16_t *count) const;
  bool self_test_();

  std::vector<uint8_t> zones_;
  uint32_t silence_ms_{400};
  uint32_t poll_interval_ms_{30000};
  bool writes_enabled_{false};

  State state_{State::IDLE};
  std::deque<Request> commands_;  // served first
  std::deque<Request> polls_;
  Request current_;
  std::vector<uint8_t> rx_;
  uint32_t last_rx_{0};
  bool seen_traffic_{false};
  uint32_t sent_at_{0};
  uint32_t next_poll_{0};
  uint32_t last_no_traffic_log_{0};
  uint8_t failures_{0};

  std::map<uint16_t, uint16_t> regs_;
  std::vector<std::function<void(uint16_t, uint16_t)>> listeners_;
};

}  // namespace airzone
}  // namespace esphome
