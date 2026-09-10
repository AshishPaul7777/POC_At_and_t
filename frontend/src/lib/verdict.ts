/**
 * The verdict vocabulary, in one place.
 *
 * The same four values are shown in the component table, the filter bar, the
 * dashboard tiles, the graph legend and the code explorer. Each of those used
 * to render the enum its own way -- `NEEDS_REVIEW`, `NEEDS REVIEW` and "needs a
 * human decision" all appeared in the UI for the same thing. A reviewer reading
 * two of those surfaces cannot tell whether they are looking at one category or
 * two, so the wording lives here and the surfaces import it.
 */
import type { Verdict } from './api'

/** Display order everywhere: what to act on first, then what to ignore. */
export const VERDICT_ORDER: Verdict[] = [
  'UNUSED', 'NEEDS_REVIEW', 'USED', 'OUT_OF_SCOPE',
]

export const VERDICT_LABEL: Record<Verdict, string> = {
  USED: 'Used',
  UNUSED: 'Unused',
  NEEDS_REVIEW: 'Needs review',
  OUT_OF_SCOPE: 'Out of scope',
}

/**
 * One line a non-Salesforce reader can act on.
 *
 * These are deliberately about *what to do*, not about how the analysis works.
 * "Nothing refers to it" is the finding; the reasoning behind it belongs in the
 * detail panel, not in a filter button's tooltip.
 */
export const VERDICT_DEFINITION: Record<Verdict, string> = {
  USED: 'Something in the org refers to this. Leave it alone.',
  UNUSED: 'Nothing refers to it and no data uses it. A deletion candidate.',
  NEEDS_REVIEW: 'The checks disagreed, or something blocked a confident answer.'
    + ' A person needs to decide.',
  OUT_OF_SCOPE: 'Not ours to delete -- part of a managed package, or not'
    + ' deletable through the metadata API.',
}

/** Falls back to the raw enum rather than rendering blank on an unknown value. */
export const verdictLabel = (v: Verdict | null | undefined): string =>
  (v && VERDICT_LABEL[v]) ?? 'Not classified'

/** CSS class suffix. Matches the `.badge.USED` rules already in styles.css. */
export const verdictClass = (v: Verdict | null | undefined): string =>
  v ?? 'UNCLASSIFIED'

/**
 * Confidence as a band rather than a bare integer.
 *
 * The number is a ranking device for the review queue -- it is not a
 * probability, and showing `66` invites a precision the score does not have.
 * The band is what a reader should act on; the number stays in parentheses for
 * anyone comparing two rows.
 */
export function confidenceBand(n: number | null | undefined): string {
  if (n == null) return 'Unknown'
  if (n >= 80) return 'High'
  if (n >= 60) return 'Medium'
  return 'Low'
}
