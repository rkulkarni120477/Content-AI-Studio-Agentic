import { useState, useCallback } from 'react';
import { DEFAULT_PAGE_SIZE } from '@utils/constants';

export function usePagination({ initialPage = 1, initialPageSize = DEFAULT_PAGE_SIZE } = {}) {
  const [page,     setPage]     = useState(initialPage);
  const [pageSize, setPageSize] = useState(initialPageSize);

  const offset = (page - 1) * pageSize;

  const goToPage = useCallback((p) => setPage(p), []);
  const nextPage = useCallback(() => setPage((p) => p + 1), []);
  const prevPage = useCallback(() => setPage((p) => Math.max(1, p - 1)), []);

  const changePageSize = useCallback((size) => {
    setPageSize(size);
    setPage(1);
  }, []);

  const reset = useCallback(() => {
    setPage(1);
  }, []);

  function totalPages(totalItems) {
    return Math.max(1, Math.ceil(totalItems / pageSize));
  }

  return {
    page,
    pageSize,
    offset,
    goToPage,
    nextPage,
    prevPage,
    changePageSize,
    reset,
    totalPages,
  };
}
