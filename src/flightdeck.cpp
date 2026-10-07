#include "flightdeck.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstring>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <tuple>

namespace flightdeck {
namespace {
constexpr std::size_t kMaxFrame = 256;
constexpr char kMagic[] = "FDLOG01\n";
constexpr double kPi = 3.14159265358979323846;

double distance_m(double lat_a, double lon_a, double lat_b, double lon_b) {
  const double p = kPi / 180.0;
  const double dlat = (lat_b - lat_a) * p, dlon = (lon_b - lon_a) * p;
  const double a = std::sin(dlat / 2) * std::sin(dlat / 2) +
      std::cos(lat_a * p) * std::cos(lat_b * p) * std::sin(dlon / 2) * std::sin(dlon / 2);
  return 6371008.8 * 2 * std::asin(std::sqrt(std::clamp(a, 0.0, 1.0)));
}

std::uint64_t integer(const std::string& s, const char* field) {
  std::uint64_t value = 0;
  const auto parsed = std::from_chars(s.data(), s.data() + s.size(), value);
  if (s.empty() || parsed.ec != std::errc{} || parsed.ptr != s.data() + s.size() || value > kMaxInteger)
    throw std::invalid_argument(std::string(field) + " must be an unsigned integer <= 2^53-1");
  return value;
}

double real(const std::string& s, const char* field) {
  std::size_t pos = 0;
  if (pos < s.size() && (s[pos] == '+' || s[pos] == '-')) ++pos;
  auto digits = [&] { const auto start = pos; while (pos < s.size() && s[pos] >= '0' && s[pos] <= '9') ++pos; return pos - start; };
  auto mantissa = digits();
  if (pos < s.size() && s[pos] == '.') { ++pos; mantissa += digits(); }
  bool syntax = mantissa > 0;
  if (pos < s.size() && (s[pos] == 'e' || s[pos] == 'E')) {
    ++pos; if (pos < s.size() && (s[pos] == '+' || s[pos] == '-')) ++pos;
    syntax = syntax && digits() > 0;
  }
  if (!syntax || pos != s.size()) throw std::invalid_argument(std::string(field) + " must use decimal syntax");
  // classic-locale streams avoid accepting partial strings, hex values, or locale commas.
  std::istringstream input(s);
  input.imbue(std::locale::classic());
  input >> std::noskipws;
  double value = 0;
  if (s.empty() || !(input >> value) || input.peek() != std::char_traits<char>::eof() || !std::isfinite(value))
    throw std::invalid_argument(std::string(field) + " must be a finite decimal number");
  return value;
}

using Bytes = std::vector<std::uint8_t>;
void put_u32(Bytes& b, std::uint32_t n) { for (int i = 0; i < 4; ++i) b.push_back(static_cast<std::uint8_t>(n >> (8 * i))); }
void put_u64(Bytes& b, std::uint64_t n) { for (int i = 0; i < 8; ++i) b.push_back(static_cast<std::uint8_t>(n >> (8 * i))); }
void put_double(Bytes& b, double v) {
  static_assert(sizeof(double) == sizeof(std::uint64_t) && std::numeric_limits<double>::is_iec559, "IEEE754 doubles required");
  std::uint64_t n = 0; std::memcpy(&n, &v, sizeof(n)); put_u64(b, n);
}
struct Cursor {
  const Bytes& bytes;
  std::size_t pos = 0;
  std::uint8_t byte() { if (pos >= bytes.size()) throw std::runtime_error("truncated payload"); return bytes[pos++]; }
  std::uint64_t number(int size) { std::uint64_t n = 0; for (int i = 0; i < size; ++i) n |= std::uint64_t(byte()) << (i * 8); return n; }
  double floating() { const auto n = number(8); double d = 0; std::memcpy(&d, &n, sizeof(d)); return d; }
  void end() { if (pos != bytes.size()) throw std::runtime_error("unexpected payload suffix"); }
};
Bytes encode_config(const Config& c) {
  Bytes b{0};
  put_u64(b, c.lateness_ms); put_u64(b, c.heartbeat_ms); put_u64(b, c.future_skew_ms);
  put_u32(b, c.max_buffer); put_u32(b, c.max_vehicles); put_u32(b, c.dedup_window);
  for (double d : {c.center_lat, c.center_lon, c.radius_m, c.max_speed_mps, c.max_altitude_m, c.min_battery_pct}) put_double(b, d);
  return b;
}
Config decode_config(const Bytes& b) {
  Cursor r{b}; if (r.byte() != 0) throw std::runtime_error("first frame must contain configuration");
  Config c; c.lateness_ms = r.number(8); c.heartbeat_ms = r.number(8); c.future_skew_ms = r.number(8);
  c.max_buffer = static_cast<std::uint32_t>(r.number(4)); c.max_vehicles = static_cast<std::uint32_t>(r.number(4));
  c.dedup_window = static_cast<std::uint32_t>(r.number(4));
  c.center_lat = r.floating(); c.center_lon = r.floating(); c.radius_m = r.floating();
  c.max_speed_mps = r.floating(); c.max_altitude_m = r.floating(); c.min_battery_pct = r.floating();
  r.end(); validate_config(c); return c;
}
Bytes encode_event(const Event& e) {
  Bytes b{1}; put_u64(b, e.arrival_ms); put_u64(b, e.event_ms); put_u64(b, e.sequence);
  b.push_back(static_cast<std::uint8_t>(e.vehicle.size()));
  b.insert(b.end(), e.vehicle.begin(), e.vehicle.end()); b.push_back(static_cast<std::uint8_t>(e.state));
  for (double d : {e.latitude, e.longitude, e.altitude_m, e.battery_pct}) put_double(b, d);
  return b;
}
Event decode_event(const Bytes& b) {
  Cursor r{b}; if (r.byte() != 1) throw std::runtime_error("expected telemetry frame");
  Event e; e.arrival_ms = r.number(8); e.event_ms = r.number(8); e.sequence = r.number(8);
  const auto size = r.byte(); if (size == 0 || size > 32) throw std::runtime_error("invalid vehicle length");
  for (unsigned i = 0; i < size; ++i) e.vehicle.push_back(static_cast<char>(r.byte()));
  e.state = static_cast<State>(r.byte()); e.latitude = r.floating(); e.longitude = r.floating();
  e.altitude_m = r.floating(); e.battery_pct = r.floating(); r.end(); validate_event(e); return e;
}
bool legal_transition(State from, State to) {
  if (from == to) return true;
  return (from == State::Ground && to == State::Armed) ||
    (from == State::Armed && (to == State::Ground || to == State::Airborne)) ||
    (from == State::Airborne && to == State::Landing) ||
    (from == State::Landing && (to == State::Ground || to == State::Airborne));
}
}  // namespace

const char* state_name(State state) {
  switch (state) {
    case State::Ground: return "ground"; case State::Armed: return "armed";
    case State::Airborne: return "airborne"; case State::Landing: return "landing";
  }
  throw std::invalid_argument("invalid state");
}
State parse_state(const std::string& value) {
  for (State s : {State::Ground, State::Armed, State::Airborne, State::Landing}) if (value == state_name(s)) return s;
  throw std::invalid_argument("state must be ground, armed, airborne, or landing");
}
double parse_decimal(const std::string& value) { return real(value, "number"); }

void validate_config(const Config& c) {
  if (c.lateness_ms > 86400000 || c.heartbeat_ms == 0 || c.heartbeat_ms > 86400000 || c.future_skew_ms > 86400000)
    throw std::invalid_argument("time windows must be <= 24 hours; heartbeat must be positive");
  if (!c.max_buffer || c.max_buffer > 1000000 || !c.max_vehicles || c.max_vehicles > 4096 || !c.dedup_window || c.dedup_window > 65536)
    throw std::invalid_argument("capacity out of range: buffer 1..1000000, vehicles 1..4096, dedup 1..65536");
  if (std::uint64_t(c.max_vehicles) * c.dedup_window > 4000000)
    throw std::invalid_argument("max_vehicles * dedup_window must be <= 4000000");
  for (double d : {c.center_lat, c.center_lon, c.radius_m, c.max_speed_mps, c.max_altitude_m, c.min_battery_pct})
    if (!std::isfinite(d)) throw std::invalid_argument("config values must be finite");
  if (std::abs(c.center_lat) > 90 || std::abs(c.center_lon) > 180 || c.radius_m <= 0 || c.radius_m > 20000000 ||
      c.max_speed_mps <= 0 || c.max_speed_mps > 10000 || c.max_altitude_m <= 0 || c.max_altitude_m > 100000 ||
      c.min_battery_pct < 0 || c.min_battery_pct > 100) throw std::invalid_argument("rule value out of range");
}

void validate_event(const Event& e) {
  if (e.vehicle.empty() || e.vehicle.size() > 32) throw std::invalid_argument("vehicle must have 1..32 characters");
  for (const char c : e.vehicle) if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
      (c >= '0' && c <= '9') || c == '-' || c == '_')) throw std::invalid_argument("vehicle contains forbidden character");
  if (e.arrival_ms > kMaxInteger || e.event_ms > kMaxInteger || e.sequence > kMaxInteger) throw std::invalid_argument("integer exceeds 2^53-1");
  static_cast<void>(state_name(e.state));
  for (double d : {e.latitude, e.longitude, e.altitude_m, e.battery_pct}) if (!std::isfinite(d)) throw std::invalid_argument("telemetry must be finite");
  if (std::abs(e.latitude) > 90 || std::abs(e.longitude) > 180 || e.altitude_m < -1000 || e.altitude_m > 100000 ||
      e.battery_pct < 0 || e.battery_pct > 100) throw std::invalid_argument("telemetry field outside physical input limits");
}

