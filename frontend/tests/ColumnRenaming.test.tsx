import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { RenameSuggestion, RevisedDiscrepancies } from '../src/api/renamingTypes';
import type { DiscrepancyFinding } from '../src/api/types';
import { DiscrepancyDetailDrawer } from '../src/components/discrepancies/DiscrepancyDetailDrawer';
import { RenameColumnsModal } from '../src/components/renaming/RenameColumnsModal';
import { RevisedDiscrepancySection } from '../src/components/renaming/RevisedDiscrepancySection';

function suggestion(over: Partial<RenameSuggestion> = {}): RenameSuggestion {
  return {
    key: 'MCR-D1:gross_premium',
    rule_id: 'MCR-D1',
    field: 'gross_premium',
    source_column: 'Gross Premium',
    current_meaning: 'gross_premium',
    suggested_role: 'ceded_premium',
    suggested_label: 'Ceded Premium',
    flag: 'MEANING_CONFLICT',
    renamable: true,
    agreement: '1.0000',
    rows_evaluated: 8,
    evidence_text: 'Column equals cess% of the implied gross in 100% of rows - it is the ceded share, not the full gross.',
    evidence: {},
    accepted: false,
    decided_at: null,
    ...over,
  };
}

const finding: DiscrepancyFinding = {
  id: 'f1', check_id: 'MCR-B1', category: 'TREATY_RATIO', severity: 'ERROR', status: 'UNRESOLVED',
  affected_row_number: 1, affected_field: null, expected_value: '1', observed_value: '2', variance: '1',
  message: 'm', evidence: null,
};

describe('DiscrepancyDetailDrawer', () => {
  it('shows the rename button beside override only when a renaming is offered', () => {
    const { rerender } = render(
      <DiscrepancyDetailDrawer finding={finding} onClose={() => {}} onOverride={() => {}} canOverride />,
    );
    expect(screen.getByRole('button', { name: 'Override this finding' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /rename the column as suggested/i })).toBeNull();

    const onRename = vi.fn();
    rerender(
      <DiscrepancyDetailDrawer finding={finding} onClose={() => {}} onOverride={() => {}} canOverride onRenameColumn={onRename} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /rename the column as suggested/i }));
    expect(onRename).toHaveBeenCalled();
  });
});

describe('RenameColumnsModal', () => {
  it('lists the suggestions, pre-selects renamable ones and submits the chosen keys', () => {
    const onSubmit = vi.fn();
    render(
      <RenameColumnsModal
        suggestions={[
          suggestion(),
          suggestion({ key: 'MCR-D4:extra_net', rule_id: 'MCR-D4', renamable: false, suggested_role: null, suggested_label: null, flag: 'AMBIGUOUS', evidence_text: 'Does not fit any premium identity.' }),
        ]}
        onSubmit={onSubmit}
        onClose={() => {}}
        submitting={false}
      />,
    );
    expect(screen.getAllByText(/ceded share, not the full gross/i).length).toBe(1);
    expect(screen.getAllByRole('checkbox')).toHaveLength(1); // the ambiguous one cannot be selected
    fireEvent.click(screen.getByRole('button', { name: 'Rename 1 column' }));
    expect(onSubmit).toHaveBeenCalledWith(['MCR-D1:gross_premium'], '');
  });
});

describe('RevisedDiscrepancySection', () => {
  const revised: RevisedDiscrepancies = {
    active: true,
    findings: [],
    summary: { original_total: 8, revised_total: 0, cleared: 8, new: 0 },
  };

  it('is shown separately, reports the change and offers undo', () => {
    const onUndo = vi.fn();
    render(
      <RevisedDiscrepancySection revised={revised} accepted={[suggestion({ accepted: true })]} canUndo undoing={false} onUndo={onUndo} />,
    );
    expect(screen.getByText(/Revised discrepancies \(0\)/)).toBeTruthy();
    expect(screen.getByText(/8 originally, 8 cleared, 0 new/)).toBeTruthy();
    expect(screen.getByText(/No discrepancies remain/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /undo renaming of gross premium/i }));
    expect(onUndo).toHaveBeenCalledWith('MCR-D1:gross_premium');
  });
});
