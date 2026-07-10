#!/usr/bin/env bash
# Check whether marker/surya models are cached locally.
# Usage: check-marker-models.sh
# Exit 0: all models present.
# Exit 1: one or more models missing — running marker will trigger downloads.
# Stdout: one line per missing model, or "All models cached." if none missing.

python3 - <<'EOF'
import os, sys
try:
    from surya.settings import settings as s
except ImportError:
    print("surya not found — is marker-pdf installed?", file=sys.stderr)
    sys.exit(2)

checkpoints = {
    "layout":             s.LAYOUT_MODEL_CHECKPOINT,
    "text_recognition":   s.FOUNDATION_MODEL_CHECKPOINT,
    "table_recognition":  s.TABLE_REC_MODEL_CHECKPOINT,
    "ocr_error_detection": s.OCR_ERROR_MODEL_CHECKPOINT,
    "text_detection":     s.DETECTOR_MODEL_CHECKPOINT,
}

missing = [
    name for name, ckpt in checkpoints.items()
    if not os.path.exists(os.path.join(s.MODEL_CACHE_DIR, ckpt.replace("s3://", "")))
]

if missing:
    print(f"Missing models ({len(missing)}/{len(checkpoints)}): {', '.join(missing)}")
    sys.exit(1)
else:
    print("All models cached.")
    sys.exit(0)
EOF