Event parse_csv_line(const std::string& line, std::size_t line_number) {
  try {
    if (line.size() > 512) throw std::invalid_argument("line exceeds 512 bytes");
    std::array<std::string, 9> fields;
    std::size_t begin = 0;
    for (std::size_t i = 0; i < fields.size(); ++i) {
      const auto end = line.find(',', begin);
      if ((end == std::string::npos) != (i == fields.size() - 1)) throw std::invalid_argument("expected exactly 9 unquoted CSV fields");
      fields[i] = line.substr(begin, end == std::string::npos ? end : end - begin);
      begin = end == std::string::npos ? line.size() : end + 1;
    }
    Event e; e.arrival_ms = integer(fields[0], "arrival_ms"); e.event_ms = integer(fields[1], "event_ms");
    e.vehicle = fields[2]; e.sequence = integer(fields[3], "sequence"); e.state = parse_state(fields[4]);
    e.latitude = real(fields[5], "latitude"); e.longitude = real(fields[6], "longitude");
    e.altitude_m = real(fields[7], "altitude_m"); e.battery_pct = real(fields[8], "battery_pct");
    validate_event(e); return e;
  } catch (const std::exception& error) {
    throw std::invalid_argument("CSV line " + std::to_string(line_number) + ": " + error.what());
  }
}

