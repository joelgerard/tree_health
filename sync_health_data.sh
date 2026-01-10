#!/bin/bash

# Configuration
DEST_DIR="$HOME/GarminDBSync"

# Source Directories
SRC1="/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/HealthData/"
SRC2="/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/tree_home/HealthData/"

# Destination Subdirectories
DEST1="$DEST_DIR/joel/HealthData/"
DEST2="$DEST_DIR/tree/HealthData/"

# Create destination directories if they don't exist
mkdir -p "$DEST1"
mkdir -p "$DEST2"

echo "Starting Sync..."

# Sync Source 1
if [ -d "$SRC1" ]; then
    echo "Syncing HealthData..."
    rsync -avP --delete "$SRC1" "$DEST1"
else
    echo "Error: Source 1 directory does not exist: $SRC1"
    exit 1
fi

# Sync Source 2
if [ -d "$SRC2" ]; then
    echo "Syncing tree_home/HealthData..."
    rsync -avP --delete "$SRC2" "$DEST2"
else
    echo "Error: Source 2 directory does not exist: $SRC2"
    exit 1
fi

echo "Sync Complete."
