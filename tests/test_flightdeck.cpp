#include "flightdeck.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <sstream>
#include <stdexcept>

using namespace flightdeck;
namespace {
int assertions = 0;
#define CHECK(x) do { ++assertions; if (!(x)) throw std::runtime_error(std::string(__FILE__) + ":" + std::to_string(__LINE__) + " assertion " + std::to_string(assertions) + " failed: " #x); } while (0)
template<class F> void fails(F fn) { bool threw = false; try { fn(); } catch (const std::exception&) { threw = true; } CHECK(threw); }
Event sample(std::uint64_t time, std::uint64_t seq, const std::string& name = "alpha", State state = State::Ground) {
  Event e; e.arrival_ms = time + 100; e.event_ms = time; e.sequence = seq; e.vehicle = name;
  e.state = state; e.latitude = 43.4723; e.longitude = -80.5449; return e;
}
std::string all(const std::string& path) { std::ifstream f(path, std::ios::binary); return {std::istreambuf_iterator<char>(f), {}}; }
void save(const std::string& path, const std::string& bytes) { std::ofstream f(path, std::ios::binary); f.write(bytes.data(), static_cast<std::streamsize>(bytes.size())); }
std::uint32_t length(const std::string& s, std::size_t at) {
  std::uint32_t n = 0; for (int i = 0; i < 4; ++i) n |= std::uint32_t(static_cast<unsigned char>(s.at(at + std::size_t(i)))) << (i * 8); return n;
}
std::string analyze(const std::vector<Event>& events) {
  Config c; c.lateness_ms = 1000;
  std::ostringstream findings; Processor p(c, [&](const Anomaly& a) { write_anomaly(findings, a); });
  for (const auto& event : events) {
    p.ingest(event);
  }
  p.finish();
  write_metrics(findings, p.metrics());
  return findings.str();
}
void parsing() {
  auto e = parse_csv_line("1,0,alpha-1,0,ground,43.4723,-80.5449,0,100", 2);
  CHECK(e.vehicle == "alpha-1"); CHECK(e.sequence == 0);
  for (const std::string bad : {
      "1,0,alpha,0,ground,nan,0,0,100", "1,0,alpha,0,ground,inf,0,0,100",
      "1,0,alpha,0,ground,1e999,0,0,100", "1,0,alpha,0,ground,91,0,0,100",
      "1,0,alpha,0,ground,0,181,0,100", "1,0,alpha,0,ground,0,0,0,101",
      "1,0,alpha,0,ground,0,0,-1001,100", "1,0,alpha,0,ground,0,0,0,100,",
      "1,0,alpha,0,ground,0,0,0", "1,0,,0,ground,0,0,0,100",
      "1,0,alpha,0,flying,0,0,0,100", "-1,0,alpha,0,ground,0,0,0,100",
      "1,0,alpha,0,ground,0x1p2,0,0,100", "1,0,alpha,0,ground, 2,0,0,100",
      "1,0,a/b,0,ground,0,0,0,100", "1,0,alpha,9007199254740992,ground,0,0,0,100"})
    fails([&] { parse_csv_line(bad, 9); });
  fails([&] { parse_csv_line(std::string(513, 'a'), 4); });
  Config c; c.max_buffer = 0; fails([&] { Processor p(c); });
  c = {}; c.max_vehicles = 4096; c.dedup_window = 65536; fails([&] { Processor p(c); });
  c = {}; c.radius_m = std::numeric_limits<double>::quiet_NaN(); fails([&] { Processor p(c); });
  e.latitude = std::numeric_limits<double>::infinity(); fails([&] { Processor p; p.ingest(e); });
}
void ordering() {
  std::vector<Event> ordered{sample(10, 0), sample(20, 1, "alpha", State::Armed),
      sample(30, 2, "alpha", State::Airborne), sample(40, 3, "alpha", State::Landing)};
  for (auto& e : ordered) e.arrival_ms = 100;
  const auto baseline = analyze(ordered);
  // Every permutation must produce identical results when all samples fit the reorder window.
  std::vector<int> permutation{0,1,2,3};
  do { std::vector<Event> input; for (const auto i : permutation) input.push_back(ordered[std::size_t(i)]); CHECK(analyze(input) == baseline); }
  while (std::next_permutation(permutation.begin(), permutation.end()));
  Config c; c.lateness_ms = 10; Processor p(c);
  p.ingest(sample(100, 0)); auto late = sample(89, 1); late.arrival_ms = 201; p.ingest(late);
  auto boundary = sample(90, 2); boundary.arrival_ms = 202; p.ingest(boundary); p.finish();
  CHECK(p.metrics().late_drops == 1); CHECK(p.metrics().emitted == 2);
  p.finish(); fails([&] { p.ingest(sample(500, 5)); });
}
void dedup_and_capacity() {
  Config c; c.lateness_ms = 1000; c.dedup_window = 1; c.max_buffer = 3;
  Processor p(c); p.ingest(sample(100, 1)); p.ingest(sample(101, 2));
  auto repeat = sample(100, 1); repeat.arrival_ms = 202; p.ingest(repeat);
  CHECK(p.metrics().duplicate_drops == 1); CHECK(p.metrics().dedup_evictions == 1); // Original still queued, evicted from recent history.
  p.ingest(sample(103, 3)); p.ingest(sample(104, 4)); CHECK(p.metrics().capacity_drops == 1);
  CHECK(p.buffered() == 3); p.finish(); CHECK(p.metrics().emitted == 3); CHECK(p.metrics().peak_buffer == 3);
  c = {}; c.max_vehicles = 1; Processor one(c);
  one.ingest(sample(0, 0, "one")); one.ingest(sample(1, 0, "two")); one.finish();
  CHECK(one.metrics().vehicle_drops == 1); CHECK(one.metrics().peak_vehicles == 1);
  c = {}; c.lateness_ms = 0; c.dedup_window = 1; Processor expired(c);
  expired.ingest(sample(0, 1)); expired.ingest(sample(1, 2)); expired.ingest(sample(2, 1)); expired.finish();
  CHECK(expired.metrics().emitted == 3); CHECK(expired.metrics().anomaly_counts.at("sequence_regression") == 1);
}
void rules() {
  Config c; c.lateness_ms = 0; c.heartbeat_ms = 20;
  Processor p(c); p.ingest(sample(0, 0)); p.ingest(sample(30, 0, "bravo"));
  auto fault = sample(40, 1, "alpha", State::Airborne);
  fault.latitude += 0.01; fault.altitude_m = 200; fault.battery_pct = 10; p.ingest(fault); p.finish();
  for (const auto* code : {"heartbeat_stale", "heartbeat_recovered", "telemetry_gap", "velocity_limit", "geofence", "altitude_limit", "low_battery", "state_transition"})
    CHECK(p.metrics().anomaly_counts.count(code) == 1);
  Processor clocks; auto first = sample(100, 0); clocks.ingest(first);
  auto back = sample(101, 1); back.arrival_ms = 199; clocks.ingest(back);
  auto future = sample(1000, 2); future.arrival_ms = 201; clocks.ingest(future); clocks.finish();
  CHECK(clocks.metrics().clock_drops == 2); CHECK(clocks.metrics().emitted == 1);
  Processor simult(c); simult.ingest(sample(0, 0)); auto moved = sample(0, 1); moved.longitude += 0.001;
  simult.ingest(moved); simult.finish(); CHECK(simult.metrics().anomaly_counts.at("position_time_conflict") == 1);
}
void logging(const std::string& dir) {
  CHECK(crc32(reinterpret_cast<const std::uint8_t*>("123456789"), 9) == 0xcbf43926U);
  const std::string log = dir + "/test.fdlog", bad = dir + "/bad.fdlog";
  Config config; config.lateness_ms = 987; config.radius_m = 765;
  std::vector<Event> original{sample(0, 0), sample(10, 1), sample(20, 2)};
  { Recorder w(log, config); for (const auto& e : original) w.append(e); w.seal(); w.seal(); fails([&] { w.append(original[0]); }); }
  LogReader reader(log); CHECK(reader.config().lateness_ms == 987); CHECK(reader.config().radius_m == 765);
  std::vector<Event> restored;
  const auto result = reader.read([&](const Event& e) { restored.push_back(e); });
  CHECK(result.sealed); CHECK(result.events == 3); CHECK(restored.size() == 3); CHECK(analyze(original) == analyze(restored));
  fails([&] { reader.read([](const Event&){}); });
  const auto bytes = all(log); const auto config_end = std::size_t(8 + 8 + length(bytes, 8));
  // Every single-byte truncation after intact configuration is strict-failing and prefix-salvageable.
  for (std::size_t i = config_end; i < bytes.size(); ++i) {
    save(bad, bytes.substr(0, i));
    fails([&] { LogReader r(bad); r.read([](const Event&){}); });
    LogReader r(bad); const auto recovered = r.read([](const Event&){}, true);
    CHECK(recovered.recovered_prefix); CHECK(!recovered.sealed); CHECK(recovered.events <= 3); CHECK(recovered.valid_bytes <= i);
  }
  auto corrupt = bytes; const auto first_event_end = config_end + 8 + length(bytes, config_end);
  corrupt[first_event_end + 10] ^= 0x01; save(bad, corrupt);
  fails([&] { LogReader r(bad); r.read([](const Event&){}); });
  LogReader salvage(bad); const auto prefix = salvage.read([](const Event&){}, true);
  CHECK(prefix.events == 1); CHECK(prefix.reason == "frame checksum mismatch");
  corrupt = bytes; corrupt[config_end] = static_cast<char>(255); corrupt[config_end + 1] = static_cast<char>(255); save(bad, corrupt);
  LogReader oversized(bad); CHECK(oversized.read([](const Event&){}, true).events == 0);
  save(bad, bytes + "garbage"); fails([&] { LogReader r(bad); r.read([](const Event&){}); });
  LogReader sink_error(log); fails([&] { sink_error.read([](const Event&) { throw std::runtime_error("sink failed"); }, true); });
  corrupt = bytes; corrupt[14] ^= 1; save(bad, corrupt); fails([&] { LogReader r(bad); });
  save(bad, "sentinel"); config.max_buffer = 0; fails([&] { Recorder w(bad, config); }); CHECK(all(bad) == "sentinel");
}
}  // namespace
int main() {
  const auto dir = (std::filesystem::temp_directory_path() / ("flightdeck-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()))).string();
  std::filesystem::create_directory(dir);
  try { parsing(); ordering(); dedup_and_capacity(); rules(); logging(dir); std::filesystem::remove_all(dir);
    std::cout << "PASS: " << assertions << " assertions; parser, state machine, reorder, dedup, bounds, CRC, all-byte truncation recovery\n"; return 0;
  } catch (const std::exception& e) { std::filesystem::remove_all(dir); std::cerr << e.what() << '\n'; return 1; }
}