Processor::Processor(Config config, Sink sink) : config_(config), sink_(std::move(sink)) { validate_config(config_); }
bool Processor::Later::operator()(const Pending& a, const Pending& b) const {
  return std::tie(a.event.event_ms, a.event.vehicle, a.event.sequence, a.ordinal) >
         std::tie(b.event.event_ms, b.event.vehicle, b.event.sequence, b.ordinal);
}
void Processor::warn(const char* code, const Event& e, const std::string& detail, double observed, double limit) {
  ++metrics_.anomalies; ++metrics_.anomaly_counts[code];
  if (sink_) sink_(Anomaly{code, e.vehicle, detail, e.event_ms, e.sequence, observed, limit});
}
void Processor::ingest(const Event& e) {
  if (finished_) throw std::logic_error("cannot ingest after finish");
  validate_event(e); ++metrics_.received;
  if (saw_arrival_ && e.arrival_ms < last_arrival_ms_) {
    ++metrics_.clock_drops; warn("arrival_clock_regression", e, "arrival clock moved backwards; event dropped", double(e.arrival_ms), double(last_arrival_ms_)); return;
  }
  saw_arrival_ = true; last_arrival_ms_ = e.arrival_ms;
  if (e.event_ms > e.arrival_ms && e.event_ms - e.arrival_ms > config_.future_skew_ms) {
    ++metrics_.clock_drops; warn("event_clock_ahead", e, "event clock exceeds allowed future skew; event dropped", double(e.event_ms - e.arrival_ms), double(config_.future_skew_ms)); return;
  }
  auto it = vehicles_.find(e.vehicle);
  if (it != vehicles_.end() && (it->second.seen.count(e.sequence) || it->second.pending.count(e.sequence))) {
    ++metrics_.duplicate_drops; warn("duplicate_sequence", e, "sequence is inside bounded dedup history; event dropped"); return;
  }
  if (e.event_ms < watermark_) {
    ++metrics_.late_drops; warn("late_event", e, "event precedes watermark; event dropped", double(e.event_ms), double(watermark_)); return;
  }
  if (it == vehicles_.end() && vehicles_.size() == config_.max_vehicles) {
    ++metrics_.vehicle_drops; warn("vehicle_capacity", e, "vehicle table is full; unknown vehicle dropped"); return;
  }
  max_event_ms_ = std::max(max_event_ms_, e.event_ms);
  watermark_ = max_event_ms_ > config_.lateness_ms ? max_event_ms_ - config_.lateness_ms : 0;
  drain(watermark_);
  if (queue_.size() == config_.max_buffer) {
    ++metrics_.capacity_drops; warn("buffer_capacity", e, "reorder buffer is full; newest event dropped"); check_heartbeats(); return;
  }
  if (it == vehicles_.end()) it = vehicles_.emplace(e.vehicle, Vehicle{}).first;
  auto& v = it->second;
  v.seen.insert(e.sequence); v.recent.push_back(e.sequence); v.pending.insert(e.sequence);
  if (v.recent.size() > config_.dedup_window) { v.seen.erase(v.recent.front()); v.recent.pop_front(); ++metrics_.dedup_evictions; }
  queue_.push(Pending{e, ordinal_++});
  metrics_.peak_buffer = std::max(metrics_.peak_buffer, std::uint64_t(queue_.size()));
  metrics_.peak_vehicles = std::max(metrics_.peak_vehicles, std::uint64_t(vehicles_.size()));
  drain(watermark_); check_heartbeats();
}
void Processor::drain(std::uint64_t until) {
  while (!queue_.empty() && queue_.top().event.event_ms <= until) {
    Event event = queue_.top().event; queue_.pop(); process(event);
  }
}
void Processor::check_heartbeats() {
  for (auto& entry : vehicles_) {
    auto& v = entry.second;
    if (v.has_last && !v.stale && watermark_ > v.last.event_ms && watermark_ - v.last.event_ms > config_.heartbeat_ms) {
      v.stale = true; warn("heartbeat_stale", v.last, "watermark advanced without a heartbeat", double(watermark_ - v.last.event_ms), double(config_.heartbeat_ms));
    }
  }
}
void Processor::process(const Event& e) {
  auto& v = vehicles_.at(e.vehicle); v.pending.erase(e.sequence); ++metrics_.emitted;
  if (!v.has_last && e.state != State::Ground) warn("initial_state", e, "first sample is not grounded; initial history is incomplete");
  if (v.has_last) {
    if (e.sequence <= v.last.sequence) warn("sequence_regression", e, "sequence did not increase in event-time order", double(e.sequence), double(v.last.sequence));
    if (!legal_transition(v.last.state, e.state)) warn("state_transition", e, std::string(state_name(v.last.state)) + " -> " + state_name(e.state));
    const auto delta = e.event_ms - v.last.event_ms;
    if (delta > config_.heartbeat_ms) warn("telemetry_gap", e, "gap between samples exceeds heartbeat window", double(delta), double(config_.heartbeat_ms));
    const double distance = distance_m(v.last.latitude, v.last.longitude, e.latitude, e.longitude);
    if (delta == 0 && distance > 0.01) warn("position_time_conflict", e, "distinct positions have the same event timestamp", distance, 0);
    else if (delta > 0 && distance * 1000 / double(delta) > config_.max_speed_mps)
      warn("velocity_limit", e, "horizontal ground speed exceeds configured limit", distance * 1000 / double(delta), config_.max_speed_mps);
    if (v.stale) warn("heartbeat_recovered", e, "telemetry resumed after a stale heartbeat");
  }
  const double radius = distance_m(config_.center_lat, config_.center_lon, e.latitude, e.longitude);
  if (radius > config_.radius_m) warn("geofence", e, "sample is outside circular geofence", radius, config_.radius_m);
  if (e.altitude_m > config_.max_altitude_m) warn("altitude_limit", e, "altitude exceeds configured ceiling", e.altitude_m, config_.max_altitude_m);
  if (e.battery_pct < config_.min_battery_pct) warn("low_battery", e, "battery is below configured reserve", e.battery_pct, config_.min_battery_pct);
  v.last = e; v.has_last = true; v.stale = false;
}
void Processor::finish() {
  if (finished_) return;
  drain(std::numeric_limits<std::uint64_t>::max());
  // End-of-file drains pending data, but is not interpreted as infinite wall-clock silence.
  finished_ = true;
}

