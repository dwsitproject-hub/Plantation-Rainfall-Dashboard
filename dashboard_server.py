"""Zero-dependency LAN server for the palm-oil weather dashboard."""

from __future__ import annotations

import csv
import gzip
import json
import mimetypes
import os
import socket
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from cumulative_rainfall_data import build_cumulative_rainfall
from forecast_changes import build_forecast_changes, build_last_week_accuracy
from rainfall_common import OVERALL_LABELS, TEMPERATURE_COMBINED_CSV, is_major_plantation_region, region_members
from soil_moisture_common import SOIL_DEPTH_CM

ROOT = Path(__file__).resolve().parent
INHOUSE_ROOT = ROOT / "rainfall-dashboard" / "dist"
PORT = 8080
FIXED_LAN_IP = "192.168.1.82"
DATA_FILES = (ROOT / "rainfall_combined.csv", ROOT / "soil_moisture_combined.csv", ROOT / TEMPERATURE_COMBINED_CSV.name)
PAYLOAD_CACHE = ROOT / ".dashboard_data_cache.json"
PAYLOAD_GZIP_CACHE = ROOT / ".dashboard_data_cache.json.gz"
PAYLOAD_SIGNATURE = ROOT / ".dashboard_data_cache.signature"
_payload_lock = threading.Lock()
_payload_memory: tuple[str, bytes, bytes] | None = None


def _data_signature() -> str:
    """Identify the exact CSV inputs without rereading their contents."""
    return "|".join(
        f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
        for path in DATA_FILES
    )


def save_payload_cache(payload: dict) -> tuple[bytes, bytes]:
    """Atomically save the validated dashboard payload and its compressed copy."""
    global _payload_memory
    signature = _data_signature()
    body = json.dumps(payload, separators=(",", ":")).encode()
    compressed = gzip.compress(body, compresslevel=6)
    temporary_files = []
    try:
        for target, content in (
            (PAYLOAD_CACHE, body),
            (PAYLOAD_GZIP_CACHE, compressed),
            (PAYLOAD_SIGNATURE, signature.encode()),
        ):
            temporary = target.with_name(target.name + ".new")
            temporary.write_bytes(content)
            temporary_files.append(temporary)
            os.replace(temporary, target)
    finally:
        for temporary in temporary_files:
            temporary.unlink(missing_ok=True)
    _payload_memory = (signature, body, compressed)
    return body, compressed


def payload_bytes() -> tuple[bytes, bytes]:
    """Return cached API bytes; rebuild only after one of the source CSVs changes."""
    global _payload_memory
    signature = _data_signature()
    if _payload_memory and _payload_memory[0] == signature:
        return _payload_memory[1], _payload_memory[2]
    with _payload_lock:
        if _payload_memory and _payload_memory[0] == signature:
            return _payload_memory[1], _payload_memory[2]
        if (PAYLOAD_CACHE.is_file() and PAYLOAD_GZIP_CACHE.is_file()
                and PAYLOAD_SIGNATURE.is_file()
                and PAYLOAD_SIGNATURE.read_text(encoding="utf-8") == signature):
            body = PAYLOAD_CACHE.read_bytes()
            compressed = PAYLOAD_GZIP_CACHE.read_bytes()
            _payload_memory = (signature, body, compressed)
            return body, compressed
        return save_payload_cache(build_payload())


def _read(name: str, value_field: str) -> list[dict]:
    rows = []
    with (ROOT / name).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                raw_date = row["date"].strip()
                try:
                    normalized_date = date.fromisoformat(raw_date).isoformat()
                except ValueError:
                    normalized_date = datetime.strptime(raw_date, "%d/%m/%Y").date().isoformat()
                rows.append({
                    "country": row["country"], "region": row["region"],
                    "date": normalized_date, "value": float(row[value_field]),
                    "type": row["data_type"], "lat": float(row["latitude"]),
                    "lon": float(row["longitude"]),
                    "retrieved": row.get("retrieved_at_utc", ""),
                })
            except (TypeError, ValueError):
                continue
    members = region_members("Malaysia", "Pennisular")
    grouped = defaultdict(list)
    retained = []
    for row in rows:
        if row["country"] == "Malaysia" and row["region"] in members:
            grouped[(row["date"], row["type"])].append(row)
        else:
            retained.append(row)
    for values in grouped.values():
        direct = [row for row in values if row["region"] == "Pennisular"]
        selected = direct or values
        retained.append({
            **selected[0], "region": "Pennisular",
            "value": sum(row["value"] for row in selected) / len(selected),
            "lat": sum(row["lat"] for row in selected) / len(selected),
            "lon": sum(row["lon"] for row in selected) / len(selected),
            "retrieved": max(row["retrieved"] for row in selected),
        })
    return retained


