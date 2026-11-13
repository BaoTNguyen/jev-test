#!/usr/bin/env bash
# Create a DigitalOcean droplet that runs ONLY the MedAgentBench FHIR server, with a cloud
# firewall that allows port 8080 (and SSH) from this machine's public IP only.
# Cost: s-4vcpu-8gb in SFO3 = $0.071/hour; delete it after the hackathon (command printed at the end).
# Needs DIGITALOCEAN_API_TOKEN in .env. No doctl or SSH key required.
set -euo pipefail
cd "$(dirname "$0")/.."
TOKEN=$(grep -E '^DIGITALOCEAN_API_TOKEN=' .env | cut -d= -f2-)
[ -n "$TOKEN" ] || { echo "DIGITALOCEAN_API_TOKEN missing in .env"; exit 1; }
MYIP=$(curl -s --max-time 10 https://api.ipify.org)
echo "your public IP: $MYIP (only this IP will reach the FHIR server)"

uv run --no-sync python - "$TOKEN" "$MYIP" <<'EOF'
import json, sys, time, urllib.request

token, myip = sys.argv[1], sys.argv[2]
H = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def api(method, path, body=None):
    req = urllib.request.Request(f"https://api.digitalocean.com/v2{path}", method=method, headers=H,
                                 data=json.dumps(body).encode() if body else None)
    with urllib.request.urlopen(req) as r:
        return json.load(r) if r.length != 0 else {}

existing = api("GET", "/droplets?tag_name=jevbench")["droplets"]
d = next((x for x in existing if x["name"] == "jevbench-fhir"), None)
if d:
    print("droplet jevbench-fhir already exists:", d["id"])
else:
    d = api("POST", "/droplets", {
        "name": "jevbench-fhir", "region": "sfo3", "size": "s-4vcpu-8gb", "image": "ubuntu-24-04-x64",
        "tags": ["jevbench"], "monitoring": True,
        "user_data": open("deploy/fhir-cloud-init.yaml", encoding="utf-8").read(),
    })["droplet"]
    print("created droplet", d["id"])

fw = next((f for f in api("GET", "/firewalls")["firewalls"] if f["name"] == "jevbench-fhir"), None)
rules = {
    "name": "jevbench-fhir",
    "inbound_rules": [
        {"protocol": "tcp", "ports": "8080", "sources": {"addresses": [f"{myip}/32"]}},
        {"protocol": "tcp", "ports": "22", "sources": {"addresses": [f"{myip}/32"]}},
    ],
    "outbound_rules": [
        {"protocol": "tcp", "ports": "all", "destinations": {"addresses": ["0.0.0.0/0", "::/0"]}},
        {"protocol": "udp", "ports": "all", "destinations": {"addresses": ["0.0.0.0/0", "::/0"]}},
        {"protocol": "icmp", "destinations": {"addresses": ["0.0.0.0/0", "::/0"]}},
    ],
    "droplet_ids": [d["id"]],
}
if fw:
    api("PUT", f"/firewalls/{fw['id']}", rules)
    print("updated firewall", fw["id"])
else:
    print("created firewall", api("POST", "/firewalls", rules)["firewall"]["id"])

for _ in range(60):
    d = api("GET", f"/droplets/{d['id']}")["droplet"]
    ips = [n["ip_address"] for n in d["networks"]["v4"] if n["type"] == "public"]
    if d["status"] == "active" and ips:
        break
    time.sleep(5)
ip = ips[0]
print(f"\nDroplet active at {ip}. The FHIR server needs ~5-10 more minutes (Docker install + image pull + startup).")
print(f"Then set in your shell (not .env, which keeps the Docker path):  FHIR_API_BASE=http://{ip}:8080/fhir/")
print(f"Check it with:  curl http://{ip}:8080/fhir/metadata")
print(f"Delete later:   curl -X DELETE -H 'Authorization: Bearer $DIGITALOCEAN_API_TOKEN' https://api.digitalocean.com/v2/droplets/{d['id']}")
EOF
