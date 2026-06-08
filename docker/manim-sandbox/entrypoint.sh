#!/bin/bash
set -e

SCENE_FILE="/workspace/input/scene.py"
OUTPUT_DIR="/workspace/output"

if [ ! -f "$SCENE_FILE" ]; then
    echo "ERROR: $SCENE_FILE not found" >&2
    exit 1
fi

# Extract scene class name from the file (first class inheriting from Scene)
SCENE_CLASS=$(grep -oP 'class \K\w+(?=\(Scene\))' "$SCENE_FILE" | head -1)

if [ -z "$SCENE_CLASS" ]; then
    echo "ERROR: No Scene subclass found in $SCENE_FILE" >&2
    exit 1
fi

exec manim render \
    --output_dir "$OUTPUT_DIR" \
    --media_dir "$OUTPUT_DIR" \
    --quality l \
    --format mp4 \
    --disable_caching \
    "$SCENE_FILE" \
    "$SCENE_CLASS"
