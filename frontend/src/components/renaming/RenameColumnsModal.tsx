import { useState } from 'react';
import type { RenameSuggestion } from '../../api/renamingTypes';
import { RenameSuggestionRow } from './RenameSuggestionList';

/** Popup listing the same suggested renamings as the review screen; the reviewer picks which to apply. */
export function RenameColumnsModal({
  suggestions,
  onSubmit,
  onClose,
  submitting,
  error,
}: {
  suggestions: RenameSuggestion[];
  onSubmit: (keys: string[], note: string) => void;
  onClose: () => void;
  submitting: boolean;
  error?: string | null;
}) {
  const open = suggestions.filter((s) => s.renamable && !s.accepted);
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(open.map((s) => s.key)));
  const [note, setNote] = useState('');

  const toggle = (key: string) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="rename-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-md bg-white p-5 shadow-lg">
        <h2 id="rename-modal-title" className="text-lg font-semibold text-slate-900">
          Rename the columns as suggested
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          Tested against your treaty figures, these columns do not mean what their labels say. Accepting a renaming adds a
          separate <span className="font-medium">revised discrepancies</span> list below the original one; the original findings
          are kept as they are, and you can undo any renaming.
        </p>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (chosen.size > 0) onSubmit([...chosen], note.trim());
          }}
          className="mt-4 space-y-3"
        >
          <ul className="divide-y divide-slate-100 rounded border border-slate-200 px-3">
            {suggestions.map((s) => {
              const selectable = s.renamable && !s.accepted;
              return (
                <li key={s.key} className="flex items-start gap-3 py-2">
                  {selectable ? (
                    <input
                      type="checkbox"
                      id={`rename-${s.key}`}
                      checked={chosen.has(s.key)}
                      onChange={() => toggle(s.key)}
                      aria-label={`Rename ${s.source_column}`}
                      className="mt-1.5"
                    />
                  ) : (
                    <span className="mt-1.5 inline-block w-[13px]" aria-hidden="true" />
                  )}
                  <RenameSuggestionRow s={s} />
                </li>
              );
            })}
          </ul>
          {open.length === 0 && (
            <p className="text-sm text-slate-600">Nothing left to rename - the remaining items need a person to confirm them.</p>
          )}

          <div>
            <label htmlFor="rename-note" className="block text-sm font-medium text-slate-800">
              Note (optional)
            </label>
            <textarea
              id="rename-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              className="mt-1 w-full rounded border border-slate-300 p-2 text-sm"
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-red-700">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || chosen.size === 0}
              className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? 'Renaming...' : `Rename ${chosen.size} column${chosen.size === 1 ? '' : 's'}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
