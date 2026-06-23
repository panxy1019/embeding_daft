"""
Demo: automatic config version compaction with retention window.

Run:
    python tutorials/config_versioning_auto_compact_demo.py
"""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ahvn.utils.basic.config_utils import ConfigStorage


def main() -> None:
    with TemporaryDirectory(prefix="ahvn-config-compact-demo-") as tmp:
        db_path = Path(tmp) / "config.db"
        storage = ConfigStorage(package="demo", database=str(db_path))

        # 1) Auto-compact on set(): keep_last_n=None -> default-config value (20).
        for i in range(1, 26):
            storage.set("demo", "demo", {"value": i}, package_version="1.0", keep_last_n=None)
        versions = storage.versions("demo", "demo")
        assert len(versions) == 20
        assert versions[0] == 25 and versions[-1] == 6

        # 2) Manual compact without reset keeps original version numbers.
        for i in range(26, 31):
            storage.set("demo", "demo", {"value": i}, package_version="1.0", keep_last_n=1000)
        removed = storage.compact("demo", "demo", keep_last_n=5)
        versions = storage.versions("demo", "demo")
        assert removed == 20
        assert versions == [30, 29, 28, 27, 26]
        assert storage.get("demo", "demo") == {"value": 30}

        # 3) Legacy reset mode is still available.
        removed = storage.compact("demo", "demo", reset=True)
        snap = storage.get("demo", "demo", snapshot=True)
        assert removed == 1
        assert snap is not None and snap.version == 1
        assert dict(snap) == {"value": 30}

    print("Config auto-compaction demo passed.")


if __name__ == "__main__":
    main()
