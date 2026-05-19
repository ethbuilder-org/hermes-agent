#!/bin/bash
# claude-vision.sh — Fast vision via Claude Max subscription
# Usage: claude-vision.sh <image_path> [question]
IMAGE="$1"
QUESTION="${2:-Describe this image in detail.}"

if [ ! -f "$IMAGE" ]; then
    echo "ERROR: Image not found: $IMAGE"
    exit 1
fi

claude -p "Read the image at $IMAGE. $QUESTION" --model claude-opus-4-7 2>&1
