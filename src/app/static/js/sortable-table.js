/**
 * Alpine.js data component for sortable tables.
 *
 * Usage (via x-data on a table wrapper):
 *   x-data="sortableTable({ rows: [...], sortKey: 'label', sortDir: 'asc' })"
 *
 * Provides:
 *   - sortedRows: computed sorted array (excludes summary/total row)
 *   - sort(key): toggle sort on a column
 *   - sortKey / sortDir: current sort state
 */
document.addEventListener('alpine:init', () => {
  Alpine.data('sortableTable', ({ rows, sortKey, sortDir }) => ({
    rows: rows.map((r, i) => ({ ...r, _idx: i })),
    sortKey: sortKey,
    sortDir: sortDir,

    get sortedRows() {
      const key = this.sortKey;
      const dir = this.sortDir;
      return [...this.rows].sort((a, b) => {
        let aVal = a[key];
        let bVal = b[key];

        // Treat null/undefined as smallest
        if (aVal == null) aVal = dir === 'asc' ? -Infinity : Infinity;
        if (bVal == null) bVal = dir === 'asc' ? -Infinity : Infinity;

        if (typeof aVal === 'string' && typeof bVal === 'string') {
          return dir === 'asc'
            ? aVal.localeCompare(bVal)
            : bVal.localeCompare(aVal);
        }

        const aNum = Number(aVal) || 0;
        const bNum = Number(bVal) || 0;
        return dir === 'asc' ? aNum - bNum : bNum - aNum;
      });
    },

    sort(key) {
      if (this.sortKey === key) {
        this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        this.sortKey = key;
        this.sortDir = 'asc';
      }
    }
  }));
});
