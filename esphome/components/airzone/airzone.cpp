#include "airzone.h"

#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

#include <cmath>

namespace esphome {
namespace airzone {

static const char *const TAG = "airzone";
static const uint8_t SLAVE = 1;
static const uint32_t REPLY_WINDOW_MS = 300;     // the central answers in ~0.1 s
static const uint32_t NO_TRAFFIC_MS = 10000;     // no webserver traffic: never transmit
static const uint16_t IAQ_BASE = 0x2100, IAQ_COUNT = 34;   // up to 0x2121 (night mode)
static const uint16_t PRIORITY_START = 0x0038, PRIORITY_COUNT = 2;
static const uint16_t SYSTEM_START = 0x0000, SYSTEM_COUNT = 3;
static const uint16_t ZONE_COUNT = 32, LEVEL_OFFSET = 0x39, LEVEL_COUNT = 2;
// Every read size above (34, 2, 3, 32) is one the webserver never uses (80, 1, 65, 33, 12): a reply meant
// for the webserver can never be taken for ours. LEVEL and PRIORITY both read 2, but never at the same time.

static uint16_t crc16(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (int b = 0; b < 8; b++)
      crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : crc >> 1;
  }
  return crc;
}

static void push_u16(std::vector<uint8_t> &out, uint16_t v) {
  out.push_back(v >> 8);
  out.push_back(v & 0xFF);
}

static std::vector<uint8_t> with_crc(std::vector<uint8_t> body) {
  uint16_t crc = crc16(body.data(), body.size());
  body.push_back(crc & 0xFF);
  body.push_back(crc >> 8);
  return body;
}

static bool crc_ok(const uint8_t *f, size_t n) {
  return n >= 4 && crc16(f, n - 2) == (f[n - 2] | (f[n - 1] << 8));
}

std::vector<uint8_t> Airzone::build_read(uint16_t start, uint16_t count) {
  std::vector<uint8_t> b{SLAVE, 0x03};
  push_u16(b, start);
  push_u16(b, count);
  return with_crc(b);
}

std::vector<uint8_t> Airzone::build_mask(uint16_t reg, uint16_t and_mask, uint16_t or_mask) {
  std::vector<uint8_t> b{SLAVE, 0x16};
  push_u16(b, reg);
  push_u16(b, and_mask);
  push_u16(b, or_mask);
  return with_crc(b);
}

std::vector<uint8_t> Airzone::build_write(uint16_t reg, uint16_t value) {
  std::vector<uint8_t> b{SLAVE, 0x06};
  push_u16(b, reg);
  push_u16(b, value);
  return with_crc(b);
}

static uint8_t shift_of(uint16_t mask) {
  uint8_t s = 0;
  while (mask && !(mask & 1)) {
    mask >>= 1;
    s++;
  }
  return s;
}

// Possible frame lengths at the start of buf (same rules as modbus.py candidates()).
static std::vector<size_t> candidates(const uint8_t *b, size_t n) {
  std::vector<size_t> out;
  if (n < 4)
    return out;
  uint8_t fn = b[1] & 0x7F;
  if (b[1] & 0x80)
    out.push_back(5);
  if (fn == 3 || fn == 4) {
    out.push_back(8);
    out.push_back(5 + b[2]);
  } else if (fn == 5 || fn == 6) {
    out.push_back(8);
  } else if (fn == 0x10) {
    out.push_back(8);
    if (n >= 7)
      out.push_back(9 + b[6]);
  } else if (fn == 0x16) {
    out.push_back(10);
  } else if (fn == 0x17 && n >= 11) {
    out.push_back(13 + b[10]);
  }
  return out;
}

static std::vector<std::vector<uint8_t>> split(const std::vector<uint8_t> &data) {
  std::vector<std::vector<uint8_t>> out;
  size_t i = 0;
  while (data.size() - i >= 4) {
    bool found = false;
    for (size_t len : candidates(&data[i], data.size() - i)) {
      if (len <= data.size() - i && crc_ok(&data[i], len)) {
        out.emplace_back(data.begin() + i, data.begin() + i + len);
        i += len;
        found = true;
        break;
      }
    }
    if (!found)
      i++;
  }
  return out;
}

// True if frame f is the central's reply to request req (not the webserver's traffic). Same as domo.answers().
static bool answers(const std::vector<uint8_t> &req, const std::vector<uint8_t> &f) {
  if (f[0] != SLAVE || (f[1] & 0x7F) != req[1])
    return false;
  if (f[1] & 0x80)
    return f.size() == 5;
  if (req[1] == 0x03) {
    size_t count = (req[4] << 8) | req[5];
    return f.size() == 5 + 2 * count && f[2] == 2 * count;
  }
  return f[2] == req[2] && f[3] == req[3];  // writes: the reply names the same register
}

// Frames the official webserver sent (captures domo1, domo3, domo6 and domo7): the encoder must reproduce them exactly.
bool Airzone::self_test_() {
  struct Known {
    std::vector<uint8_t> got;
    const char *expected;
  };
  const Known known[] = {
      {build_mask(0x0200, 0xFFFB, 0x0004), "01160200fffb0004b7c2"},  // zone 2 on
      {build_mask(0x0200, 0xFFFB, 0x0000), "01160200fffb0000b601"},  // zone 2 off
      {build_mask(0x2121, 0xFF83, 0x0008), "01162121ff8300088c8a"},  // night mode start 2 h
      {build_mask(0x2121, 0xF07F, 0x0500), "01162121f07f05004d38"},  // night mode end 10 h
      {build_mask(0x211F, 0x00FF, 0x1E00), "0116211f00ff1e005ce5"},  // cyclic minimum 30 min
      {build_mask(0x0400, 0xF0FF, 0x0900), "01160400f0ff0900f2e2"},  // zone 4 mode cool (domo6)
      {build_mask(0x0400, 0xF0FF, 0x0500), "01160400f0ff0500f7e2"},  // zone 4 mode heat (domo6)
      {build_mask(0x0200, 0xF0FF, 0x0000), "01160200f0ff0000f4d4"},  // zone 2 mode stop (domo7)
      {build_mask(0x0200, 0xF0FF, 0x0300), "01160200f0ff0300f424"},  // zone 2 mode fan (domo7)
      {build_mask(0x0200, 0xF0FF, 0x0600), "01160200f0ff0600f774"},  // zone 2 mode dry (domo7)
      {build_mask(0x2120, 0xFF00, 0x0019), "01162120ff00001980ae"},  // free-cooling min 25 °C
  };
  bool ok = true;
  for (const auto &k : known) {
    std::string hex = format_hex(k.got);
    if (hex != k.expected) {
      ESP_LOGE(TAG, "Encodeur faux : %s au lieu de %s", hex.c_str(), k.expected);
      ok = false;
    }
  }
  std::string sp = format_hex(build_write(0x0203, 205));  // setpoint 20.5 °C (prefix only, CRC not captured)
  if (sp.rfind("0106020300cd", 0) != 0) {
    ESP_LOGE(TAG, "Encodeur de consigne faux : %s", sp.c_str());
    ok = false;
  }
  return ok;
}

void Airzone::setup() {
  if (!this->self_test_()) {
    ESP_LOGE(TAG, "Auto-test de l'encodeur échoué : écritures désactivées");
    this->writes_enabled_ = false;
  }
  this->next_poll_ = millis() + 2000;
}

void Airzone::dump_config() {
  ESP_LOGCONFIG(TAG, "Airzone (bus domotique, esclave %u)", SLAVE);
  ESP_LOGCONFIG(TAG, "  Silence minimal : %u ms", (unsigned) this->silence_ms_);
  ESP_LOGCONFIG(TAG, "  Relecture : toutes les %u s", (unsigned) (this->poll_interval_ms_ / 1000));
  ESP_LOGCONFIG(TAG, "  Écritures : %s", this->writes_enabled_ ? "activées" : "DÉSACTIVÉES (lecture seule)");
  for (auto z : this->zones_)
    ESP_LOGCONFIG(TAG, "  Zone %u", z);
}

void Airzone::queue_polls_() {
  for (auto z : this->zones_)
    this->polls_.push_back({build_read(z * 256, ZONE_COUNT)});
  this->polls_.push_back({build_read(SYSTEM_START, SYSTEM_COUNT)});
  this->polls_.push_back({build_read(IAQ_BASE, IAQ_COUNT)});
  this->polls_.push_back({build_read(PRIORITY_START, PRIORITY_COUNT)});
  if (!this->zones_.empty())
    this->polls_.push_back({build_read(this->zones_[0] * 256 + LEVEL_OFFSET, LEVEL_COUNT)});
}

void Airzone::loop() {
  const uint32_t now = millis();
  uint8_t byte;
  while (this->available() && this->read_byte(&byte)) {
    this->last_rx_ = now;
    this->seen_traffic_ = true;
    if (this->state_ == State::WAITING)
      this->rx_.push_back(byte);
  }

  if (this->state_ == State::WAITING) {
    if (now - this->sent_at_ >= REPLY_WINDOW_MS)
      this->finish_();
    return;
  }

  if (this->polls_.empty() && (int32_t) (now - this->next_poll_) >= 0) {
    this->queue_polls_();
    this->next_poll_ = now + this->poll_interval_ms_;
  }
  if (this->commands_.empty() && this->polls_.empty())
    return;

  // Speak only in a silence of the webserver's polling, and never if it is absent.
  if (!this->seen_traffic_ || now - this->last_rx_ > NO_TRAFFIC_MS) {
    if (now - this->last_no_traffic_log_ > NO_TRAFFIC_MS) {
      ESP_LOGW(TAG, "Pas de trafic du webserver : rien n'est envoyé (webserver absent, fils A/B ?)");
      this->last_no_traffic_log_ = now;
      this->failures_ = 3;  // shown as offline
    }
    return;
  }
  if (now - this->last_rx_ < this->silence_ms_)
    return;

  std::deque<Request> &queue = this->commands_.empty() ? this->polls_ : this->commands_;
  this->current_ = queue.front();
  queue.pop_front();
  this->send_(this->current_);
}

void Airzone::send_(const Request &req) {
  this->rx_.clear();
  this->write_array(req.frame);
  this->flush();
  this->sent_at_ = millis();
  this->state_ = State::WAITING;
  if (req.frame[1] != 0x03) {
    ESP_LOGI(TAG, "Écriture : %s", format_hex_pretty(req.frame).c_str());
  } else {
    ESP_LOGV(TAG, "Lecture : %s", format_hex_pretty(req.frame).c_str());
  }
}

void Airzone::finish_() {
  this->state_ = State::IDLE;
  const Request req = this->current_;
  const std::vector<uint8_t> *answer = nullptr;
  auto frames = split(this->rx_);
  for (const auto &f : frames) {
    if (answers(req.frame, f)) {
      answer = &f;
      break;
    }
  }
  if (answer == nullptr) {
    this->fail_("pas de réponse de la centrale");
    return;
  }
  if ((*answer)[1] & 0x80) {
    ESP_LOGW(TAG, "Exception Modbus %u pour %s", (*answer)[2], format_hex_pretty(req.frame).c_str());
    this->fail_("exception Modbus");
    return;
  }
  if (req.frame[1] == 0x03) {
    if (!this->store_(req, *answer)) {
      this->fail_("valeurs incohérentes");
      return;
    }
  } else if (req.refresh_count > 0) {
    // read the block back at once, so Home Assistant shows the central's state, not ours
    this->commands_.push_front({build_read(req.refresh_start, req.refresh_count)});
  }
  if (this->failures_ >= 3)
    ESP_LOGI(TAG, "Bus de nouveau joignable");
  this->failures_ = 0;
}

void Airzone::fail_(const char *why) {
  if (this->failures_ < 255)
    this->failures_++;
  ESP_LOGW(TAG, "Échec bus (%u d'affilée) : %s", this->failures_, why);
}

bool Airzone::store_(const Request &req, const std::vector<uint8_t> &answer) {
  const uint16_t start = (req.frame[2] << 8) | req.frame[3];
  const uint16_t count = (req.frame[4] << 8) | req.frame[5];
  std::vector<uint16_t> values(count);
  for (uint16_t i = 0; i < count; i++)
    values[i] = (answer[3 + 2 * i] << 8) | answer[4 + 2 * i];

  if (count == ZONE_COUNT && (start & 0xFF) == 0) {
    // Safety net: never publish an implausible zone (a wrong frame once gave 6523.6 °C).
    float temp = values[8] / 10.0f, setpoint = values[3] / 10.0f;
    if (temp < -20 || temp > 60 || values[31] > 100 || setpoint < 5 || setpoint > 40) {
      ESP_LOGW(TAG, "Zone %u : valeurs incohérentes (temp %.1f, consigne %.1f, hum %u), ignorées", start >> 8, temp,
               setpoint, values[31]);
      return false;
    }
  }
  for (uint16_t i = 0; i < count; i++)
    this->regs_[start + i] = values[i];
  for (auto &l : this->listeners_)
    l(start, count);
  return true;
}

void Airzone::refresh_block_for_(uint16_t reg, uint16_t *start, uint16_t *count) const {
  if (reg >= IAQ_BASE && reg < IAQ_BASE + IAQ_COUNT) {
    *start = IAQ_BASE;
    *count = IAQ_COUNT;
  } else if (reg >= PRIORITY_START && reg < PRIORITY_START + PRIORITY_COUNT) {
    *start = PRIORITY_START;
    *count = PRIORITY_COUNT;
  } else if (reg >= 0x0100 && reg < 0x0900) {
    *start = reg & 0xFF00;
    *count = ZONE_COUNT;
  } else {
    *count = 0;
  }
}

void Airzone::queue_command_(std::vector<uint8_t> frame, uint16_t refresh_start, uint16_t refresh_count) {
  if (!this->writes_enabled_) {
    ESP_LOGW(TAG, "Écritures désactivées (writes_enabled: false) : %s non envoyée", format_hex_pretty(frame).c_str());
    return;
  }
  this->commands_.push_back({std::move(frame), refresh_start, refresh_count});
}

void Airzone::mask_write(uint16_t reg, uint16_t mask, bool on) {
  uint16_t s, c;
  this->refresh_block_for_(reg, &s, &c);
  this->queue_command_(build_mask(reg, 0xFFFF ^ mask, on ? mask : 0), s, c);
}

void Airzone::field_write(uint16_t reg, uint16_t mask, int value, int low, int high, int step) {
  uint32_t raw = (uint32_t) value << shift_of(mask);
  if (value < low || value > high || (step > 0 && (value - low) % step) || (raw & ~mask)) {
    ESP_LOGW(TAG, "Valeur %d hors plage pour 0x%04X (%d à %d, pas %d) : rien n'est envoyé", value, reg, low, high, step);
    return;
  }
  uint16_t s, c;
  this->refresh_block_for_(reg, &s, &c);
  this->queue_command_(build_mask(reg, 0xFFFF ^ mask, raw), s, c);
}

void Airzone::zone_setpoint(uint8_t zone, float celsius) {
  int raw = (int) lroundf(celsius * 2) * 5;  // 0.5 °C steps, in tenths
  if (raw < 150 || raw > 300) {
    ESP_LOGW(TAG, "Consigne %.1f °C hors plage 15-30 °C : rien n'est envoyé", celsius);
    return;
  }
  this->queue_command_(build_write(zone * 256 + 3, raw), zone * 256, ZONE_COUNT);
}

void Airzone::zone_mode(uint8_t zone, uint8_t code) {
  if (code != MODE_STOP && code != MODE_FAN && code != MODE_HEAT && code != MODE_DRY && code != MODE_COOL) {
    ESP_LOGW(TAG, "Code de mode %u inconnu : rien n'est envoyé", code);
    return;
  }
  this->queue_command_(build_mask(zone * 256, 0xF0FF, code << 8), zone * 256, ZONE_COUNT);
  this->next_poll_ = millis();  // the system mode changed for every zone: re-read them all once the commands are done
}

void Airzone::set_system_mode(const std::string &mode) {
  uint8_t code;
  if (mode == "stop")
    code = MODE_STOP;
  else if (mode == "chaud")
    code = MODE_HEAT;
  else if (mode == "froid")
    code = MODE_COOL;
  else if (mode == "ventilation")
    code = MODE_FAN;
  else if (mode == "déshumidification")
    code = MODE_DRY;
  else {
    ESP_LOGW(TAG, "Mode général inconnu : %s", mode.c_str());
    return;
  }
  if (this->zones_.empty())
    return;
  ESP_LOGI(TAG, "Mode général -> %s", mode.c_str());
  this->zone_mode(this->zones_[0], code);
}

int Airzone::reg(uint16_t reg) const {
  auto it = this->regs_.find(reg);
  return it == this->regs_.end() ? -1 : it->second;
}

optional<bool> Airzone::flag(uint16_t reg, uint16_t mask) const {
  int v = this->reg(reg);
  if (v < 0)
    return {};
  return (v & mask) != 0;
}

optional<float> Airzone::field(uint16_t reg, uint16_t mask) const {
  int v = this->reg(reg);
  if (v < 0)
    return {};
  return (float) ((v & mask) >> shift_of(mask));
}

float Airzone::value(uint16_t reg, float scale, float low, float high) const {
  int v = this->reg(reg);
  if (v < 0)
    return NAN;
  float x = v * scale;
  return (x < low || x > high) ? NAN : x;
}

optional<std::string> Airzone::system_mode() const {
  int v = this->reg(SYSTEM_START);
  if (v < 0)
    return {};
  switch (v & 0xFF) {
    case 0: return std::string("stop");
    case 1: return std::string("froid");
    case 2: return std::string("chaud");
    case 3: return std::string("ventilation");
    case 6: return std::string("déshumidification");
    default: return str_sprintf("inconnu (%d)", v);
  }
}

optional<std::string> Airzone::air_quality_level() const {
  if (this->zones_.empty())
    return {};
  int v = this->reg(this->zones_[0] * 256 + LEVEL_OFFSET);
  if (v < 0)
    return {};
  // Low nibble only: bit 0x10 is set while the zone is on and demanding (seen in capture domo5)
  switch (v & 0x0F) {
    case 6: return std::string("bon");
    case 10: return std::string("moyen");
    case 14: return std::string("mauvais");
    default: return str_sprintf("inconnu (%d)", v);
  }
}

bool Airzone::online() const { return this->failures_ < 3 && this->seen_traffic_; }

}  // namespace airzone
}  // namespace esphome
