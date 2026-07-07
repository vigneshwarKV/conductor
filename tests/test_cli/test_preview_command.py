"""Tests for the preview CLI command.

Tests cover:
- Help text
- Missing / invalid workflow file errors
- Successful invocation (mocked dashboard) seeds topology with preview=True
- --silent compliance (mirrors TestReplaySilentCompliance)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from conductor.cli.app import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _write_workflow(tmp_path: Path) -> Path:
    wf_path = tmp_path / "wf.yaml"
    wf_path.write_text(
        """
workflow:
  name: preview-test
  entry_point: a
agents:
  - name: a
    prompt: hello
    routes:
      - to: $end
"""
    )
    return wf_path


def _mock_dashboard() -> MagicMock:
    mock_dashboard = MagicMock()
    mock_dashboard.url = "http://127.0.0.1:9999"
    mock_dashboard.start = AsyncMock()
    mock_dashboard.stop = AsyncMock()
    mock_dashboard.prepend_workflow_started = MagicMock()
    return mock_dashboard


def _patch_wait_to_cancel() -> Any:
    import asyncio

    mock_event = MagicMock()
    mock_event.wait = AsyncMock(side_effect=asyncio.CancelledError())
    return patch("asyncio.Event", return_value=mock_event)


class TestPreviewCommand:
    def test_help(self) -> None:
        result = runner.invoke(app, ["preview", "--help"])
        assert result.exit_code == 0
        clean = _ANSI_RE.sub("", result.output)
        assert "without running it" in clean
        assert "--web-port" in clean

    def test_missing_file(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["preview", str(tmp_path / "nonexistent.yaml")])
        assert result.exit_code != 0

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("not: valid: workflow: yaml: at: all:")
        result = runner.invoke(app, ["preview", str(bad_file)])
        assert result.exit_code != 0

    def test_seeds_dashboard_with_preview_topology(self, tmp_path: Path) -> None:
        wf_path = _write_workflow(tmp_path)
        mock_dashboard = _mock_dashboard()

        with (
            patch("conductor.web.server.WebDashboard", return_value=mock_dashboard),
            _patch_wait_to_cancel(),
        ):
            result = runner.invoke(app, ["preview", str(wf_path)])

        assert result.exit_code == 0, result.output
        mock_dashboard.prepend_workflow_started.assert_called_once()
        seeded = mock_dashboard.prepend_workflow_started.call_args[0][0]
        assert seeded["preview"] is True
        assert seeded["name"] == "preview-test"
        assert any(a["name"] == "a" for a in seeded["agents"])
        mock_dashboard.start.assert_awaited_once()
        mock_dashboard.stop.assert_awaited_once()

    def test_custom_port_option(self) -> None:
        result = runner.invoke(app, ["preview", "--help"])
        clean = _ANSI_RE.sub("", result.output)
        assert "--web-port" in clean


class TestPreviewSilentCompliance:
    """Mirrors TestReplaySilentCompliance in test_replay_command.py."""

    def test_silent_suppresses_dashboard_messages(self, tmp_path: Path) -> None:
        wf_path = _write_workflow(tmp_path)
        mock_dashboard = _mock_dashboard()

        with (
            patch("conductor.web.server.WebDashboard", return_value=mock_dashboard),
            _patch_wait_to_cancel(),
        ):
            result = runner.invoke(app, ["--silent", "preview", str(wf_path)])

        assert result.exit_code == 0, result.output
        stderr = _ANSI_RE.sub("", result.stderr)
        assert "Preview dashboard" not in stderr
        assert "Press Ctrl+C" not in stderr
        assert "http://127.0.0.1:9999" not in stderr

    def test_verbose_prints_dashboard_messages(self, tmp_path: Path) -> None:
        wf_path = _write_workflow(tmp_path)
        mock_dashboard = _mock_dashboard()

        with (
            patch("conductor.web.server.WebDashboard", return_value=mock_dashboard),
            _patch_wait_to_cancel(),
        ):
            result = runner.invoke(app, ["preview", str(wf_path)])

        assert result.exit_code == 0, result.output
        stderr = _ANSI_RE.sub("", result.stderr)
        assert "Preview dashboard" in stderr
        assert "http://127.0.0.1:9999" in stderr
        assert "Press Ctrl+C" in stderr
