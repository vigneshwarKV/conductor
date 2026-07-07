"""Provider tier metadata in the workflow_started event (#241)."""

from __future__ import annotations

from typing import Any

import pytest

from conductor.config.schema import (
    AgentDef,
    ForEachDef,
    RuntimeConfig,
    WorkflowConfig,
    WorkflowDef,
)
from conductor.engine.workflow import WorkflowEngine, _static_agent_config


def _engine(agents: list[AgentDef], default_provider: str = "copilot") -> WorkflowEngine:
    config = WorkflowConfig(
        workflow=WorkflowDef(
            name="test",
            entry_point=agents[0].name,
            runtime=RuntimeConfig(provider=default_provider),
        ),
        agents=agents,
    )
    return WorkflowEngine(config=config, provider=None)


class TestProvidersBlock:
    def test_default_provider_recorded(self) -> None:
        engine = _engine([AgentDef(name="a", prompt="hi")])
        data = engine.build_workflow_started_data()
        providers = data["providers"]
        assert "copilot" in providers
        copilot = providers["copilot"]
        assert copilot["name"] == "copilot"
        assert copilot["status"] == "ok"
        assert copilot["tier"] == "stable"
        # The wire payload intentionally does NOT include the full
        # capability dump — it's not consumed by any frontend and would
        # bloat the JSONL. The CLI banner re-resolves capabilities from
        # the provider name when needed.
        assert "capabilities" not in copilot

    def test_per_agent_override_recorded(self) -> None:
        engine = _engine(
            [
                AgentDef(name="a", prompt="hi"),
                AgentDef(name="b", prompt="hi", provider="claude"),
            ]
        )
        data = engine.build_workflow_started_data()
        # Both providers appear in the providers block.
        assert "copilot" in data["providers"]
        assert "claude" in data["providers"]

    def test_agent_entries_include_provider_name(self) -> None:
        engine = _engine(
            [
                AgentDef(name="a", prompt="hi"),
                AgentDef(name="b", prompt="hi", provider="claude"),
            ]
        )
        data = engine.build_workflow_started_data()
        by_name = {a["name"]: a for a in data["agents"]}
        assert by_name["a"]["provider_name"] == "copilot"
        assert by_name["b"]["provider_name"] == "claude"

    def test_experimental_provider_surfaces_in_block(self) -> None:
        """claude-agent-sdk shows up with tier=experimental and an upstream_pin."""
        pytest.importorskip("claude_agent_sdk")
        engine = _engine([AgentDef(name="a", prompt="hi", provider="claude-agent-sdk")])
        data = engine.build_workflow_started_data()
        sdk_meta = data["providers"]["claude-agent-sdk"]
        assert sdk_meta["tier"] == "experimental"
        assert sdk_meta["upstream_pin"] is not None
        assert "claude-agent-sdk" in sdk_meta["upstream_pin"]

    def test_unknown_provider_gets_unresolved_stub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When CAPABILITIES is missing/broken, emit status='unresolved' (NOT tier='unknown').

        The wire ``tier`` field stays constrained to the same Literal as
        ``ProviderCapabilities.tier``; a ``status`` discriminator
        differentiates resolved from fallback entries.
        """

        def fake(name: str) -> Any:
            raise AttributeError(f"no CAPABILITIES on {name}")

        monkeypatch.setattr("conductor.providers.capabilities.get_capabilities", fake)
        engine = _engine([AgentDef(name="a", prompt="hi")])
        data = engine.build_workflow_started_data()
        stub = data["providers"]["copilot"]
        assert stub["status"] == "unresolved"
        assert stub["tier"] is None


class TestPreviewFlag:
    """The ``preview`` kwarg used by ``conductor preview`` (no execution)."""

    def test_defaults_to_false(self) -> None:
        engine = _engine([AgentDef(name="a", prompt="hi")])
        data = engine.build_workflow_started_data()
        assert data["preview"] is False

    def test_preview_true_when_requested(self) -> None:
        engine = _engine([AgentDef(name="a", prompt="hi")])
        data = engine.build_workflow_started_data(preview=True)
        assert data["preview"] is True


class TestStaticAgentConfig:
    """Static (author-configured) YAML fields surfaced for the dashboard.

    Lets a node's detail panel show what it does (prompt, command, wait
    duration, gate options, ...) before it ever runs — see
    ``conductor.cli.preview`` and ``StaticConfigSection.tsx``.
    """

    def test_agent_type_fields(self) -> None:
        agent = AgentDef(
            name="a",
            prompt="hi {{x}}",
            system_prompt="you are helpful",
            tools=["search"],
        )
        config = _static_agent_config(agent)
        assert config["prompt"] == "hi {{x}}"
        assert config["system_prompt"] == "you are helpful"
        assert config["tools"] == ["search"]
        # No config fields for things that weren't set.
        assert "retry" not in config
        assert "dialog" not in config
        assert "validator" not in config

    def test_script_env_values_never_included_only_keys(self) -> None:
        """Security: env values may hold resolved secrets; never send them."""
        agent = AgentDef(
            name="s",
            type="script",
            command="echo",
            args=["hi"],
            env={"API_KEY": "super-secret-value", "MODE": "prod"},
        )
        config = _static_agent_config(agent)
        assert config["command"] == "echo"
        assert config["args"] == ["hi"]
        assert set(config["env_keys"]) == {"API_KEY", "MODE"}
        assert "env" not in config
        dumped = str(config)
        assert "super-secret-value" not in dumped

    def test_wait_type_fields(self) -> None:
        agent = AgentDef(name="w", type="wait", duration="5s", reason="cooldown")
        config = _static_agent_config(agent)
        assert config == {"duration": "5s", "reason": "cooldown"}

    def test_human_gate_options(self) -> None:
        agent = AgentDef(
            name="g",
            type="human_gate",
            prompt="approve?",
            options=[{"label": "Yes", "value": "y", "route": "$end"}],
        )
        config = _static_agent_config(agent)
        assert config["prompt"] == "approve?"
        assert config["options"] == [
            {"label": "Yes", "value": "y", "route": "$end", "prompt_for": None}
        ]

    def test_terminate_type_fields(self) -> None:
        agent = AgentDef(name="t", type="terminate", status="failed", reason="bad input")
        config = _static_agent_config(agent)
        assert config == {"status": "failed", "reason": "bad input"}

    def test_workflow_type_fields(self) -> None:
        agent = AgentDef(name="sub", type="workflow", workflow="./child.yaml", max_depth=3)
        config = _static_agent_config(agent)
        assert config["workflow"] == "./child.yaml"
        assert config["max_depth"] == 3

    def test_build_workflow_started_data_includes_config_per_agent(self) -> None:
        engine = _engine([AgentDef(name="a", prompt="hi there")])
        data = engine.build_workflow_started_data()
        assert data["agents"][0]["config"]["prompt"] == "hi there"

    def test_for_each_group_includes_inline_agent_config(self) -> None:
        config = WorkflowConfig(
            workflow=WorkflowDef(
                name="test",
                entry_point="fe",
                runtime=RuntimeConfig(provider="copilot"),
            ),
            agents=[],
            for_each=[
                ForEachDef(
                    name="fe",
                    type="for_each",
                    source="workflow.input.items",
                    **{"as": "item"},
                    agent=AgentDef(name="inline", type="script", command="echo"),
                )
            ],
        )
        engine = WorkflowEngine(config=config, provider=None)
        data = engine.build_workflow_started_data()
        fe_entry = data["for_each_groups"][0]
        assert fe_entry["agent"]["name"] == "inline"
        assert fe_entry["agent"]["type"] == "script"
        assert fe_entry["agent"]["config"]["command"] == "echo"
