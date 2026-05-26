import pandas as pd
from flask import Flask, render_template, jsonify
from prometheus_client import Gauge, generate_latest
from flask import Response
import psutil
import csv
import os
from datetime import datetime

app = Flask(__name__)

LOG_FILE = "metrics.csv"
cpu_gauge = Gauge('system_cpu_usage', 'CPU Usage Percentage')
memory_gauge = Gauge('system_memory_usage', 'Memory Usage Percentage')
disk_gauge = Gauge('system_disk_usage', 'Disk Usage Percentage')

# Create CSV file if not exists
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Time", "CPU", "Memory", "Disk"])

def get_metrics():
    cpu = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    cpu_gauge.set(cpu)
    memory_gauge.set(memory)
    disk_gauge.set(disk)

    timestamp = datetime.now().strftime("%H:%M:%S")

    with open(LOG_FILE, mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([timestamp, cpu, memory, disk])

    return {
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "time": timestamp
    }

@app.route("/")
def dashboard():
    return render_template("index.html")

@app.route("/metrics")
def metrics():
    return jsonify(get_metrics())

@app.route('/prometheus')
def prometheus_metrics():
    return Response(
        generate_latest(),
        mimetype='text/plain'
    )

@app.route("/history")
def history():

    data = pd.read_csv(LOG_FILE)

    return render_template(
        "history.html",
        times=data["Time"].tolist(),
        cpu=data["CPU"].tolist(),
        memory=data["Memory"].tolist(),
        disk=data["Disk"].tolist()
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)     