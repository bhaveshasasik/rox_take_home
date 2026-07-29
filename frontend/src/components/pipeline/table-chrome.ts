/**
 * Shared table styling, so the pipeline list and the contacts table stay
 * visually identical without either duplicating Tailwind strings.
 *
 * Deliberately not a generic `<DataTable>`: the pipeline table is coupled to
 * server-side sorting and its `sortKey` column meta, which the contacts table
 * has no use for. Sharing the chrome keeps them consistent; sharing the
 * component would force one of them to carry the other's complexity.
 */

export const TABLE_HEAD =
  "border-border text-muted-foreground border-b px-3 py-2 " +
  "text-[11px] font-medium tracking-wider uppercase";

export const TABLE_ROW =
  "border-border hover:bg-accent/40 border-b transition-colors last:border-0";

export const TABLE_CELL = "py-2 align-top text-[12px]";
