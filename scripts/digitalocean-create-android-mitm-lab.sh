#!/usr/bin/env bash
set -euo pipefail

NAME="${DO_LAB_NAME:-android-mitm-lab}"
REGION="${DO_REGION:-nyc3}"
SIZE="${DO_SIZE:-s-4vcpu-8gb}"
IMAGE="${DO_IMAGE:-ubuntu-22-04-x64}"
SSH_KEYS="${DO_SSH_KEYS:-}"

if ! command -v doctl >/dev/null 2>&1; then
  echo "ERROR: doctl is required. Install and authenticate it first." >&2
  exit 1
fi

args=(
  compute droplet create "$NAME"
  --region "$REGION"
  --size "$SIZE"
  --image "$IMAGE"
  --enable-monitoring
  --wait
)

if [[ -n "$SSH_KEYS" ]]; then
  args+=(--ssh-keys "$SSH_KEYS")
fi

echo "Creating DigitalOcean Droplet: $NAME ($SIZE in $REGION)"
doctl "${args[@]}"

echo
echo "Validate nested KVM immediately after SSH:"
echo "  ./scripts/bootstrap-android-mitm-lab.sh --validate-only"
echo
echo "If /dev/kvm is missing or kvm-ok fails, destroy this Droplet."
