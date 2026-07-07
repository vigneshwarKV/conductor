"""Tests for recursive subworkflow expansion in `conductor preview`.

Tests cover:
- `_iter_subworkflow_agents` finds top-level `type: workflow` agents,
  excludes for_each inline agents
- `_build_subworkflow_preview_events` builds correctly ordered/shaped
  synthetic events, recursing into nested subworkflows
- Cycle detection stops recursion instead of looping forever
- CLI integration: `conductor preview` seeds the dashboard with the
  full subworkflow event tree
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from conductor.cli.app import app
from conductor.cli.preview import _build_subworkflow_preview_events, _iter_subworkflow_agents
from conductor.config.loader import load_config
from conductor.config.schema import AgentDef

runner = CliRunner()


def _write_yaml(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


class TestIterSubworkflowAgents:
    def test_finds_top_level_workflow_agents(self, tmp_path: Path) -> None:
        wf_path = _write_yaml(
            tmp_path / "root.yaml",
            """\
            workflow:
              name: root
              entry_point: a
            agents:
              - name: a
                type: workflow
                workflow: ./sub.yaml
              - name: b
                prompt: hi
            """,
        )
        _write_yaml(
            tmp_path / "sub.yaml",
            "workflow:\n  name: sub\n  entry_point: x\nagents:\n  - name: x\n    prompt: hi\n",
        )
        config = load_config(wf_path)
        agents = _iter_subworkflow_agents(config)
        assert [a.name for a in agents] == ["a"]

    def test_excludes_for_each_inline_agent(self, tmp_path: Path) -> None:
        wf_path = _write_yaml(
            tmp_path / "root.yaml",
            """\
            workflow:
              name: root
              entry_point: fe
              input:
                items:
                  type: string
                  required: true
            agents: []
            for_each:
              - name: fe
                type: for_each
                source: workflow.input.items
                as: item
                agent:
                  name: inline
                  type: workflow
                  workflow: ./sub.yaml
            """,
        )
        _write_yaml(
            tmp_path / "sub.yaml",
            "workflow:\n  name: sub\n  entry_point: x\nagents:\n  - name: x\n    prompt: hi\n",
        )
        config = load_config(wf_path)
        assert _iter_subworkflow_agents(config) == []


class TestBuildSubworkflowPreviewEvents:
    def test_single_level_subworkflow(self, tmp_path: Path) -> None:
        wf_path = _write_yaml(
            tmp_path / "root.yaml",
            """\
            workflow:
              name: root
              entry_point: design
            agents:
              - name: design
                type: workflow
                workflow: ./sub.yaml
            """,
        )
        _write_yaml(
            tmp_path / "sub.yaml",
            """\
            workflow:
              name: sub-design
              entry_point: x
            agents:
              - name: x
                prompt: hi
            """,
        )
        config = load_config(wf_path)
        [agent] = _iter_subworkflow_agents(config)

        events = _build_subworkflow_preview_events(
            agent, [], wf_path.resolve().parent, frozenset(), 0, {}
        )

        assert [e["type"] for e in events] == ["subworkflow_started", "workflow_started"]
        sub_started, workflow_started = events
        assert sub_started["data"]["agent_name"] == "design"
        assert sub_started["data"]["parent_path"] == []
        assert sub_started["data"]["slot_key"] == "design"
        assert workflow_started["data"]["name"] == "sub-design"
        assert workflow_started["data"]["preview"] is True
        assert workflow_started["data"]["subworkflow_path"] == ["design"]

    def test_recurses_into_nested_subworkflow(self, tmp_path: Path) -> None:
        wf_path = _write_yaml(
            tmp_path / "root.yaml",
            """\
            workflow:
              name: root
              entry_point: design
            agents:
              - name: design
                type: workflow
                workflow: ./mid.yaml
            """,
        )
        _write_yaml(
            tmp_path / "mid.yaml",
            """\
            workflow:
              name: mid
              entry_point: inner
            agents:
              - name: inner
                type: workflow
                workflow: ./leaf.yaml
            """,
        )
        _write_yaml(
            tmp_path / "leaf.yaml",
            "workflow:\n  name: leaf\n  entry_point: x\nagents:\n  - name: x\n    prompt: hi\n",
        )
        config = load_config(wf_path)
        [agent] = _iter_subworkflow_agents(config)

        events = _build_subworkflow_preview_events(
            agent, [], wf_path.resolve().parent, frozenset(), 0, {}
        )

        assert [e["type"] for e in events] == [
            "subworkflow_started",
            "workflow_started",
            "subworkflow_started",
            "workflow_started",
        ]
        names = [e["data"].get("name") for e in events if e["type"] == "workflow_started"]
        assert names == ["mid", "leaf"]
        leaf_started = events[-1]
        assert leaf_started["data"]["subworkflow_path"] == ["design", "inner"]

    def test_self_referencing_cycle_stops_recursion(self, tmp_path: Path) -> None:
        wf_path = _write_yaml(
            tmp_path / "root.yaml",
            """\
            workflow:
              name: root
              entry_point: loop
            agents:
              - name: loop
                type: workflow
                workflow: ./root.yaml
            """,
        )
        config = load_config(wf_path)
        [agent] = _iter_subworkflow_agents(config)

        events = _build_subworkflow_preview_events(
            agent, [], wf_path.resolve().parent, frozenset(), 0, {}
        )

        # First expansion succeeds; the self-reference inside it hits the
        # visited-set and stops rather than recursing forever.
        assert [e["type"] for e in events] == ["subworkflow_started", "workflow_started"]

    def test_missing_subworkflow_file_returns_empty(self, tmp_path: Path) -> None:
        wf_path = _write_yaml(
            tmp_path / "root.yaml",
            """\
            workflow:
              name: root
              entry_point: a
            agents:
              - name: a
                type: workflow
                workflow: ./does-not-exist.yaml
            """,
        )
        config = load_config(wf_path)
        [agent] = _iter_subworkflow_agents(config)

        events = _build_subworkflow_preview_events(
            agent, [], wf_path.resolve().parent, frozenset(), 0, {}
        )
        assert events == []

    def test_shared_config_cache_avoids_reloading_same_file(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Two sibling agents referencing the same subworkflow file only load it once."""
        _write_yaml(
            tmp_path / "sub.yaml",
            """\
            workflow:
              name: shared-sub
              entry_point: x
            agents:
              - name: x
                prompt: hi
            """,
        )
        agent_a = AgentDef(name="a", type="workflow", workflow="./sub.yaml")
        agent_b = AgentDef(name="b", type="workflow", workflow="./sub.yaml")

        real_load_config = load_config
        call_count = 0

        def counting_load_config(path: Path) -> Any:
            nonlocal call_count
            call_count += 1
            return real_load_config(path)

        monkeypatch.setattr("conductor.config.loader.load_config", counting_load_config)

        config_cache: dict[tuple[int, int], Any] = {}
        events_a = _build_subworkflow_preview_events(
            agent_a, [], tmp_path, frozenset(), 0, config_cache
        )
        events_b = _build_subworkflow_preview_events(
            agent_b, [], tmp_path, frozenset(), 0, config_cache
        )

        assert call_count == 1
        assert len(events_a) == 2
        assert len(events_b) == 2


