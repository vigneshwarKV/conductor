import { memo } from 'react';
import { MetadataGrid } from './MetadataGrid';
import { OutputViewer } from './OutputViewer';
import type { StaticAgentConfig } from '@/types/events';

interface StaticConfigSectionProps {
  config?: StaticAgentConfig;
}

/**
 * Renders a node's author-configured YAML fields — prompt, command, wait
 * duration, gate options, etc. — independent of whether the node has ever
 * run. Shown unconditionally (not preview-only): a pending node in a live
 * run has the exact same "nothing to show yet" problem preview does.
 *
 * Field presence varies entirely by `config`'s shape (driven by the node's
 * step type — see `StaticAgentConfig`), so this renders generically rather
 * than switching on `node.type`: each field is only shown when set.
 *
 * Memoized: `config` never changes after `workflow_started` (see
 * `NodeData.config`), but the parent Detail component re-renders on every
 * streaming event for the selected node (new node object via `replaceNode`),
 * which would otherwise re-run `OutputViewer`'s formatting on this
 * unchanging data every time.
 */
export const StaticConfigSection = memo(function StaticConfigSection({
  config,
}: StaticConfigSectionProps) {
  if (!config || Object.keys(config).length === 0) return null;

  const metaItems: Array<{ label: string; value: string | number | null | undefined }> = [
    {
      // Distinguish `tools: []` ("explicitly no tools") from an unset
      // `tools:` field ("inherits all workflow tools") — both are
      // meaningful and must not collapse to the same blank row.
      label: 'Tools',
      value: config.tools === undefined ? undefined : config.tools.length ? config.tools.join(', ') : '(none)',
    },
    { label: 'Timeout', value: config.timeout_seconds != null ? `${config.timeout_seconds}s` : config.timeout != null ? `${config.timeout}s` : undefined },
    { label: 'Working Dir', value: config.working_dir },
    { label: 'Env Vars', value: config.env_keys?.length ? config.env_keys.join(', ') : undefined },
    { label: 'Duration', value: config.duration != null ? String(config.duration) : undefined },
    { label: 'Output Type', value: config.output_type },
    { label: 'Subworkflow', value: config.workflow },
    { label: 'Max Depth', value: config.max_depth },
    { label: 'Status', value: config.status },
    {
      label: 'Retry',
      value: config.retry
        ? `${config.retry.max_attempts}x (${config.retry.backoff}, ${config.retry.delay_seconds}s delay)`
        : undefined,
    },
    {
      label: 'Validator retries',
      value: config.validator ? config.validator.max_retries : undefined,
    },
  ];

  return (
    <div className="space-y-3">
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] font-semibold">
        Configuration
      </div>
      <MetadataGrid items={metaItems} />
      {config.prompt && (
        <OutputViewer output={config.prompt} title="Prompt Template" defaultExpanded={false} />
      )}
      {config.system_prompt && (
        <OutputViewer output={config.system_prompt} title="System Prompt" defaultExpanded={false} />
      )}
      {config.command && (
        <OutputViewer
          output={[config.command, ...(config.args || [])].join(' ')}
          title="Command"
          defaultExpanded={false}
        />
      )}
      {config.value !== undefined && (
        <OutputViewer output={config.value} title="Value" defaultExpanded={false} />
      )}
      {config.values && (
        <OutputViewer output={config.values} title="Values" defaultExpanded={false} />
      )}
      {config.output && (
        <OutputViewer output={config.output} title="Output Schema" defaultExpanded={false} />
      )}
      {config.input_mapping && (
        <OutputViewer output={config.input_mapping} title="Input Mapping" defaultExpanded={false} />
      )}
      {config.output_template && (
        <OutputViewer output={config.output_template} title="Output Template" defaultExpanded={false} />
      )}
      {config.dialog?.trigger_prompt && (
        <OutputViewer output={config.dialog.trigger_prompt} title="Dialog Trigger" defaultExpanded={false} />
      )}
      {config.validator?.criteria && (
        <OutputViewer output={config.validator.criteria} title="Validator Criteria" defaultExpanded={false} />
      )}
      {config.reason && (
        <OutputViewer output={config.reason} title="Reason" defaultExpanded={false} />
      )}
      {config.options && config.options.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] font-semibold">
            Options
          </div>
          <div className="space-y-1">
            {config.options.map((o) => (
              <div
                key={o.value}
                className="text-xs px-2 py-1.5 rounded border border-[var(--border)] bg-[var(--bg)]"
              >
                <span className="font-medium text-[var(--text)]">{o.label}</span>
                <span className="text-[var(--text-muted)]"> → {o.route}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
});
