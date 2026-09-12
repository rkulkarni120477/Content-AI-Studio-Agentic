import { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import sourceLibraryApi from '@features/sourceLibrary/services/sourceLibraryApi';
import styles from '../../pages/MetadataEditorPage/MetadataEditorPage.module.scss';

function errorMessage(err, fallback) {
  return err?.response?.data?.detail || err?.message || fallback;
}

export default function DocumentContentPanel({ jobId, scopeParams = {} }) {
  const [overview, setOverview] = useState(null);
  const [units, setUnits] = useState(null);
  const [structureError, setStructureError] = useState('');
  const [selectedUnit, setSelectedUnit] = useState(null);
  const [sectionIndex, setSectionIndex] = useState(0);
  const [sectionLoading, setSectionLoading] = useState(false);
  const [retagging, setRetagging] = useState(false);
  const [retagMessage, setRetagMessage] = useState('');

  async function fetchUnitDetail(unitId, unitList) {
    if (!jobId || !unitId) return;
    const list = unitList || [];
    const idx = list.findIndex((u) => u.unit_id === unitId);
    if (idx >= 0) setSectionIndex(idx);
    setSelectedUnit(null);
    setSectionLoading(true);
    try {
      const data = await sourceLibraryApi.getUnitDetail(jobId, unitId, scopeParams);
      setSelectedUnit(data.unit || null);
    } catch (e) {
      setSelectedUnit({ title: 'Error', text: errorMessage(e, 'Could not load content unit.') });
    } finally {
      setSectionLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function boot() {
      if (!jobId) return;
      setStructureError('');
      setOverview(null);
      setUnits(null);
      setSelectedUnit(null);
      setRetagMessage('');
      setSectionIndex(0);
      try {
        const ov = await sourceLibraryApi.getOverview(jobId, scopeParams);
        if (cancelled) return;
        setOverview(ov);
        const un = await sourceLibraryApi.getUnits(jobId, scopeParams);
        if (cancelled) return;
        setUnits(un);
        const firstUnit = (un.units || [])[0];
        if (firstUnit?.unit_id) {
          setSectionLoading(true);
          const detail = await sourceLibraryApi.getUnitDetail(jobId, firstUnit.unit_id, scopeParams);
          if (cancelled) return;
          setSelectedUnit(detail.unit || null);
          setSectionLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setSectionLoading(false);
          setStructureError(errorMessage(e, 'Could not load document overview.'));
        }
      }
    }
    boot();
    return () => { cancelled = true; };
    // Intentionally boot once per jobId / scope identity
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, JSON.stringify(scopeParams)]);

  async function loadSectionByIndex(nextIndex) {
    if (!units?.units?.length) return;
    const safeIndex = Math.min(Math.max(0, nextIndex), units.units.length - 1);
    const unit = units.units[safeIndex];
    setSectionIndex(safeIndex);
    await fetchUnitDetail(unit.unit_id, units.units);
  }

  async function handleRetag({ unitIds = null, allFailed = false } = {}) {
    setRetagging(true);
    setRetagMessage('');
    try {
      const payload = allFailed ? { all_failed: true } : { unit_ids: unitIds || [] };
      const result = await sourceLibraryApi.retagContent(jobId, payload, scopeParams);
      const failedLeft = Number(result?.tagging_failed_count || 0);
      const n = Number(result?.retagged || 0);
      setRetagMessage(
        failedLeft > 0
          ? `Re-tagged ${n} page(s); ${failedLeft} still need tagging.`
          : `Re-tagged ${n} page(s) successfully.`,
      );
      const ov = await sourceLibraryApi.getOverview(jobId, scopeParams);
      setOverview(ov);
      const un = await sourceLibraryApi.getUnits(jobId, scopeParams);
      setUnits(un);
      const list = un.units || [];
      const current = list[sectionIndex] || list[0];
      if (current?.unit_id) {
        await fetchUnitDetail(current.unit_id, list);
      } else {
        setSelectedUnit(null);
      }
    } catch (e) {
      setRetagMessage(errorMessage(e, 'Re-tag failed.'));
    } finally {
      setRetagging(false);
    }
  }

  if (structureError) {
    return <div className={styles.error}>{structureError}</div>;
  }

  const failedCount = Number(overview?.tagging_failed_count ?? units?.tagging_failed_count ?? 0);
  const pendingCount = Number(overview?.tagging_pending_count ?? units?.tagging_pending_count ?? 0);
  const needsRetry = failedCount + pendingCount;
  const unitStatus = String(
    selectedUnit?.metadata?.tagging_status
    || units?.units?.[sectionIndex]?.tagging_status
    || '',
  ).toLowerCase();
  const unitNeedsRetry = unitStatus === 'failed' || unitStatus === 'pending';

  return (
    <div className={styles.contentPanel}>
      {needsRetry > 0 && (
        <div className={styles.taggingBanner}>
          <span className={styles.taggingWarn}>
            {needsRetry} page{needsRetry === 1 ? '' : 's'} need content tagging
            {failedCount ? ` (${failedCount} failed)` : ''}
            {pendingCount ? ` (${pendingCount} pending)` : ''}
          </span>
          <button
            type="button"
            className={styles.contentButton}
            disabled={retagging}
            onClick={() => handleRetag({ allFailed: true })}
          >
            {retagging ? 'Retrying…' : 'Retry all failed'}
          </button>
        </div>
      )}
      {retagMessage && <div className={styles.mutedNote}>{retagMessage}</div>}

      {units?.units?.length > 0 && (
        <div className={styles.sectionsBox}>
          <div className={styles.sectionNavHeader}>
            <h4 className={styles.sectionTitle}>Sections</h4>
            <select
              className={styles.contentSelect}
              value={String(sectionIndex)}
              onChange={(e) => loadSectionByIndex(Number(e.target.value || 0))}
            >
              {units.units.map((u, idx) => {
                const st = String(u.tagging_status || '').toLowerCase();
                const mark = (st === 'failed' || st === 'pending') ? ` [${st}]` : '';
                const label = u.page_number
                  ? `p. ${u.page_number}${mark}`
                  : `Section ${idx + 1}${mark}`;
                return (
                  <option key={u.unit_id || idx} value={String(idx)}>{label}</option>
                );
              })}
            </select>
          </div>
        </div>
      )}

      {sectionLoading ? (
        <div className={styles.unitDetail}>
          <h4 className={styles.sectionTitle}>Loading section…</h4>
          <textarea className={styles.previewText} readOnly value="" />
        </div>
      ) : selectedUnit ? (
        <div className={styles.unitDetail}>
          <div className={styles.sectionNavHeader}>
            <h4 className={styles.sectionTitle}>
              {`Section ${sectionIndex + 1}`}
              {selectedUnit.metadata?.page_number ? ` · p. ${selectedUnit.metadata.page_number}` : ''}
              {unitNeedsRetry && (
                <span className={`${styles.statusPill} ${styles.statusFailed}`}>
                  {selectedUnit.metadata?.tagging_status
                    || units?.units?.[sectionIndex]?.tagging_status}
                </span>
              )}
            </h4>
            {unitNeedsRetry && (
              <button
                type="button"
                className={styles.contentButtonSecondary}
                disabled={retagging}
                onClick={() => handleRetag({
                  unitIds: [
                    selectedUnit.unit_id
                    || selectedUnit.content_unit_id
                    || units?.units?.[sectionIndex]?.unit_id,
                  ].filter(Boolean),
                })}
              >
                {retagging ? 'Retrying…' : 'Retry this page'}
              </button>
            )}
          </div>
          {(selectedUnit.metadata?.tagging_error
            || units?.units?.[sectionIndex]?.tagging_error) && (
            <div className={styles.mutedNote}>
              {selectedUnit.metadata?.tagging_error
                || units?.units?.[sectionIndex]?.tagging_error}
            </div>
          )}
          <textarea className={styles.previewText} readOnly value={selectedUnit.text || ''} />
        </div>
      ) : (
        <textarea
          className={styles.previewText}
          readOnly
          value={(overview?.preview || '').slice(0, 30000)}
        />
      )}
    </div>
  );
}

DocumentContentPanel.propTypes = {
  jobId: PropTypes.string.isRequired,
  scopeParams: PropTypes.object,
};
