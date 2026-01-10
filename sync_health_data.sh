#!/bin/bash

trap "exit" INT
set -x

# Configuration
DEST_DIR="$HOME/GarminDBSync"

# Source Directories
SRC1="/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/HealthData/DBs/"
SRC2="/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/tree_home/HealthData/DBs/"

# Destination Subdirectories
DEST1="$DEST_DIR/joel/HealthData/DBs/"
DEST2="$DEST_DIR/tree/HealthData/DBs/"

# Create destination directories if they don't exist
mkdir -p "$DEST1"
mkdir -p "$DEST2"

echo "Starting Sync..."

# Sync Source 1
if [ -d "$SRC1" ]; then
    echo "Syncing HealthData DBs..."
    rsync -avPh --delete --stats "$SRC1" "$DEST1"
else
    echo "Error: Source 1 directory does not exist: $SRC1"
    exit 1
fi

# Sync Source 2
if [ -d "$SRC2" ]; then
    echo "Syncing tree_home/HealthData DBs..."
    rsync -avPh --delete --stats "$SRC2" "$DEST2"
else
    echo "Error: Source 2 directory does not exist: $SRC2"
    exit 1
fi

echo "Sync Complete."
