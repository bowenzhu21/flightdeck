#pragma once

#include <cstdint>
#include <deque>
#include <fstream>
#include <functional>
#include <map>
#include <queue>
#include <string>
#include <unordered_set>
#include <vector>

namespace flightdeck {

constexpr std::uint64_t kMaxInteger = 9007199254740991ULL;
enum class State : std::uint8_t { Ground, Armed, Airborne, Landing };
const char* state_name(State state);
State parse_state(const std::string& value);

struct Event {
  std::uint64_t arrival_ms = 0, event_ms = 0, sequence = 0;
  std::string vehicle;
  State state = State::Ground;
  double latitude = 0, longitude = 0, altitude_m = 0, battery_pct = 100;
};

struct Config {
  std::uint64_t lateness_ms = 250, heartbeat_ms = 2000, future_skew_ms = 100;
  std::uint32_t max_buffer = 4096, max_vehicles = 256, dedup_window = 256;
  double center_lat = 43.4723, center_lon = -80.5449;
  double radius_m = 500, max_speed_mps = 45, max_altitude_m = 120, min_battery_pct = 20;
};
void validate_config(const Config& config);
void validate_event(const Event& event);
Event parse_csv_line(const std::string& line, std::size_t line_number);
double parse_decimal(const std::string& value);
constexpr const char* kCsvHeader = "arrival_ms,event_ms,vehicle,sequence,state,latitude,longitude,altitude_m,battery_pct";

struct Anomaly {
  std::string code, vehicle, detail;
  std::uint64_t event_ms = 0, sequence = 0;
  double observed = 0, limit = 0;
};
struct Metrics {
  std::uint64_t received = 0, emitted = 0, duplicate_drops = 0, late_drops = 0;
  std::uint64_t capacity_drops = 0, vehicle_drops = 0, clock_drops = 0;
  std::uint64_t dedup_evictions = 0, anomalies = 0, peak_buffer = 0, peak_vehicles = 0;
  std::map<std::string, std::uint64_t> anomaly_counts;
};

class Processor {
 public:
  using Sink = std::function<void(const Anomaly&)>;
  explicit Processor(Config config = {}, Sink sink = {});
  void ingest(const Event& event);
  void finish();
  const Metrics& metrics() const { return metrics_; }
  std::uint64_t watermark() const { return watermark_; }
  std::size_t buffered() const { return queue_.size(); }
 private:
  struct Vehicle {
    std::deque<std::uint64_t> recent;
    std::unordered_set<std::uint64_t> seen;
    std::unordered_set<std::uint64_t> pending;
    Event last;
    bool has_last = false, stale = false;
  };
  struct Pending { Event event; std::uint64_t ordinal; };
  struct Later { bool operator()(const Pending& a, const Pending& b) const; };
  Config config_;
  Sink sink_;
  Metrics metrics_;
  std::map<std::string, Vehicle> vehicles_;
  std::priority_queue<Pending, std::vector<Pending>, Later> queue_;
  std::uint64_t watermark_ = 0, max_event_ms_ = 0, last_arrival_ms_ = 0, ordinal_ = 0;
  bool saw_arrival_ = false, finished_ = false;
  void drain(std::uint64_t until);
  void process(const Event& event);
  void check_heartbeats();
  void warn(const char* code, const Event& event, const std::string& detail, double observed = 0, double limit = 0);
};

std::uint32_t crc32(const std::uint8_t* data, std::size_t size);
class Recorder {
 public:
  Recorder(const std::string& path, const Config& config);
  void append(const Event& event);
  void seal();
 private:
  std::ofstream stream_;
  std::uint64_t count_ = 0;
  bool sealed_ = false;
  void frame(const std::vector<std::uint8_t>& payload);
};
struct Recovery {
  bool sealed = false, recovered_prefix = false;
  std::uint64_t events = 0, valid_bytes = 0;
  std::string reason;
};
// Construction validates the magic and checksum-protected configuration before exposing it.
class LogReader {
 public:
  explicit LogReader(const std::string& path);
  const Config& config() const { return config_; }
  Recovery read(const std::function<void(const Event&)>& sink, bool salvage = false);
 private:
  std::ifstream stream_;
  Config config_;
  std::uint64_t offset_ = 0;
  bool consumed_ = false;
  std::vector<std::uint8_t> next_frame(bool& clean_eof);
};

std::string json_escape(const std::string& text);
void write_config(std::ostream& stream, const Config& config);
void write_metrics(std::ostream& stream, const Metrics& metrics);
void write_anomaly(std::ostream& stream, const Anomaly& anomaly);
}  // namespace flightdeck
