import importlib.util
from pathlib import Path


def test_progress_manifest_survives_transient_windows_read_handle(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/finalize_demo_schedule.py"
    spec = importlib.util.spec_from_file_location("finalize_demo_schedule", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = Path.replace
    calls = []

    def replace(source, target):
        calls.append(target)
        if len(calls) == 1:
            raise PermissionError("temporary file read handle")
        return original(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    target = tmp_path / "progress.json"
    module.save(target, {"accepted": 100, "simulation": True})
    assert '"accepted": 100' in target.read_text()
    assert len(calls) == 2
    assert not target.with_suffix(".tmp").exists()
