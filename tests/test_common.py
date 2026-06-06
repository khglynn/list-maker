"""Shared logging foundation (Workstream A5)."""

import logging

from pipeline.common import get_logger


def test_get_logger_is_idempotent_and_configured(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    name = "pipeline.test.a5"
    logging.getLogger(name).handlers.clear()  # clean slate

    first = get_logger(name)
    assert first.level == logging.DEBUG
    assert len(first.handlers) == 1
    assert first.propagate is False

    # Repeated calls don't stack handlers (idempotent).
    second = get_logger(name)
    assert second is first
    assert len(second.handlers) == 1


def test_get_logger_defaults_to_info(monkeypatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    name = "pipeline.test.a5_default"
    logging.getLogger(name).handlers.clear()
    assert get_logger(name).level == logging.INFO


def test_get_logger_falls_back_on_bogus_level(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "NONSENSE")
    name = "pipeline.test.a5_bogus"
    logging.getLogger(name).handlers.clear()
    assert get_logger(name).level == logging.INFO
