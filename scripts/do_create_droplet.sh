#!/usr/bin/env bash
# Create the benchmark droplet in San Francisco (SFO3), next to the US-West APIs.
# Needs: doctl (https://docs.digitalocean.com/reference/doctl/how-to/install/),
#        `doctl auth init` with DIGITALOCEAN_API_TOKEN, and an SSH key uploaded to your team.
# Usage: scripts/do_create_droplet.sh [name] [ssh-key-fingerprint-or-id]
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:-jevbench}"
KEY="${2:-$(doctl compute ssh-key list --format ID --no-header | head -n1)}"
SIZE="${DO_SIZE:-s-4vcpu-8gb}"      # 4 vCPU / 8 GB, $48/mo, billed per second
REGION="${DO_REGION:-sfo3}"
IMAGE="${DO_IMAGE:-ubuntu-24-04-x64}"

doctl compute droplet create "$NAME" \
  --region "$REGION" --size "$SIZE" --image "$IMAGE" \
  --ssh-keys "$KEY" --user-data-file deploy/cloud-init.yaml \
  --tag-names jevbench --enable-monitoring --wait \
  --format ID,Name,PublicIPv4,Region,Status

IP=$(doctl compute droplet get "$NAME" --format PublicIPv4 --no-header)
cat <<MSG

Droplet $NAME is up at $IP (cloud-init takes ~2 min to finish installing Docker).
Next:
  ssh root@$IP
  git clone <this repo> jevbench && cd jevbench
  cp .env.example .env && nano .env            # paste keys
  mkdir -p refsol && nano refsol/refsol.py      # MedAgentBench grader (see README)
  docker compose up -d --build                  # FHIR server + dashboard -> http://$IP:8000
  docker compose run --rm runner probe          # network floor per provider from SFO3
  docker compose run --rm runner                # run the whole matrix
MSG
