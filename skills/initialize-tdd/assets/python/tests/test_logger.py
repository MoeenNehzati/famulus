import logging

from project.logger import get_logger


def test_get_logger_returns_namespaced_logger():
    logger = get_logger("some.module")

    assert logger.name == "project.some.module"


def test_logger_writes_to_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))

    # Re-trigger configuration with the patched LOG_DIR.
    import project.logger as logger_module

    monkeypatch.setattr(logger_module, "_configured", False)
    root = logging.getLogger("project")
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    logger = get_logger("test_module")
    logger.info("hello from test")
    for handler in root.handlers:
        handler.flush()

    log_file = tmp_path / "project.log"
    assert log_file.exists()
    assert "hello from test" in log_file.read_text()
