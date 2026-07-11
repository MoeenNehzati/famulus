"""Check whether Marker/Surya model checkpoints are cached locally."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonMachineInterface


CHECKPOINT_ATTRS = {
    "layout": "LAYOUT_MODEL_CHECKPOINT",
    "text_recognition": "FOUNDATION_MODEL_CHECKPOINT",
    "table_recognition": "TABLE_REC_MODEL_CHECKPOINT",
    "ocr_error_detection": "OCR_ERROR_MODEL_CHECKPOINT",
    "text_detection": "DETECTOR_MODEL_CHECKPOINT",
}


class Interface(PythonMachineInterface):
    prog = "check-marker-models"

    def run(self, _args) -> int:
        try:
            from surya.settings import settings as surya_settings
        except ImportError:
            print("surya not found - is marker-pdf installed?", file=sys.stderr)
            return 2

        cache_dir = Path(surya_settings.MODEL_CACHE_DIR)
        missing = []
        for name, attr in CHECKPOINT_ATTRS.items():
            checkpoint = getattr(surya_settings, attr)
            local = cache_dir / checkpoint.replace("s3://", "")
            if not os.path.exists(local):
                missing.append(name)

        if missing:
            print(f"Missing models ({len(missing)}/{len(CHECKPOINT_ATTRS)}): {', '.join(missing)}")
            return 1
        print("All models cached.")
        return 0


def main(argv: list[str] | None = None) -> int:
    interface = Interface()
    parser = interface.build_parser()
    return interface.run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
