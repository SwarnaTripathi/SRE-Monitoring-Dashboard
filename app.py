"""
SRE Monitoring Dashboard — Flask Application
=============================================
A real-time system observability platform that exposes host-level telemetry
via a web dashboard and Prometheus-compatible metrics endpoint.

Designed to demonstrate core SRE principles:
  • Monitoring & Alerting
  • Incident Detection via threshold-based alerts
  • Observability through structured logging
  • Service Level Indicators (SLIs) tracking

Author : Swarna Tripathi
License: MIT
"""

import os
import csv
import time
import logging
import platform
from datetime import datetime, timezone
from functools import wraps

import psutil
import pandas as pd
from flask import Flask, Response, render_template, jsonify, request
from prometheus_client import (
    Gauge, Counter, Histogram, Summary,
    generate_latest, CONTENT_TYPE_LATEST,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LOG_FILE = os.getenv("METRICS_LOG", "metrics.csv")
ALERT_CPU_THRESHOLD = int(os.getenv("ALERT_CPU_THRESHOLD", 80))
ALERT_MEMORY_THRESHOLD = int(os.getenv("ALERT_MEMORY_THRESHOLD", 90))
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", 3))  # seconds (frontend)

# ---------------------------------------------------------------------------
# Logging — structured output for production observability
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("sre-dashboard")

# ---------------------------------------------------------------------------
# Flask Application
# ---------------------------------------------------------------------------
app = Flask(__name__)

# ---------------------------------------------------------------------------
# Prometheus Metrics — following naming conventions from
# https://prometheus.io/docs/practices/naming/
# ---------------------------------------------------------------------------

# System gauges
cpu_gauge = Gauge("system_cpu_usage_percent", "Current CPU utilisation (%)")
memory_gauge = Gauge("system_memory_usage_percent", "Current memory utilisation (%)")
disk_gauge = Gauge("system_disk_usage_percent", "Current disk utilisation (%)")
network_sent_gauge = Gauge("system_network_bytes_sent_total", "Total bytes sent over network")
network_recv_gauge = Gauge("system_network_bytes_recv_total", "Total bytes received over network")
swap_gauge = Gauge("system_swap_usage_percent", "Current swap utilisation (%)")
open_files_gauge = Gauge("system_open_file_descriptors", "Number of open file descriptors")
process_count_gauge = Gauge("system_process_count", "Total running processes")
boot_time_gauge = Gauge("system_boot_time_seconds", "System boot time (UNIX epoch)")

# Application metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
request_latency = Histogram(
    "http_request_duration_seconds",
    "Request latency in seconds",
    ["endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
active_alerts = Gauge("active_alerts_total", "Number of currently active alerts")

# ---------------------------------------------------------------------------
# Middleware — Security headers & request instrumentation
# ---------------------------------------------------------------------------

@app.before_request
def start_timer():
    """Record the request start time for latency measurement."""
    request._start_time = time.perf_counter()


@app.after_request
def apply_security_headers(response):
    """Inject security headers on every response (OWASP best-practice)."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"

    # Record request metrics
    latency = time.perf_counter() - getattr(request, "_start_time", time.perf_counter())
    endpoint = request.endpoint or "unknown"
    http_requests_total.labels(
        method=request.method,
        endpoint=endpoint,
        status=response.status_code,
    ).inc()
    request_latency.labels(endpoint=endpoint).observe(latency)

    return response


# ---------------------------------------------------------------------------
# CSV Initialisation
# ---------------------------------------------------------------------------
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, mode="w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Timestamp", "CPU", "Memory", "Disk", "Net_Sent_MB", "Net_Recv_MB", "Swap", "Processes"])
    logger.info("Created metrics log file: %s", LOG_FILE)


# ---------------------------------------------------------------------------
# Core Metrics Collection
# ---------------------------------------------------------------------------
def collect_metrics() -> dict:
    """
    Sample host-level telemetry and update Prometheus gauges.

    Returns a dict with the latest snapshot — consumed by the JSON API
    and written to the CSV log for historical analysis.
    """
    cpu = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    swap = psutil.swap_memory()
    procs = len(psutil.pids())

    # Update Prometheus gauges
    cpu_gauge.set(cpu)
    memory_gauge.set(memory.percent)
    disk_gauge.set(disk.percent)
    network_sent_gauge.set(net.bytes_sent)
    network_recv_gauge.set(net.bytes_recv)
    swap_gauge.set(swap.percent)
    process_count_gauge.set(procs)
    boot_time_gauge.set(psutil.boot_time())

    # Alerting logic
    alerts = []
    if cpu > ALERT_CPU_THRESHOLD:
        alerts.append(f"CPU usage critical: {cpu}% > {ALERT_CPU_THRESHOLD}%")
        logger.warning("ALERT — CPU usage at %.1f%% (threshold: %d%%)", cpu, ALERT_CPU_THRESHOLD)
    if memory.percent > ALERT_MEMORY_THRESHOLD:
        alerts.append(f"Memory usage critical: {memory.percent}% > {ALERT_MEMORY_THRESHOLD}%")
        logger.warning("ALERT — Memory usage at %.1f%% (threshold: %d%%)", memory.percent, ALERT_MEMORY_THRESHOLD)
    active_alerts.set(len(alerts))

    timestamp = datetime.now().strftime("%H:%M:%S")
    net_sent_mb = round(net.bytes_sent / (1024 * 1024), 2)
    net_recv_mb = round(net.bytes_recv / (1024 * 1024), 2)

    # Append to CSV log
    try:
        with open(LOG_FILE, mode="a", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([timestamp, cpu, memory.percent, disk.percent, net_sent_mb, net_recv_mb, swap.percent, procs])
    except IOError as exc:
        logger.error("Failed to write metrics to CSV: %s", exc)

    return {
        "cpu": cpu,
        "memory": memory.percent,
        "disk": disk.percent,
        "swap": swap.percent,
        "net_sent_mb": net_sent_mb,
        "net_recv_mb": net_recv_mb,
        "processes": procs,
        "memory_total_gb": round(memory.total / (1024**3), 2),
        "memory_used_gb": round(memory.used / (1024**3), 2),
        "disk_total_gb": round(disk.total / (1024**3), 2),
        "disk_used_gb": round(disk.used / (1024**3), 2),
        "time": timestamp,
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    """Serve the real-time monitoring dashboard."""
    return render_template(
        "index.html",
        cpu_threshold=ALERT_CPU_THRESHOLD,
        memory_threshold=ALERT_MEMORY_THRESHOLD,
        scrape_interval=SCRAPE_INTERVAL * 1000,
        hostname=platform.node(),
    )


@app.route("/metrics")
def metrics_json():
    """JSON API — returns the latest system telemetry snapshot."""
    return jsonify(collect_metrics())


@app.route("/prometheus")
def prometheus_metrics():
    """Prometheus-compatible scrape endpoint (OpenMetrics format)."""
    # Ensure gauges are up-to-date before scrape
    collect_metrics()
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/history")
def history():
    """Render the historical metrics page from CSV data."""
    try:
        data = pd.read_csv(LOG_FILE)
        return render_template(
            "history.html",
            times=data["Timestamp"].tolist(),
            cpu=data["CPU"].tolist(),
            memory=data["Memory"].tolist(),
            disk=data["Disk"].tolist(),
        )
    except Exception as exc:
        logger.error("Failed to read history: %s", exc)
        return render_template(
            "history.html",
            times=[], cpu=[], memory=[], disk=[],
        )


@app.route("/health")
def health_check():
    """
    Liveness probe — confirms the application process is running.
    Used by container orchestrators (K8s, Docker) for health checks.
    """
    return jsonify({"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}), 200


@app.route("/ready")
def readiness_check():
    """
    Readiness probe — confirms the app can serve traffic.
    Validates that critical dependencies (CSV log, psutil) are available.
    """
    checks = {}
    try:
        psutil.cpu_percent(interval=0)
        checks["psutil"] = "ok"
    except Exception:
        checks["psutil"] = "fail"

    try:
        os.access(LOG_FILE, os.W_OK) or not os.path.exists(LOG_FILE)
        checks["csv_log"] = "ok"
    except Exception:
        checks["csv_log"] = "fail"

    all_ok = all(v == "ok" for v in checks.values())
    return jsonify({
        "ready": all_ok,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), 200 if all_ok else 503


@app.route("/system")
def system_info():
    """Return static system metadata (useful for asset inventory)."""
    return jsonify({
        "hostname": platform.node(),
        "os": platform.system(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "total_memory_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "total_disk_gb": round(psutil.disk_usage("/").total / (1024**3), 2),
        "boot_time": datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting SRE Monitoring Dashboard on port 5000")
    logger.info("Host: %s | OS: %s | CPUs: %d | Memory: %.1f GB",
                platform.node(), platform.system(),
                psutil.cpu_count(), psutil.virtual_memory().total / (1024**3))
    app.run(host="0.0.0.0", port=5000, debug=True)