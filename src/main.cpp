#include "flightdeck.hpp"

#include <charconv>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <limits>
#include <locale>
#include <map>
#include <sstream>
#include <stdexcept>
#include <type_traits>

using namespace flightdeck;
#ifndef FLIGHTDECK_BUILD_FLAGS
#define FLIGHTDECK_BUILD_FLAGS "unrecorded; compile with the project Makefile to embed flags"
#endif
namespace {
class JsonReport {
 public:
  JsonReport(const std::string& path, const Config& config, const std::string& mode)
      : path_(path), temporary_(path + ".tmp"), out_(temporary_) {
    if (!out_) throw std::runtime_error("cannot write report: " + path);
    out_ << "{\"schema_version\":1,\"tool\":\"flightdeck\",\"mode\":" << json_escape(mode) << ",\"config\":";
    write_config(out_, config); out_ << ",\"anomalies\":[";
  }
  ~JsonReport() { if (!done_) std::remove(temporary_.c_str()); }
  void anomaly(const Anomaly& a) { if (!first_) out_ << ','; first_ = false; write_anomaly(out_, a); }
  void finish(const Metrics& metrics, const Recovery& r) {
    out_ << "],\"metrics\":"; write_metrics(out_, metrics);
    out_ << ",\"log\":{\"sealed\":" << (r.sealed ? "true" : "false")
         << ",\"recovered_prefix\":" << (r.recovered_prefix ? "true" : "false")
         << ",\"events\":" << r.events << ",\"valid_bytes\":" << r.valid_bytes << ",\"reason\":" << json_escape(r.reason) << "}}\n";
    out_.close(); if (!out_) throw std::runtime_error("report write failed");
    std::filesystem::rename(temporary_, path_); done_ = true;
  }
 private:
  std::string path_, temporary_;
  std::ofstream out_;
  bool first_ = true, done_ = false;
};

bool bounded_line(std::istream& input, std::string& line) {
  line.clear(); char c;
  while (input.get(c)) {
    if (c == '\n') { if (!line.empty() && line.back() == '\r') line.pop_back(); return true; }
    if (line.size() >= 512) throw std::runtime_error("CSV line exceeds 512 byte limit");
    line.push_back(c);
  }
  if (input.bad()) throw std::runtime_error("CSV read failed");
  if (!line.empty() && line.back() == '\r') line.pop_back();
  return !line.empty();
}
std::uint64_t number(const std::string& text) {
  std::uint64_t v = 0; const auto r = std::from_chars(text.data(), text.data() + text.size(), v);
  if (text.empty() || r.ec != std::errc{} || r.ptr != text.data() + text.size() || v > kMaxInteger)
    throw std::invalid_argument("invalid integer option: " + text);
  return v;
}
double decimal(const std::string& text) {
  return parse_decimal(text);
}
std::map<std::string, std::string> options(int argc, char** argv, int start) {
  std::map<std::string, std::string> opts;
  for (int i = start; i < argc; ++i) {
    std::string key = argv[i];
    if (key.rfind("--", 0) != 0) throw std::invalid_argument("expected option, got: " + key);
    std::string value;
    if (key == "--salvage") value = "true";
    else { if (++i == argc) throw std::invalid_argument("missing value for " + key); value = argv[i]; }
    if (!opts.emplace(key, value).second) throw std::invalid_argument("duplicate option: " + key);
  }
  return opts;
}
std::string take(std::map<std::string, std::string>& o, const char* key, const std::string& fallback = "") {
  auto it = o.find(key); if (it == o.end()) return fallback; auto v = it->second; o.erase(it); return v;
}
Config configuration(std::map<std::string, std::string>& o) {
  Config c;
  auto uint_opt = [&](const char* key, auto& target) {
    const auto s = take(o, key); if (s.empty()) return;
    const auto n = number(s);
    if (n > std::numeric_limits<std::decay_t<decltype(target)>>::max()) throw std::invalid_argument(std::string(key) + " overflows its type");
    target = static_cast<std::decay_t<decltype(target)>>(n);
  };
  auto real_opt = [&](const char* key, double& target) { const auto s = take(o, key); if (!s.empty()) target = decimal(s); };
  uint_opt("--lateness-ms", c.lateness_ms); uint_opt("--heartbeat-ms", c.heartbeat_ms); uint_opt("--future-skew-ms", c.future_skew_ms);
  uint_opt("--max-buffer", c.max_buffer); uint_opt("--max-vehicles", c.max_vehicles); uint_opt("--dedup-window", c.dedup_window);
  real_opt("--center-lat", c.center_lat); real_opt("--center-lon", c.center_lon); real_opt("--radius-m", c.radius_m);
  real_opt("--max-speed-mps", c.max_speed_mps); real_opt("--max-altitude-m", c.max_altitude_m); real_opt("--min-battery-pct", c.min_battery_pct);
  validate_config(c); return c;
}
void exhausted(const std::map<std::string, std::string>& opts) { if (!opts.empty()) throw std::invalid_argument("unknown or unsupported option: " + opts.begin()->first); }
void distinct(const std::string& a, const std::string& b) {
  if (std::filesystem::weakly_canonical(a) == std::filesystem::weakly_canonical(b)) throw std::invalid_argument("input and output paths must be distinct");
  if (std::filesystem::exists(a) && std::filesystem::exists(b) && std::filesystem::equivalent(a, b)) throw std::invalid_argument("input and output refer to same file");
}
void record(const std::string& csv, const std::string& log, const std::string& report_path, const Config& config) {
  distinct(csv, log); distinct(csv, report_path); distinct(csv, report_path + ".tmp"); distinct(log, report_path); distinct(log, report_path + ".tmp");
  std::ifstream input(csv); if (!input) throw std::runtime_error("cannot open CSV: " + csv);
  std::string line; if (!bounded_line(input, line) || line != kCsvHeader) throw std::runtime_error("CSV header does not match documented schema");
  Recorder writer(log, config); JsonReport report(report_path, config, "record");
  Processor p(config, [&](const Anomaly& a) { report.anomaly(a); });
  std::size_t line_number = 1;
  while (bounded_line(input, line)) {
    ++line_number; const auto e = parse_csv_line(line, line_number);
    writer.append(e); p.ingest(e);
  }
  writer.seal(); p.finish();
  Recovery recovery; recovery.sealed = true; recovery.events = p.metrics().received;
  recovery.valid_bytes = std::filesystem::file_size(log); report.finish(p.metrics(), recovery);
  std::cout << "Recorded " << p.metrics().received << " events; " << p.metrics().anomalies << " findings. Report: " << report_path << '\n';
}
void replay(const std::string& log, const std::string& path, bool salvage) {
  distinct(log, path); distinct(log, path + ".tmp");
  LogReader reader(log); JsonReport report(path, reader.config(), "replay");
  Processor p(reader.config(), [&](const Anomaly& a) { report.anomaly(a); });
  const auto recovery = reader.read([&](const Event& e) { p.ingest(e); }, salvage);
  p.finish(); report.finish(p.metrics(), recovery);
  std::cout << "Replayed " << recovery.events << " events; " << p.metrics().anomalies << " findings";
  if (recovery.recovered_prefix) std::cout << "; RECOVERED PREFIX ONLY: " << recovery.reason;
  std::cout << ". Report: " << path << '\n';
}
void benchmark(std::uint64_t count, const std::string& path, const Config& config) {
  if (count < 1 || count > 10000000) throw std::invalid_argument("benchmark events must be 1..10000000");
  Processor p(config);
  std::array<std::string, 16> names;
  for (std::size_t i = 0; i < names.size(); ++i) names[i] = "bench-" + std::to_string(i);
  const auto start = std::chrono::steady_clock::now();
  for (std::uint64_t i = 0; i < count; ++i) {
    Event e; e.arrival_ms = i * 20 + 10; e.event_ms = i * 20; e.vehicle = names[i % names.size()];
    e.sequence = i / names.size(); e.latitude = config.center_lat + double((i / 16) % 100) * 0.000001;
    e.longitude = config.center_lon; p.ingest(e);
  }
  p.finish();
  const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
  std::ofstream out(path); if (!out) throw std::runtime_error("cannot write benchmark report");
  out << std::setprecision(17) << "{\"schema_version\":1,\"workload\":\"synthetic processor-only; no CSV, recorder, or report I/O in timed region\",\"events\":"
      << count << ",\"vehicles\":16,\"elapsed_seconds\":" << elapsed << ",\"events_per_second\":" << double(count) / elapsed
      << ",\"compiler\":" << json_escape(__VERSION__) << ",\"build_flags\":" << json_escape(FLIGHTDECK_BUILD_FLAGS) << ",\"config\":";
  write_config(out, config); out << ",\"metrics\":"; write_metrics(out, p.metrics()); out << "}\n";
  out.close(); if (!out) throw std::runtime_error("benchmark report write failed");
  std::cout << "Synthetic processor benchmark: " << count << " events in " << elapsed << " s (" << double(count) / elapsed << " events/s).\n";
}
void usage() {
  std::cout << R"(Flightdeck — deterministic telemetry recorder and replay analysis

flightdeck record input.csv output.fdlog --report report.json [rules]
flightdeck replay input.fdlog --report replay.json [--salvage]
flightdeck benchmark --events 200000 --report bench.json [rules]

Rules (record and benchmark only; replay restores the log's original rules):
  --lateness-ms 250       --heartbeat-ms 2000     --future-skew-ms 100
  --max-buffer 4096       --max-vehicles 256      --dedup-window 256
  --center-lat 43.4723    --center-lon -80.5449    --radius-m 500
  --max-speed-mps 45      --max-altitude-m 120     --min-battery-pct 20

CSV is strict unquoted 9-column data; see examples/synthetic.csv.
--salvage stops at the first invalid frame and reports only the valid prefix.
This is a simulation engineering toolkit, not certified flight software.
)";
}
}  // namespace