std::uint32_t crc32(const std::uint8_t* data, std::size_t size) {
  // IEEE CRC-32, not an authentication mechanism.
  static const auto table = [] {
    std::array<std::uint32_t, 256> t{};
    for (std::uint32_t i = 0; i < 256; ++i) { auto c = i; for (int bit = 0; bit < 8; ++bit) c = (c >> 1) ^ ((c & 1) ? 0xedb88320U : 0); t[i] = c; }
    return t;
  }();
  std::uint32_t c = 0xffffffffU;
  for (std::size_t i = 0; i < size; ++i) c = table[(c ^ data[i]) & 255] ^ (c >> 8);
  return c ^ 0xffffffffU;
}
Recorder::Recorder(const std::string& path, const Config& c) {
  validate_config(c); stream_.open(path, std::ios::binary | std::ios::trunc);
  if (!stream_) throw std::runtime_error("cannot open recorder: " + path);
  stream_.write(kMagic, 8); frame(encode_config(c));
}
void Recorder::frame(const Bytes& payload) {
  Bytes prefix; put_u32(prefix, static_cast<std::uint32_t>(payload.size()));
  stream_.write(reinterpret_cast<const char*>(prefix.data()), static_cast<std::streamsize>(prefix.size()));
  stream_.write(reinterpret_cast<const char*>(payload.data()), static_cast<std::streamsize>(payload.size()));
  Bytes checksum; put_u32(checksum, crc32(payload.data(), payload.size()));
  stream_.write(reinterpret_cast<const char*>(checksum.data()), static_cast<std::streamsize>(checksum.size()));
  stream_.flush(); // Detect write failures before accepting another record; not an fsync guarantee.
  if (!stream_) throw std::runtime_error("flight recorder write failed");
}
void Recorder::append(const Event& e) {
  if (sealed_) throw std::logic_error("recorder already sealed");
  validate_event(e); frame(encode_event(e)); ++count_;
}
void Recorder::seal() {
  if (sealed_) return;
  Bytes payload{2}; put_u64(payload, count_); frame(payload); sealed_ = true;
}
LogReader::LogReader(const std::string& path) : stream_(path, std::ios::binary) {
  if (!stream_) throw std::runtime_error("cannot open log: " + path);
  char magic[8]; stream_.read(magic, 8);
  if (stream_.gcount() != 8 || std::memcmp(magic, kMagic, 8) != 0) throw std::runtime_error("invalid flight recorder magic/version");
  offset_ = 8; bool eof = false; const auto payload = next_frame(eof);
  if (eof) throw std::runtime_error("missing recorder configuration");
  config_ = decode_config(payload);
}
std::vector<std::uint8_t> LogReader::next_frame(bool& clean_eof) {
  clean_eof = false; std::array<std::uint8_t, 4> header{};
  stream_.read(reinterpret_cast<char*>(header.data()), 4);
  if (stream_.gcount() == 0 && stream_.eof()) { clean_eof = true; return {}; }
  if (stream_.gcount() != 4) throw std::runtime_error("partial frame length");
  const std::uint32_t length = std::uint32_t(header[0]) | (std::uint32_t(header[1]) << 8) |
      (std::uint32_t(header[2]) << 16) | (std::uint32_t(header[3]) << 24);
  if (length == 0 || length > kMaxFrame) throw std::runtime_error("frame length outside 1..256 byte bound");
  Bytes payload(length); stream_.read(reinterpret_cast<char*>(payload.data()), length);
  if (stream_.gcount() != static_cast<std::streamsize>(length)) throw std::runtime_error("partial frame payload");
  stream_.read(reinterpret_cast<char*>(header.data()), 4);
  if (stream_.gcount() != 4) throw std::runtime_error("partial frame checksum");
  const auto expected = std::uint32_t(header[0]) | (std::uint32_t(header[1]) << 8) |
      (std::uint32_t(header[2]) << 16) | (std::uint32_t(header[3]) << 24);
  if (crc32(payload.data(), payload.size()) != expected) throw std::runtime_error("frame checksum mismatch");
  offset_ += 8 + length; return payload;
}
Recovery LogReader::read(const std::function<void(const Event&)>& sink, bool salvage) {
  if (consumed_) throw std::logic_error("log already consumed"); consumed_ = true;
  Recovery result; result.valid_bytes = offset_;
  for (;;) {
    Bytes payload;
    Event event;
    bool is_event = false;
    try {
      bool eof = false; payload = next_frame(eof);
      if (eof) throw std::runtime_error("missing end-of-log seal");
      if (payload[0] == 1) { event = decode_event(payload); is_event = true; }
      else if (payload[0] == 2) {
        Cursor r{payload}; r.byte(); const auto expected = r.number(8); r.end();
        if (expected != result.events) throw std::runtime_error("seal event count mismatch");
        if (stream_.peek() != std::char_traits<char>::eof()) throw std::runtime_error("data after end-of-log seal");
        result.sealed = true; result.valid_bytes = offset_; return result;
      } else throw std::runtime_error("unknown frame type");
    } catch (const std::exception& error) {
      if (!salvage) throw std::runtime_error("log offset " + std::to_string(result.valid_bytes) + ": " + error.what());
      result.recovered_prefix = true; result.reason = error.what(); return result;
    }
    // User sink errors must propagate, never be mistaken for recoverable log corruption.
    if (is_event) { sink(event); ++result.events; result.valid_bytes = offset_; }
  }
}

