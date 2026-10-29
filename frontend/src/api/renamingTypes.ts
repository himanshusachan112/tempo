/** Suggested column renamings (MCR-D1..D5) and the revised discrepancies they produce. */
import type { DiscrepancyFinding } from './types';

export type RenameFlag = 'MEANING_CONFLICT' | 'BUNDLED' | 'PARTIAL_NET' | 'AMBIGUOUS';

export interface RenameSuggestion {
  /** Stable id, e.g. "MCR-D1:gross_premium". */
  key: string;
  rule_id: string;
  field: string;
  /** The column's header as it appears in the uploaded file. */
  source_column: string;
  current_meaning: string;
  suggested_role: string | null;
  suggested_label: string | null;
  flag: RenameFlag | string;
  /** False for ambiguous columns: a person must confirm what they are, nothing is renamed. */
  renamable: boolean;
  agreement: string;
  rows_evaluated: number;
  evidence_text: string;
  evidence: Record<string, unknown>;
  accepted: boolean;
  decided_at: string | null;
}

export interface NotEvaluatedRule {
  rule_id: string;
  field: string;
  reason: string;
}

export interface RevisedFinding extends DiscrepancyFinding {
  source: 'recomputed' | 'carried_over';
}

export interface RevisedSummary {
  original_total: number;
  revised_total: number;
  cleared: number;
  new: number;
}

export interface RevisedDiscrepancies {
  /** False until at least one renaming has been accepted. */
  active: boolean;
  findings: RevisedFinding[];
  summary: RevisedSummary;
}

export interface ColumnRenamingWorkspace {
  /** Renamings can only be changed while the discrepancy review is open. */
  editable: boolean;
  suggestions: RenameSuggestion[];
  not_evaluated: NotEvaluatedRule[];
  accepted_count: number;
  revised: RevisedDiscrepancies;
}
