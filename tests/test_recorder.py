from pathlib import Path
import time

from gaitlab.hub import DataHub
from gaitlab.record.csv_recorder import CsvRecorder


def test_csv_recorder(tmp_path: Path):
    hub = DataHub()
    hub.publish("physio.heart_rate_bpm", 72.0, "bpm")
    rec = CsvRecorder(hub, output_dir=tmp_path)
    path = rec.start("test", metadata={"note": "unit"}, sample_mode="hz", sample_value=1000.0)
    assert path.exists()
    rec.tick()
    time.sleep(0.002)
    rec.tick()
    stopped = rec.stop()
    assert stopped == path
    text = path.read_text(encoding="utf-8")
    assert "time_sec" in text
    assert "physio.heart_rate_bpm" in text
    assert "72" in text


def test_csv_custom_name_and_interval(tmp_path: Path):
    hub = DataHub()
    hub.publish("a", 1.0)
    rec = CsvRecorder(hub, output_dir=tmp_path)
    path = rec.start(
        "ignored_base",
        output_name="My_Trial_3",
        sample_mode="interval",
        sample_value=10.0,  # only one row unless we wait 10s
    )
    assert "My_Trial_3" in path.name
    rec.tick()
    rec.tick()  # second tick should be rate-limited away
    rec.stop()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("#") and ln.strip()]
    # header + one data row
    assert len(lines) == 2
    assert "sample_interval_sec=10" in path.read_text(encoding="utf-8")
