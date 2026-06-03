import { useState, useMemo, useCallback } from 'react';
import { cn } from '@utils/helpers';
import Loader from '../Loader/Loader';
import EmptyState from '../EmptyState/EmptyState';
import Pagination from '../Pagination/Pagination';
import styles from './Table.module.scss';

/**
 * Generic reusable table.
 *
 * columns: Array<{ key, header, render?, sortable?, width?, align? }>
 * rows: Array of data objects
 * onRowClick?: (row) => void
 * serverSide?: boolean — if true, sorting/pagination is external
 */
export default function Table({
  columns    = [],
  rows       = [],
  rowKey     = 'id',
  isLoading  = false,
  emptyTitle,
  emptyMessage,
  emptyAction,
  emptyActionLabel,
  onRowClick,
  // Pagination
  pagination = false,
  pageSize   = 20,
  // Sorting
  defaultSortKey,
  defaultSortDir = 'asc',
  // Server-side overrides
  serverSide        = false,
  totalCount,
  currentPage,
  onPageChange,
  onSortChange,
  className,
}) {
  const [sortKey, setSortKey] = useState(defaultSortKey || null);
  const [sortDir, setSortDir] = useState(defaultSortDir);
  const [page, setPage]       = useState(1);

  // Client-side sort
  const sorted = useMemo(() => {
    if (serverSide || !sortKey) return rows;
    return [...rows].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      const cmp = av == null ? 1 : bv == null ? -1
        : typeof av === 'string' ? av.localeCompare(bv)
        : av - bv;
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [rows, sortKey, sortDir, serverSide]);

  // Client-side pagination
  const paged = useMemo(() => {
    if (serverSide || !pagination) return sorted;
    const start = (page - 1) * pageSize;
    if (!Array.isArray(sorted)) return [];
    return sorted.slice(start, start + pageSize);
  }, [sorted, page, pageSize, pagination, serverSide]);

  const total = serverSide ? (totalCount ?? rows.length) : rows.length;
  const activePage = serverSide ? currentPage : page;

  const handleSort = useCallback((key) => {
    if (serverSide) {
      const dir = sortKey === key && sortDir === 'asc' ? 'desc' : 'asc';
      setSortKey(key); setSortDir(dir);
      onSortChange?.({ key, dir });
      return;
    }
    setSortDir((d) => sortKey === key ? (d === 'asc' ? 'desc' : 'asc') : 'asc');
    setSortKey(key);
    setPage(1);
  }, [sortKey, sortDir, serverSide, onSortChange]);

  const handlePage = useCallback((p) => {
    if (serverSide) { onPageChange?.(p); return; }
    setPage(p);
  }, [serverSide, onPageChange]);

  if (isLoading) {
    return (
      <div className={cn(styles.wrapper, className)}>
        <div className={styles.loadingState}><Loader size="lg" /></div>
      </div>
    );
  }

  return (
    <div className={cn(styles.wrapper, className)}>
      <div className={styles.tableScroll}>
        <table className={styles.table} role="table">
          <thead>
            <tr className={styles.thead__row}>
              {columns.map((col) => (
                <th
                  key={col.key}
                  className={cn(
                    styles.th,
                    col.sortable && styles['th--sortable'],
                    col.align && styles[`th--${col.align}`],
                  )}
                  style={col.width ? { width: col.width } : undefined}
                  onClick={col.sortable ? () => handleSort(col.key) : undefined}
                  aria-sort={col.sortable && sortKey === col.key
                    ? (sortDir === 'asc' ? 'ascending' : 'descending')
                    : undefined}
                >
                  {col.header}
                  {col.sortable && (
                    <span className={styles.th__sort} aria-hidden="true">
                      {sortKey === col.key ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ' ↕'}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className={styles.emptyCell}>
                  <EmptyState
                    title={emptyTitle}
                    message={emptyMessage}
                    action={emptyAction}
                    actionLabel={emptyActionLabel}
                  />
                </td>
              </tr>
            ) : (
              paged.map((row) => (
                <tr
                  key={row[rowKey]}
                  className={cn(styles.tr, onRowClick && styles['tr--clickable'])}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  onKeyDown={onRowClick ? (e) => e.key === 'Enter' && onRowClick(row) : undefined}
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={cn(styles.td, col.align && styles[`td--${col.align}`])}
                    >
                      {col.render ? col.render(row[col.key], row) : (row[col.key] ?? '—')}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {pagination && total > pageSize && (
        <div className={styles.footer}>
          <span className={styles.footer__count}>
            {total} result{total !== 1 ? 's' : ''}
          </span>
          <Pagination
            current={activePage}
            total={Math.ceil(total / pageSize)}
            onChange={handlePage}
          />
        </div>
      )}
    </div>
  );
}