def build_payload() -> dict:
    rain = _read("rainfall_combined.csv", "rainfall_mm")
    soil = _read("soil_moisture_combined.csv", "soil_moisture_m3_m3")
    temperature = _read(TEMPERATURE_COMBINED_CSV.name, "temperature_mean_c")
    if not rain or not soil or not temperature:
        raise RuntimeError("Combined rainfall, soil-moisture, and temperature CSV files are required.")

    rain_last_actual = max(r["date"] for r in rain if r["type"] == "historical_actual")
    soil_last_actual = max(r["date"] for r in soil if r["type"] == "historical_actual")
    temperature_last_actual = max(r["date"] for r in temperature if r["type"] == "historical_actual")
    last_actual = rain_last_actual
    daily_start = (date.fromisoformat(last_actual) - timedelta(days=365)).isoformat()
    daily = defaultdict(dict)
    metric_daily = {"rain": [], "soil": [], "temperature": []}
    region_meta = {}
    monthly = defaultdict(lambda: {
        "rain": 0.0, "soil_sum": 0.0, "soil_n": 0,
        "temperature_sum": 0.0, "temperature_n": 0,
    })

    for metric, rows in (("rain", rain), ("soil", soil), ("temperature", temperature)):
        metric_cutoff = {
            "rain": rain_last_actual,
            "soil": soil_last_actual,
            "temperature": temperature_last_actual,
        }[metric]
        for r in rows:
            # A newly refreshed historical file can overlap an older forecast
            # file. Never allow forecast values on or before the latest actual
            # date to enter dashboard series or weekly aggregates.
            if r["type"] == "forecast" and r["date"] <= metric_cutoff:
                continue
            key = f'{r["country"]}|{r["region"]}'
            if metric == "rain" or key not in region_meta:
                region_meta[key] = {k: r[k] for k in ("country", "region", "lat", "lon")}
            if r["date"] >= daily_start or r["type"] == "forecast":
                daily[(key, r["date"])][metric] = r["value"]
                daily[(key, r["date"])]["type"] = r["type"]
                metric_daily[metric].append({"region": key, "date": r["date"],
                                             metric: r["value"], "type": r["type"]})
            if r["type"] == "historical_actual":
                bucket = monthly[(key, r["date"][:7])]
                if metric == "rain": bucket["rain"] += r["value"]
                elif metric == "soil":
                    bucket["soil_sum"] += r["value"]
                    bucket["soil_n"] += 1
                else:
                    bucket["temperature_sum"] += r["value"]
                    bucket["temperature_n"] += 1

    daily_rows = []
    for (region, day), values in sorted(daily.items()):
        if "rain" in values and "soil" in values and "temperature" in values:
            daily_rows.append({"region": region, "date": day, **values})
    monthly_rows = [
        {"region": key, "month": month, "rain": round(v["rain"], 1),
         "soil": round(v["soil_sum"] / v["soil_n"], 4) if v["soil_n"] else None,
         "temperature": (round(v["temperature_sum"] / v["temperature_n"], 1)
                         if v["temperature_n"] else None)}
        for (key, month), v in sorted(monthly.items())
    ]

    weekly = defaultdict(lambda: {
        "rain": 0.0, "soil_sum": 0.0, "soil_n": 0,
        "temperature_sum": 0.0, "temperature_n": 0,
    })
    for metric, rows in (("rain", rain), ("soil", soil), ("temperature", temperature)):
        for r in rows:
            day = date.fromisoformat(r["date"])
            iso_year, iso_week, _ = day.isocalendar()
            key = f'{r["country"]}|{r["region"]}'
            bucket = weekly[(key, iso_year, iso_week, r["type"])]
            if metric == "rain": bucket["rain"] += r["value"]
            elif metric == "soil":
                bucket["soil_sum"] += r["value"]
                bucket["soil_n"] += 1
            else:
                bucket["temperature_sum"] += r["value"]
                bucket["temperature_n"] += 1
    weekly_rows = [
        {"region": key, "year": year, "week": week,
         "weekStart": date.fromisocalendar(year, week, 1).isoformat(), "type": data_type,
         "rain": round(v["rain"], 1),
         "soil": round(v["soil_sum"] / v["soil_n"], 4) if v["soil_n"] else None,
         "temperature": (round(v["temperature_sum"] / v["temperature_n"], 1)
                         if v["temperature_n"] else None)}
        for (key, year, week, data_type), v in sorted(weekly.items())
    ]
    country_daily = defaultdict(lambda: {"rain": [], "soil": [], "temperature": [], "type": ""})
    for row in daily_rows:
        meta = region_meta[row["region"]]
        country = meta["country"]
        if not is_major_plantation_region(country, meta["region"]):
            continue
        bucket = country_daily[(country, row["date"])]
        bucket["rain"].append(row["rain"]); bucket["soil"].append(row["soil"])
        bucket["temperature"].append(row["temperature"])
        bucket["type"] = row["type"]
    for (country, day), values in sorted(country_daily.items()):
        daily_rows.append({"region": f"Overall {country}", "date": day,
            "rain": round(sum(values["rain"]) / len(values["rain"]), 2),
            "soil": round(sum(values["soil"]) / len(values["soil"]), 5),
            "temperature": round(sum(values["temperature"]) / len(values["temperature"]), 2),
            "type": values["type"]})

    def add_country_metric_rows(rows: list[dict], metric: str) -> list[dict]:
        result = list(rows)
        grouped = defaultdict(lambda: {"values": [], "types": []})
        for row in rows:
            meta = region_meta[row["region"]]
            country = meta["country"]
            if not is_major_plantation_region(country, meta["region"]):
                continue
            bucket = grouped[(country, row["date"])]
            bucket["values"].append(row[metric])
            bucket["types"].append(row["type"])
        for (country, day), values in sorted(grouped.items()):
            result.append({"region": f"Overall {country}", "date": day,
                           metric: round(sum(values["values"]) / len(values["values"]), 5),
                           "type": "forecast" if "forecast" in values["types"] else "historical_actual"})
        return result

    rain_daily_rows = add_country_metric_rows(metric_daily["rain"], "rain")
    soil_daily_rows = add_country_metric_rows(metric_daily["soil"], "soil")
    temperature_daily_rows = add_country_metric_rows(metric_daily["temperature"], "temperature")

    country_monthly = defaultdict(lambda: {"rain": [], "soil": [], "temperature": []})
    for row in monthly_rows:
        meta = region_meta[row["region"]]
        country = meta["country"]
        if not is_major_plantation_region(country, meta["region"]):
            continue
        country_monthly[(country, row["month"])]["rain"].append(row["rain"])
        if row["soil"] is not None: country_monthly[(country, row["month"])]["soil"].append(row["soil"])
        if row["temperature"] is not None: country_monthly[(country, row["month"])]["temperature"].append(row["temperature"])
    for (country, month), values in sorted(country_monthly.items()):
        monthly_rows.append({"region": f"Overall {country}", "month": month,
            "rain": round(sum(values["rain"]) / len(values["rain"]), 1),
            "soil": (round(sum(values["soil"]) / len(values["soil"]), 4)
                     if values["soil"] else None),
            "temperature": (round(sum(values["temperature"]) / len(values["temperature"]), 1)
                            if values["temperature"] else None)})

    country_weekly = defaultdict(lambda: {"rain": [], "soil": [], "temperature": []})
    for row in weekly_rows:
        meta = region_meta[row["region"]]
        country = meta["country"]
        if not is_major_plantation_region(country, meta["region"]):
            continue
        bucket = country_weekly[(country, row["year"], row["week"], row["type"])]
        bucket["rain"].append(row["rain"])
        if row["soil"] is not None: bucket["soil"].append(row["soil"])
        if row["temperature"] is not None: bucket["temperature"].append(row["temperature"])
    for (country, year, week, data_type), values in sorted(country_weekly.items()):
        weekly_rows.append({"region": f"Overall {country}", "year": year, "week": week,
            "weekStart": date.fromisocalendar(year, week, 1).isoformat(), "type": data_type,
            "rain": round(sum(values["rain"]) / len(values["rain"]), 1),
            "soil": (round(sum(values["soil"]) / len(values["soil"]), 4)
                     if values["soil"] else None),
            "temperature": (round(sum(values["temperature"]) / len(values["temperature"]), 1)
                            if values["temperature"] else None)})

    for country in sorted({m["country"] for m in region_meta.values()}):
        region_meta[f"Overall {country}"] = {"country": country,
            "region": OVERALL_LABELS[country], "lat": None, "lon": None}

    current_year = int(last_actual[:4])
    by_region_month = {(r["region"], r["month"]): r for r in monthly_rows}
    seasonality = []
    for region in sorted({r["region"] for r in monthly_rows}):
        for month in range(1, 13):
            current = by_region_month.get((region, f"{current_year}-{month:02d}"))
            past = [by_region_month.get((region, f"{year}-{month:02d}"))
                    for year in range(current_year - 10, current_year)]
            past = [r for r in past if r is not None]
            item = {"region": region, "month": month,
                    "currentYear": current_year, "baselineStart": current_year - 10,
                    "baselineEnd": current_year - 1}
            for metric, digits in (("rain", 1), ("soil", 4), ("temperature", 1)):
                values = [r[metric] for r in past if r[metric] is not None]
                item[f"{metric}Current"] = current[metric] if current else None
                item[f"{metric}Avg"] = round(sum(values) / len(values), digits) if values else None
                item[f"{metric}Min"] = round(min(values), digits) if values else None
                item[f"{metric}Max"] = round(max(values), digits) if values else None
            seasonality.append(item)

    forecasts = [r for r in daily_rows if r["type"] == "forecast"]
    summaries = []
    for key, meta in sorted(region_meta.items()):
        rows = [r for r in forecasts if r["region"] == key]
        summaries.append({**meta, "key": key,
            "forecastRain": round(sum(r["rain"] for r in rows), 1),
            "avgSoil": round(sum(r["soil"] for r in rows) / len(rows), 3) if rows else None,
            "avgTemperature": (round(sum(r["temperature"] for r in rows) / len(rows), 1)
                               if rows else None),
            "wetDays": sum(r["rain"] >= 10 for r in rows),
            "maxRain": round(max((r["rain"] for r in rows), default=0), 1)})
    soil_history_start = min(r["date"] for r in soil if r["type"] == "historical_actual")
    return {"generated": datetime.now().isoformat(timespec="seconds"), "lastActual": last_actual,
            "rainLastActual": rain_last_actual, "soilLastActual": soil_last_actual,
            "temperatureLastActual": temperature_last_actual,
            "forecastEnd": max(r["date"] for r in forecasts), "regions": summaries,
            "daily": daily_rows, "rainDaily": rain_daily_rows, "soilDaily": soil_daily_rows,
            "temperatureDaily": temperature_daily_rows,
            "monthly": monthly_rows, "weekly": weekly_rows,
            "seasonality": seasonality, "soilDepthCm": SOIL_DEPTH_CM,
            "soilModel": "ECMWF IFS", "soilHistoryStart": soil_history_start}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        elif Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2"}:
            self.send_header("Cache-Control", "public, max-age=3600")
        else:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        request_path = urlparse(self.path).path
        if request_path.startswith("/inhouse/"):
            relative_path = unquote(request_path.removeprefix("/inhouse/")) or "index.html"
            file_path = (INHOUSE_ROOT / relative_path).resolve()
            if not file_path.is_relative_to(INHOUSE_ROOT.resolve()) or not file_path.is_file():
                self.send_error(404, "File not found")
                return
            body = file_path.read_bytes()
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if request_path == "/inhouse":
            self.send_response(302)
            self.send_header("Location", "/inhouse/")
            self.end_headers()
            return
        if request_path == "/api/cumulative-rainfall":
            try:
                query = parse_qs(urlparse(self.path).query)
                body = json.dumps(build_cumulative_rainfall(
                    query.get("region", [""])[0],
                    int(query.get("startMonth", ["7"])[0]),
                    int(query.get("from", ["2015"])[0]),
                    int(query.get("to", ["2025"])[0]),
                ), separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            except (ValueError, KeyError) as exc:
                body = json.dumps({"error": str(exc)}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return
        if urlparse(self.path).path == "/api/last-week-forecast-accuracy":
            try:
                body = json.dumps(build_last_week_accuracy(), separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return
        if urlparse(self.path).path == "/api/forecast-changes":
            try:
                body = json.dumps(build_forecast_changes(), separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return
        if urlparse(self.path).path == "/api/data":
            try:
                body, compressed = payload_bytes()
                use_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
                if use_gzip:
                    body = compressed
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Vary", "Accept-Encoding")
                if use_gzip:
                    self.send_header("Content-Encoding", "gzip")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return
        if self.path == "/": self.path = "/dashboard.html"
        return super().do_GET()


def main() -> None:
    socket.setdefaulttimeout(30)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Dashboard ready: http://localhost:{PORT}")
    print(f"Company network: http://{FIXED_LAN_IP}:{PORT}")
    print("Keep this window open. Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
