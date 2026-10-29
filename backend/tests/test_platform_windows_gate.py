import os

import pytest


@pytest.fixture
def require_windows():
    if os.name != "nt":
        pytest.skip("Windows-only platform acceptance test")


def test_windows_module_imports_without_fcntl(require_windows, tmp_path):
    import app.main

    app = app.main.create_app(tmp_path)
    assert app is not None
    assert not (tmp_path / "voicetransl.log").exists()