int main(int argc, char** argv) {
  try {
    std::locale::global(std::locale::classic());
    if (argc < 2 || std::string(argv[1]) == "--help") { usage(); return argc < 2 ? 2 : 0; }
    const std::string command = argv[1];
    if (command == "record") {
      if (argc < 4) throw std::invalid_argument("record requires input CSV and output log");
      auto opts = options(argc, argv, 4); const auto path = take(opts, "--report", "report.json");
      const auto config = configuration(opts); exhausted(opts); record(argv[2], argv[3], path, config);
    } else if (command == "replay") {
      if (argc < 3) throw std::invalid_argument("replay requires input log");
      auto opts = options(argc, argv, 3); const auto path = take(opts, "--report", "replay.json");
      const bool salvage = take(opts, "--salvage") == "true"; exhausted(opts); replay(argv[2], path, salvage);
    } else if (command == "benchmark") {
      auto opts = options(argc, argv, 2); const auto path = take(opts, "--report", "benchmark.json");
      const auto count = number(take(opts, "--events", "200000")); const auto config = configuration(opts);
      exhausted(opts); benchmark(count, path, config);
    } else throw std::invalid_argument("unknown command: " + command);
    return 0;
  } catch (const std::exception& error) { std::cerr << "flightdeck: " << error.what() << '\n'; return 1; }
}
