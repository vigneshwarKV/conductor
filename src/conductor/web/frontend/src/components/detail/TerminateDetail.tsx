import { MetadataGrid } from './MetadataGrid';
import { StaticConfigSection } from './StaticConfigSection';
import type { NodeData } from '@/stores/workflow-store';
import { NODE_STATUS_HEX } from '@/lib/constants';
import type { NodeStatus } from '@/lib/constants';

interface TerminateDetailProps {
  node: NodeData;
}

/**
 * Detail panel for `type: terminate` steps.
 *
 * Previously terminate nodes fell through to `AgentDetail`, which has no
 * terminate-specific rendering — the panel showed nothing beyond a status
 * badge. `status`/`reason`/`output_template` are static YAML config
 * (rendered by `StaticConfigSection`); `termination_status`/
 * `termination_reason`/`terminated_by` are the actual outcome, populated
 * once the workflow has really reached this step.
 */
export function TerminateDetail({ node }: TerminateDetailProps) {
  const status = node.status as NodeStatus;
  const statusColor = NODE_STATUS_HEX[status] || NODE_STATUS_HEX.pending;

  const items: Array<{ label: string; value: string | number | null | undefined }> = [];
  if (node.termination_status) items.push({ label: 'Outcome', value: node.termination_status });
  if (node.terminated_by) items.push({ label: 'Terminated By', value: node.terminated_by });
  if (node.termination_reason) items.push({ label: 'Reason', value: node.termination_reason });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span
          className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider"
          style={{ backgroundColor: `${statusColor}20`, color: statusColor }}
        >
          {status}
        </span>
        <span className="text-xs text-[var(--text-muted)]">Terminate</span>
      </div>

      <StaticConfigSection config={node.config} />

      <MetadataGrid items={items} />
    </div>
  );
}
