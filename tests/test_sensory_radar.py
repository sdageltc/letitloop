"""
tests/test_sensory_radar.py
Unit tests for SensoryRadar hotspot discovery and task generation.
"""

import tempfile
from pathlib import Path

from orchestrator.sensory_radar import SensoryRadar


def test_sensory_radar_scans_hotspots():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src_dir = root / "pkg"
        src_dir.mkdir()
        (src_dir / "heavy.py").write_text(
            """
def complex_method(a, b, c):
    for i in range(10):
        if a > 0:
            if b > 0:
                if c > 0:
                    return a + b + c
    return 0
""",
            encoding="utf-8",
        )
        radar = SensoryRadar(root)
        tasks = radar.scan_workspace()
        assert len(tasks) >= 1
        assert tasks[0].target_module.replace("\\", "/").endswith("pkg/heavy.py")
        assert tasks[0].target_function == "complex_method"
        assert tasks[0].complexity_score > 0
        assert tasks[0].priority >= 1
