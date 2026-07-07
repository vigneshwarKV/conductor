"""Implementation of the 'conductor preview' command.

Loads and validates a workflow, then seeds the web dashboard with its
topology (agents, routes, parallel/for-each groups) so a user can see the
DAG without spending any tokens or making any provider calls — the engine
is constructed but its ``run()``/``resume()`` loop is never invoked.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console

from conductor.cli.validate import validate_workflow
from conductor.config.validator import (
    MAX_SUBWORKFLOW_VALIDATION_DEPTH,
    resolve_subworkflow_ref_for_validation,
)
from conductor.engine.workflow import WorkflowEngine, build_subworkflow_started_payload
from conductor.events import WorkflowEventEmitter

if TYPE_CHECKING:
    from conductor.config.schema import AgentDef, WorkflowConfig

logger = logging.getLogger(__name__)


def _iter_subworkflow_agents(config: WorkflowConfig) -> list[AgentDef]:
    """Return every top-level ``type: workflow`` agent in *config*.

    Deliberately excludes ``for_each`` inline agents: at runtime, a
    for_each subworkflow's dashboard node/context is keyed by the actual
    fan-out item (``f"{group.name}[{key}]"`` — see
    ``WorkflowEngine._execute_loop``), and the items come from resolving
    ``for_each.source`` against runtime input that doesn't exist yet in
    preview. There is no single static node to attach a preview context
    to, so for_each subworkflows are left as "not started yet" rather
    than fabricating a fake iteration key.
    """
    return [a for a in config.agents if a.type == "workflow" and a.workflow]


def _build_subworkflow_preview_events(
    agent: AgentDef,
    parent_path: list[str],
    base_dir: Path,
    visited: frozenset[tuple[int, int]],
    depth: int,
    config_cache: dict[tuple[int, int], WorkflowConfig],
) -> list[dict[str, Any]]:
    """Recursively build synthetic ``subworkflow_started``/``workflow_started`` events.

    In a real run, these events are emitted lazily by the engine only once a
    ``type: workflow`` agent actually executes (see
    ``WorkflowEngine._execute_subworkflow``), which is why nested workflows
    show "Subworkflow has not started yet" in preview mode otherwise. This
    builds the same event pairs upfront — resolving each ``workflow:``
    reference the same way ``conductor validate`` already does via
    :func:`conductor.config.validator.resolve_subworkflow_ref_for_validation`
    — so the dashboard's existing drill-down navigation
    (``subworkflowContexts``) works without any agent ever executing.

    Best-effort: any resolution/load failure silently stops that branch.
    ``conductor preview`` always validates first, which already resolves
    every subworkflow reference and rejects cycles/broken refs as hard
    errors — so by the time this runs, failures here should not occur in
    practice, but this is purely a visualization aid and must never crash
    the preview session.

    Note: this re-resolves/re-loads every reference independently of the
    resolution `validate_workflow()` already performed moments earlier in
    `preview_workflow_async` — a known, accepted duplication. Eliminating it
    would mean threading a resolved-path cache through `validate_workflow`'s
    public, widely-used contract (shared with `conductor validate`), which is
    a bigger change than this fix warrants. `config_cache` below at least
    dedupes *within* this function's own tree walk, so a subworkflow file
    referenced by multiple sibling branches is only resolved/loaded once per
    `conductor preview` invocation.

    Args:
        agent: The ``type: workflow`` agent to expand.
        parent_path: Slot-key path of the context this agent lives in
            (``[]`` for the root workflow), matching
            ``WorkflowEngine._dashboard_context_path`` semantics.
        base_dir: Directory to resolve ``agent.workflow`` relative to.
        visited: Canonical ``(st_dev, st_ino)`` tuples already on the
            current branch, for cycle detection.
        depth: Current recursion depth.
        config_cache: Canonical ``(st_dev, st_ino)`` → already-loaded
            ``WorkflowConfig``, shared across the whole call tree (not just
            the current branch) so a file referenced by multiple sibling
            subworkflow agents is only resolved and parsed once.

    Returns:
        A flat, ordered list of ``{"type": ..., "data": ...}`` event dicts
        ready for ``WebDashboard.seed_events``.
    """
    if depth >= MAX_SUBWORKFLOW_VALIDATION_DEPTH:
        logger.warning(
            "conductor preview: sub-workflow depth limit (%d) reached at agent %r; "
            "deeper sub-workflows will not be previewable.",
            MAX_SUBWORKFLOW_VALIDATION_DEPTH,
            agent.name,
        )
        return []

    from conductor.config.loader import load_config

    assert agent.workflow is not None  # narrowed by _iter_subworkflow_agents
    sub_path, errors = resolve_subworkflow_ref_for_validation(
        agent.workflow, f"agent '{agent.name}'", base_dir
    )
    if sub_path is None or errors:
        return []

    try:
        stat = sub_path.stat()
        canonical = (stat.st_dev, stat.st_ino)
    except OSError:
        return []
    if canonical in visited:
        return []

    sub_config = config_cache.get(canonical)
    if sub_config is None:
        try:
            sub_config = load_config(sub_path)
        except Exception:
            return []
        config_cache[canonical] = sub_config

    slot_key = agent.name
    subworkflow_path = [*parent_path, slot_key]
    sub_engine = WorkflowEngine(sub_config, workflow_path=sub_path)
    sub_data = sub_engine.build_workflow_started_data(preview=True)
    sub_data["subworkflow_path"] = subworkflow_path

    events: list[dict[str, Any]] = [
        {
            "type": "subworkflow_started",
            "data": build_subworkflow_started_payload(
                agent_name=agent.name,
                workflow_ref=agent.workflow,
                parent_path=parent_path,
                slot_key=slot_key,
                iteration=1,
            ),
        },
        {"type": "workflow_started", "data": sub_data},
    ]

    new_visited = visited | {canonical}
    for nested_agent in _iter_subworkflow_agents(sub_config):
        events.extend(
            _build_subworkflow_preview_events(
                nested_agent,
                subworkflow_path,
                sub_path.resolve().parent,
                new_visited,
                depth + 1,
                config_cache,
            )
        )
    return events


async def preview_workflow_async(
    workflow_path: Path,
    *,
    web_port: int = 0,
    console: Console | None = None,
    verbose: bool = True,
) -> None:
    """Seed the web dashboard with a workflow's topology and hold it open.

    Args:
        workflow_path: Path to the workflow YAML file.
        web_port: Port for the dashboard (0 = auto-select).
        console: Optional Rich console for status output.
        verbose: When False, suppresses the dashboard URL / hint messages
            (mirrors ``--silent`` handling on ``run``/``replay``).

    Raises:
        typer.Exit: If the workflow fails to load or validate.
    """
    hint_console = console if console is not None else Console(stderr=True)

    # Validation errors print to stdout via validate_workflow's own default
    # console, matching `conductor validate`'s output contract.
    is_valid, config = validate_workflow(workflow_path)
    if not is_valid or config is None:
        raise typer.Exit(code=1)

    from conductor.web.server import WebDashboard

    emitter = WorkflowEventEmitter()
    dashboard = WebDashboard(
        emitter,
        host="127.0.0.1",
        port=web_port,
        bg=False,
        workflow_root=workflow_path.resolve().parent,
    )
    engine = WorkflowEngine(config, workflow_path=workflow_path, event_emitter=emitter)

    # Seed before start() so the very first GET /api/state and WS client
    # both see the topology — same contract as the resume path.
    dashboard.prepend_workflow_started(engine.build_workflow_started_data(preview=True))

    # Recursively expand any `type: workflow` agents so nested subworkflows
    # can be drilled into from the dashboard too, not just the root DAG.
    # `config_cache` is shared across every top-level agent's subtree so a
    # subworkflow file referenced from more than one place is only resolved
    # and parsed once for this invocation.
    subworkflow_events: list[Any] = []
    config_cache: dict[tuple[int, int], WorkflowConfig] = {}
    for agent in _iter_subworkflow_agents(config):
        subworkflow_events.extend(
            _build_subworkflow_preview_events(
                agent, [], workflow_path.resolve().parent, frozenset(), 0, config_cache
            )
        )
    if subworkflow_events:
        dashboard.seed_events(subworkflow_events)

    await dashboard.start()
    if verbose:
        hint_console.print(f"\n[bold cyan]▶ Preview dashboard:[/] {dashboard.url}\n")
        hint_console.print("[dim]Press Ctrl+C to exit[/dim]\n")

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await dashboard.stop()
