import type { RenameSuggestion } from '../../api/renamingTypes';

const FLAG_LABEL: Record<string, string> = {
  MEANING_CONFLICT: 'Meaning conflict',
  BUNDLED: 'Bundled columns',
  PARTIAL_NET: 'Partial net',
  AMBIGUOUS: 'Needs your confirmation',
};

export function flagClass(flag: string): string {
  return flag === 'AMBIGUOUS' ? 'bg-slate-100 text-slate-700' : 'bg-amber-100 text-amber-800';
}

/** One suggestion, read-only. Used by the inline section and (with a checkbox) by the rename popup. */
export function RenameSuggestionRow({ s }: { s: RenameSuggestion }) {
  return (
    <div className="min-w-0">
      <p className="flex flex-wrap items-center gap-2 break-words font-medium text-slate-800">
        <span>{s.source_column}</span>
        <span aria-hidden="true" className="text-slate-400">
          {'->'}
        </span>
        {s.suggested_label ? (
          <span>{s.suggested_label}</span>
        ) : (
          <span className="text-slate-600">no rename - review needed</span>
        )}
        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${flagClass(s.flag)}`}>
          {FLAG_LABEL[s.flag] ?? s.flag}
        </span>
        {s.accepted && <span className="rounded bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800">Renamed</span>}
      </p>
      <p className="mt-0.5 text-sm text-slate-600">{s.evidence_text}</p>
      <p className="text-xs text-slate-400">
        {s.rule_id} - read as {s.current_meaning.replace(/_/g, ' ')} - {s.rows_evaluated} rows checked
      </p>
    </div>
  );
}

export function RenameSuggestionList({ suggestions }: { suggestions: RenameSuggestion[] }) {
  return (
    <ul className="divide-y divide-slate-100">
      {suggestions.map((s) => (
        <li key={s.key} className="py-2">
          <RenameSuggestionRow s={s} />
        </li>
      ))}
    </ul>
  );
}
