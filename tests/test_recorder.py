from pathlib import Path

from gaitlab.hub import DataHub
from gaitlab.record.csv_recorder import CsvRecorder


def test_csv_recorder(tmp_path: Path):
    hub = DataHub()
    hub.publish("physio.heart_rate_bpm", 72.0, "bpm")
    rec = CsvRecorder(hub, output_dir=tmp_path)
    path = rec.start("test", metadata={"note": "unit"})
    assert path.exists()
    rec.tick()
    rec.tick()
    stopped = rec.stop()
    assert stopped == path
    text = path.read_text(encoding="utf-8")
    assert "time_sec" in text
    assert "physio.heart_rate_bpm" in text
    assert "72" in text