class TestPreviewCommandSeedsSubworkflows:
    def _mock_dashboard(self) -> MagicMock:
        mock_dashboard = MagicMock()
        mock_dashboard.url = "http://127.0.0.1:9999"
        mock_dashboard.start = AsyncMock()
        mock_dashboard.stop = AsyncMock()
        mock_dashboard.prepend_workflow_started = MagicMock()
        mock_dashboard.seed_events = MagicMock()
        return mock_dashboard

    def _patch_wait_to_cancel(self) -> Any:
        import asyncio

        mock_event = MagicMock()
        mock_event.wait = AsyncMock(side_effect=asyncio.CancelledError())
        return patch("asyncio.Event", return_value=mock_event)

    def test_seeds_nested_subworkflow_events(self, tmp_path: Path) -> None:
        wf_path = _write_yaml(
            tmp_path / "root.yaml",
            """\
            workflow:
              name: root
              entry_point: design
            agents:
              - name: design
                type: workflow
                workflow: ./sub.yaml
            """,
        )
        _write_yaml(
            tmp_path / "sub.yaml",
            """\
            workflow:
              name: sub-design
              entry_point: x
            agents:
              - name: x
                prompt: hi
            """,
        )
        mock_dashboard = self._mock_dashboard()

        with (
            patch("conductor.web.server.WebDashboard", return_value=mock_dashboard),
            self._patch_wait_to_cancel(),
        ):
            result = runner.invoke(app, ["preview", str(wf_path)])

        assert result.exit_code == 0, result.output
        mock_dashboard.seed_events.assert_called_once()
        seeded_events = mock_dashboard.seed_events.call_args[0][0]
        assert [e["type"] for e in seeded_events] == ["subworkflow_started", "workflow_started"]
        assert seeded_events[1]["data"]["name"] == "sub-design"

    def test_no_seed_call_when_no_subworkflows(self, tmp_path: Path) -> None:
        wf_path = _write_yaml(
            tmp_path / "root.yaml",
            "workflow:\n  name: root\n  entry_point: a\nagents:\n  - name: a\n    prompt: hi\n",
        )
        mock_dashboard = self._mock_dashboard()

        with (
            patch("conductor.web.server.WebDashboard", return_value=mock_dashboard),
            self._patch_wait_to_cancel(),
        ):
            result = runner.invoke(app, ["preview", str(wf_path)])

        assert result.exit_code == 0, result.output
        mock_dashboard.seed_events.assert_not_called()
