"""Source-to-visualization provenance and report-only fallback tests."""
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/render_report.py"
SOURCE = ROOT / "docs/report.json"
FIXTURE = ROOT / "examples/synthetic.csv"


class ReportTests(unittest.TestCase):
    def render(self, source, output, *options, expected=0):
        result = subprocess.run([sys.executable, str(SCRIPT), str(source), str(output), *map(str, options)], capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stderr)
        if expected:
            return None
        html = output.read_text()
        payload = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(payload)
        return json.loads(payload.group(1)), html

    def test_actual_fixture_values_and_projection_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            data, html = self.render(SOURCE, Path(temp) / "report.html")
        telemetry = data["telemetry"]
        self.assertTrue(telemetry["synthetic"])
        self.assertEqual(telemetry["sha256"], hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        csv_rows = list(csv.DictReader(io.StringIO(FIXTURE.read_text())))
        self.assertEqual(len(telemetry["samples"]), len(csv_rows))
        center = data["report"]["config"]
        for row, plotted in zip(csv_rows, telemetry["samples"]):
            self.assertEqual(plotted["v"], row["vehicle"])
            self.assertEqual(plotted["t"], int(row["event_ms"]))
            self.assertEqual(plotted["arrival"], int(row["arrival_ms"]))
            self.assertEqual(plotted["seq"], int(row["sequence"]))
            self.assertEqual(plotted["lat"], float(row["latitude"]))
            self.assertEqual(plotted["lon"], float(row["longitude"]))
            self.assertEqual(plotted["alt"], float(row["altitude_m"]))
            self.assertEqual(plotted["battery"], float(row["battery_pct"]))
            self.assertAlmostEqual(plotted["y"], math.radians(plotted["lat"] - center["center_lat"]) * 6371008.8, places=4)
        self.assertIn("without interpolation", html)
        self.assertNotIn(str(ROOT), html)

    def test_report_without_fixture_remains_self_contained(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "other.json"
            source.write_bytes(SOURCE.read_bytes())
            data, html = self.render(source, Path(temp) / "report.html")
            self.assertIsNone(data["telemetry"])
            self.assertIn('id="missionSection" hidden', html)
            self.assertEqual(data["report"], json.loads(SOURCE.read_text()))

    def test_explicit_fixture_supports_other_report_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "other.json"
            source.write_bytes(SOURCE.read_bytes())
            data, _ = self.render(source, Path(temp) / "report.html", "--fixture", FIXTURE)
            self.assertEqual(len(data["telemetry"]["samples"]), 721)

    def test_telemetry_can_be_explicitly_omitted(self):
        with tempfile.TemporaryDirectory() as temp:
            data, _ = self.render(SOURCE, Path(temp) / "report.html", "--without-telemetry")
            self.assertIsNone(data["telemetry"])

    def test_mismatched_packet_count_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "mismatch.json"
            report = json.loads(SOURCE.read_text())
            report["metrics"]["received"] += 1
            source.write_text(json.dumps(report))
            self.render(source, Path(temp) / "report.html", "--fixture", FIXTURE, expected=1)

    def test_finding_missing_from_fixture_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "mismatch.json"
            report = json.loads(SOURCE.read_text())
            report["anomalies"][0]["sequence"] = 999999
            source.write_text(json.dumps(report))
            self.render(source, Path(temp) / "report.html", "--fixture", FIXTURE, expected=1)

    def test_nonfinite_source_coordinates_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "bad.csv"
            rows = list(csv.DictReader(io.StringIO(FIXTURE.read_text())))
            rows[0]["latitude"] = "NaN"
            with fixture.open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            self.render(SOURCE, Path(temp) / "report.html", "--fixture", fixture, expected=1)


if __name__ == "__main__":
    unittest.main()
