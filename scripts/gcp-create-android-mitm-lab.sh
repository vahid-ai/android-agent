#!/usr/bin/env bash
set -euo pipefail

NAME="${NAME:-android-mitm-lab}"
ZONE="${ZONE:-us-central1-a}"
MACHINE_TYPE="${MACHINE_TYPE:-n2-standard-2}"
IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2204-lts}"
IMAGE_PROJECT="${IMAGE_PROJECT:-ubuntu-os-cloud}"
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-100GB}"
BOOT_DISK_TYPE="${BOOT_DISK_TYPE:-pd-standard}"
PROVISIONING_MODEL="${PROVISIONING_MODEL:-SPOT}"

gcloud compute instances create "$NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --image-family="$IMAGE_FAMILY" \
  --image-project="$IMAGE_PROJECT" \
  --boot-disk-size="$BOOT_DISK_SIZE" \
  --boot-disk-type="$BOOT_DISK_TYPE" \
  --enable-nested-virtualization \
  --min-cpu-platform="Intel Haswell" \
  --provisioning-model="$PROVISIONING_MODEL" \
  --instance-termination-action=STOP

cat <<EOF
Created $NAME in $ZONE.

Next:
  gcloud compute ssh "$NAME" --zone="$ZONE"
  git clone <this-repo-url>
  cd <repo>
  ./scripts/bootstrap-android-mitm-lab.sh
  ./scripts/run-mitm-lab.sh
EOF
