import type { NotEvaluatedRule, RenameSuggestion } from '../../api/renamingTypes';
import { RenameSuggestionList } from './RenameSuggestionList';

/** Shown under the list of money-column relations: columns whose label looks wrong (MCR-D1..D5). */
export function SuggestedRenamingsSection({
  suggestions,
  notEvaluated,
}: {
  suggestions: RenameSuggestion[];
  notEvaluated: NotEvaluatedRule[];
}) {
  if (suggestions.length === 0) return null;
  return (
    <section className="rounded border border-amber-200 bg-amber-50/50 p-3 text-sm" aria-labelledby="rename-suggestions-heading">
      <h3 id="rename-suggestions-heading" className="font-semibold text-slate-800">
        Suggested column renamings ({suggestions.length})
      </h3>
      <p className="mt-1 text-slate-600">
        Tested against your treaty figures, these columns do not appear to mean what their labels say. Nothing has been renamed:
        open a discrepancy and choose "Rename the column as suggested" to apply any of them. Applying one adds a separate list of
        revised discrepancies; the original list stays as it is.
      </p>
      <div className="mt-2">
        <RenameSuggestionList suggestions={suggestions} />
      </div>
      {notEvaluated.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs text-slate-500">
          {notEvaluated.map((n) => (
            <li key={`${n.rule_id}-${n.field}`}>
              {n.rule_id} not checked for {n.field}: {n.reason}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