std::string json_escape(const std::string& s) {
  std::ostringstream out; out << '"';
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') out << '\\' << c;
    else if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c) << std::dec;
    else out << c;
  }
  out << '"'; return out.str();
}
void write_config(std::ostream& o, const Config& c) {
  o << std::setprecision(17) << "{\"lateness_ms\":" << c.lateness_ms << ",\"heartbeat_ms\":" << c.heartbeat_ms
    << ",\"future_skew_ms\":" << c.future_skew_ms << ",\"max_buffer\":" << c.max_buffer
    << ",\"max_vehicles\":" << c.max_vehicles << ",\"dedup_window\":" << c.dedup_window
    << ",\"center_lat\":" << c.center_lat << ",\"center_lon\":" << c.center_lon << ",\"radius_m\":" << c.radius_m
    << ",\"max_speed_mps\":" << c.max_speed_mps << ",\"max_altitude_m\":" << c.max_altitude_m << ",\"min_battery_pct\":" << c.min_battery_pct << '}';
}
void write_metrics(std::ostream& o, const Metrics& m) {
  o << "{\"received\":" << m.received << ",\"emitted\":" << m.emitted << ",\"duplicate_drops\":" << m.duplicate_drops
    << ",\"late_drops\":" << m.late_drops << ",\"capacity_drops\":" << m.capacity_drops
    << ",\"vehicle_drops\":" << m.vehicle_drops << ",\"clock_drops\":" << m.clock_drops
    << ",\"dedup_evictions\":" << m.dedup_evictions << ",\"anomalies\":" << m.anomalies
    << ",\"peak_buffer\":" << m.peak_buffer << ",\"peak_vehicles\":" << m.peak_vehicles << ",\"anomaly_counts\":{";
  bool first = true; for (const auto& entry : m.anomaly_counts) { if (!first) o << ','; first = false; o << json_escape(entry.first) << ':' << entry.second; } o << "}}";
}
void write_anomaly(std::ostream& o, const Anomaly& a) {
  o << std::setprecision(17) << "{\"code\":" << json_escape(a.code) << ",\"vehicle\":" << json_escape(a.vehicle)
    << ",\"event_ms\":" << a.event_ms << ",\"sequence\":" << a.sequence << ",\"detail\":" << json_escape(a.detail)
    << ",\"observed\":" << a.observed << ",\"limit\":" << a.limit << '}';
}
}  // namespace flightdeck
