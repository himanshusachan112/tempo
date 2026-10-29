import type { RenameSuggestion, RevisedDiscrepancies, RevisedFinding } from '../../api/renamingTypes';
import { DataTable, type DataTableColumn } from '../common/DataTable';

const mono = (v: unknown) => <span className="font-mono">{String(v ?? '-')}</span>;

const COLUMNS: DataTableColumn<RevisedFinding>[] = [
  { key: 'check_id', header: 'Rule', render: (f) => f.check_id },
  { key: 'category', header: 'Category', render: (f) => f.category },
  { key: 'row', header: 'Row', render: (f) => f.affected_row_number ?? 'n/a' },
  { key: 'field', header: 'Field', render: (f) => f.affected_field ?? 'n/a' },
  { key: 'expected', header: 'Expected', render: (f) => mono(f.expected_value) },
  { key: 'observed', header: 'Observed', render: (f) => mono(f.observed_value) },
  { key: 'variance', header: 'Variance', render: (f) => mono(f.variance) },
  { key: 'message', header: 'Explanation', render: (f) => <span className="text-xs">{f.message}</span> },
  {
    key: 'source',
    header: 'Basis',
    render: (f) => (
      <span className="text-xs text-slate-500">{f.source === 'recomputed' ? 'Re-checked with new names' : 'Unchanged'}</span>
    ),
  },
];

/**
 * Discrepancies as they stand once the accepted renamings are applied. Display only: the original
 * table above it is untouched, and nothing here can be overridden.
 */
export function RevisedDiscrepancySection({
  revised,
  accepted,
  canUndo,
  undoing,
  onUndo,
}: {
  revised: RevisedDiscrepancies;
  accepted: RenameSuggestion[];
  canUndo: boolean;
  undoing: boolean;
  onUndo: (key: string) => void;
}) {
  const { summary } = revised;
  return (
    <section className="space-y-3 rounded border border-blue-200 bg-blue-50/40 p-3 text-sm" aria-labelledby="revised-heading">
      <div>
        <h3 id="revised-heading" className="font-semibold text-slate-800">
          Revised discrepancies ({summary.revised_total})
        </h3>
        <p className="mt-1 text-slate-600">
          Re-checked with the renamings you accepted. The original list above is unchanged: {summary.original_total} originally,{' '}
          {summary.cleared} cleared, {summary.new} new.
        </p>
      </div>

      <ul className="divide-y divide-blue-100" aria-label="Accepted renamings">
        {accepted.map((s) => (
          <li key={s.key} className="flex flex-wrap items-center justify-between gap-2 py-1.5">
            <span className="break-words">
              <span className="font-medium">{s.source_column}</span> is now read as{' '}
              <span className="font-medium">{s.suggested_label ?? s.suggested_role}</span>
              <span className="text-slate-500"> ({s.rule_id})</span>
            </span>
            {canUndo && (
              <button
                type="button"
                onClick={() => onUndo(s.key)}
                disabled={undoing}
                aria-label={`Undo renaming of ${s.source_column}`}
                className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-white disabled:opacity-50"
              >
                Undo
              </button>
            )}
          </li>
        ))}
      </ul>

      {revised.findings.length === 0 ? (
        <p className="rounded bg-green-50 p-2 text-green-800">No discrepancies remain once these columns are read as renamed.</p>
      ) : (
        <DataTable columns={COLUMNS} rows={revised.findings} rowKey={(f) => f.id} caption="Revised discrepancy findings" />
      )}
    </section>
  );
}
