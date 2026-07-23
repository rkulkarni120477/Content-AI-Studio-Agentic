import { useEffect, useMemo, useState } from 'react';
import { useSelector } from 'react-redux';
import { useParams } from 'react-router-dom';
import { selectUser } from '@features/auth/authSlice';
import { selectSelectedProject, selectSelectedCourse } from '@features/dashboard/dashboardSlice';
import sourceLibraryApi from '@features/sourceLibrary/services/sourceLibraryApi';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import styles from './SourceLibraryPage.module.scss';

const CACHE_PREFIX = 'cas_dis_source_library_docs';
const UPLOAD_QUEUE_PREFIX = 'cas_dis_source_library_upload_queue';

function uploadQueueKey({ courseId, projectId, clientId }) {
  return `${UPLOAD_QUEUE_PREFIX}:${courseId || projectId || 'global'}:${clientId || 'auto'}`;
}

function readUploadQueue(key) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || 'null');
    return Array.isArray(parsed?.items) ? parsed.items : [];
  } catch {
    return [];
  }
}

function writeUploadQueue(key, items) {
  try {
    window.localStorage.setItem(key, JSON.stringify({ items, updated_at: new Date().toISOString() }));
  } catch {
    // Ignore storage quota/privacy mode issues.
  }
}

function cacheKey({ courseId, projectId, clientId }) {
  return `${CACHE_PREFIX}:${courseId || projectId || 'global'}:${clientId || 'auto'}`;
}

function readCachedDocuments(key) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || 'null');
    return Array.isArray(parsed?.documents) ? parsed.documents : [];
  } catch {
    return [];
  }
}

function writeCachedDocuments(key, documents) {
  try {
    window.localStorage.setItem(key, JSON.stringify({ documents, cached_at: new Date().toISOString() }));
  } catch {
    // Ignore storage quota/privacy mode issues.
  }
}

const PURPOSES = [
  ['', 'All purposes'],
  ['style', 'Style'],
  ['cdd', 'CDD'],
  ['blueprint', 'Blueprint'],
  ['course_generation', 'Title Generation'],
  ['general_reference', 'General Reference'],
];

function unique(values = []) {
  return Array.from(new Set((values || []).filter(Boolean))).sort();
}

function errorMessage(err, fallback) {
  const msg = err?.response?.data?.detail || err?.message || fallback;
  return String(msg || fallback).replace(/^DIS error:\s*/i, '');
}

function formatFileSize(doc = {}) {
  const raw = doc.file_size_bytes ?? doc.size_bytes ?? doc.source_file_size_bytes ?? doc.file_size ?? doc.size;
  const bytes = Number(raw || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export default function SourceLibraryPage() {
  const currentUser = useSelector(selectUser);
  const { courseId } = useParams();
  const selectedProject = useSelector(selectSelectedProject);
  const selectedCourse = useSelector(selectSelectedCourse);
  const scopedCourseId = selectedCourse?.id || courseId || "";
  const scopedProjectId = selectedProject?.id || selectedCourse?.project_id || "";
  const [uiConfig, setUiConfig] = useState(null);
  const [access, setAccess] = useState(null);
  const [activeClientId, setActiveClientId] = useState('');
  const [documents, setDocuments] = useState([]);
  const [filterOptions, setFilterOptions] = useState({});
  const [filters, setFilters] = useState({ purpose: '', document_type: '', status: '', search: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(null);
  const [structure, setStructure] = useState(null);
  const [overview, setOverview] = useState(null);
  const [pages, setPages] = useState(null);
  const [sectionIndex, setSectionIndex] = useState(0);
  const [showSectionList, setShowSectionList] = useState(false);
  const [units, setUnits] = useState(null);
  const [selectedUnit, setSelectedUnit] = useState(null);
  const [sectionLoading, setSectionLoading] = useState(false);
  const [insideSearch, setInsideSearch] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [showUpload, setShowUpload] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [showFolderScan, setShowFolderScan] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState(null);
  const [uploadQueue, setUploadQueue] = useState([]);
  const [showUploadStatusPanel, setShowUploadStatusPanel] = useState(false);
  const [uploadStatusFilters, setUploadStatusFilters] = useState({ status: '', purpose: '', document_type: '' });
  const [isShowingCached, setIsShowingCached] = useState(false);
  const [silentRetryTick, setSilentRetryTick] = useState(0);

  const purposeLabels = uiConfig?.purpose_labels || {};
  const effectiveAccess = access || uiConfig?.access || currentUser || {};
  const selectedClientId = activeClientId || selectedProject?.client_name || effectiveAccess?.client_id || '';
  const docsCacheKey = useMemo(() => cacheKey({ courseId: scopedCourseId, projectId: scopedProjectId, clientId: selectedClientId }), [scopedCourseId, scopedProjectId, selectedClientId]);
  const uploadCacheKey = useMemo(() => uploadQueueKey({ courseId: scopedCourseId, projectId: scopedProjectId, clientId: selectedClientId }), [scopedCourseId, scopedProjectId, selectedClientId]);

  function withScope(params = {}) {
    const next = { ...params };
    if (scopedCourseId) next.course_id = scopedCourseId;
    else if (scopedProjectId) next.project_id = scopedProjectId;
    return next;
  }

  useEffect(() => {
    const cached = readCachedDocuments(docsCacheKey);
    if (cached.length) {
      setDocuments(cached);
      setIsShowingCached(true);
    }
  }, [docsCacheKey]);

  useEffect(() => {
    const cachedQueue = readUploadQueue(uploadCacheKey);
    if (cachedQueue.length) setUploadQueue(cachedQueue);
  }, [uploadCacheKey]);

  async function loadConfig(clientId = selectedClientId) {
    try {
      const cfg = await sourceLibraryApi.uiConfig(withScope(clientId ? { client_id: clientId } : {}));
      setUiConfig(cfg);
      if (cfg.access) {
        setAccess(cfg.access);
        setActiveClientId(cfg.client_id || cfg.access?.client_id || clientId || '');
      }
    } catch (e) {
      // Keep Source Library calm if DIS is temporarily unavailable.
      // CAS keeps working with cached data and refreshes again in the background.
      setError('');
    }
  }

  async function loadDocuments(nextFilters = filters) {
    const cached = readCachedDocuments(docsCacheKey);
    if (cached.length && documents.length === 0) {
      setDocuments(cached);
      setIsShowingCached(true);
    }
    setLoading(true);
    try {
      const data = await sourceLibraryApi.listDocuments(withScope(nextFilters));
      const list = data.documents || data.sources || [];
      setDocuments(list);
      writeCachedDocuments(docsCacheKey, list);
      reconcileUploadQueue(list);
      setIsShowingCached(false);
      setError('');
      setFilterOptions(data.filter_options || {});
      if (data.ui_config && !uiConfig) setUiConfig(data.ui_config);
    } catch (e) {
      const cachedAgain = readCachedDocuments(docsCacheKey);
      if (cachedAgain.length) {
        setDocuments(cachedAgain);
        setIsShowingCached(true);
      }
      // Do not show red DIS-unavailable messages in the product UI.
      // Keep the last known list and retry silently.
      setError('');
      window.clearTimeout(window.__casDisSourceRetryTimer);
      window.__casDisSourceRetryTimer = window.setTimeout(() => {
        setSilentRetryTick((v) => v + 1);
      }, 10000);
    } finally {
      setLoading(false);
    }
  }


  useEffect(() => {
    if (!uploadQueue.length || !documents.length) return;
    const next = uploadQueue.filter((item) => {
      if (String(item.status || '').toLowerCase() === 'failed') return true;
      return !isDocumentProcessedForUpload(item, documents);
    });
    if (next.length !== uploadQueue.length) {
      writeUploadQueue(uploadCacheKey, next);
      setUploadQueue(next);
    }
  }, [documents, uploadQueue.length, uploadCacheKey]); // eslint-disable-line react-hooks/exhaustive-deps


  useEffect(() => {
    if (silentRetryTick > 0) loadDocuments(filters);
  }, [silentRetryTick]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    async function boot() {
      try {
        const profile = await sourceLibraryApi.profile();
        const nextAccess = profile.access || {};
        setAccess(nextAccess);
        setActiveClientId((prev) => prev || nextAccess.client_id || '');
      } catch (_) {
        // Other calls show any real backend/auth issue.
      }
    }
    boot();
  }, []);

  useEffect(() => {
    loadConfig(selectedClientId);
    loadDocuments(filters);
  }, [scopedCourseId, scopedProjectId]); // eslint-disable-line react-hooks/exhaustive-deps

  function updateFilter(key, value) {
    const next = { ...filters, [key]: value };
    setFilters(next);
    loadDocuments(next);
  }

  async function openStructure(doc) {
    const jobId = doc.job_id || doc.document_id;
    setSelected(doc);
    setStructure(null);
    setOverview(null);
    setPages(null);
    setUnits(null);
    setSelectedUnit(null);
    setSectionLoading(false);
    setInsideSearch('');
    setSearchResults(null);
    setSectionIndex(0);
    setShowSectionList(false);
    try {
      const ov = await sourceLibraryApi.getOverview(jobId, withScope({}));
      setOverview(ov);
      const [pg, un] = await Promise.all([
        sourceLibraryApi.getPages(jobId, withScope({ page: 1, page_size: 1 })),
        sourceLibraryApi.getUnits(jobId, withScope({})),
      ]);
      setPages(pg);
      setUnits(un);
      const firstUnit = (un.units || [])[0];
      if (firstUnit?.unit_id) {
        setSectionLoading(true);
        const detail = await sourceLibraryApi.getUnitDetail(jobId, firstUnit.unit_id, withScope({}));
        setSelectedUnit(detail.unit || null);
        setSectionLoading(false);
      }
      // Keep legacy shape for old small-file UI fallbacks.
      setStructure({ preview: ov.preview || '', content_units: un.units || [] });
    } catch (e) {
      setSectionLoading(false);
      setStructure({ error: errorMessage(e, 'Could not load document overview.') });
    }
  }

  async function loadSectionByIndex(nextIndex) {
    if (!selected || !units?.units?.length) return;
    const safeIndex = Math.min(Math.max(0, nextIndex), units.units.length - 1);
    const unit = units.units[safeIndex];
    setSectionIndex(safeIndex);
    setSelectedUnit(null);
    await loadUnit(unit.unit_id);
  }


  async function loadUnit(unitId) {
    if (!selected || !unitId) return;
    const idx = (units?.units || []).findIndex((u) => u.unit_id === unitId);
    if (idx >= 0) setSectionIndex(idx);
    setSelectedUnit(null);
    setSectionLoading(true);
    try {
      const data = await sourceLibraryApi.getUnitDetail(selected.job_id || selected.document_id, unitId, withScope({}));
      setSelectedUnit(data.unit || null);
    } catch (e) {
      setSelectedUnit({ title: 'Error', text: errorMessage(e, 'Could not load content unit.') });
    } finally {
      setSectionLoading(false);
    }
  }

  async function searchInsideDocument(e) {
    e.preventDefault();
    if (!selected || !insideSearch.trim()) {
      setSearchResults(null);
      return;
    }
    try {
      const data = await sourceLibraryApi.searchSource(selected.job_id || selected.document_id, withScope({ q: insideSearch.trim(), limit: 20 }));
      setSearchResults(data);
    } catch (e2) {
      setSearchResults({ error: errorMessage(e2, 'Search failed.') });
    }
  }


  function setPersistedUploadQueue(updater) {
    setUploadQueue((current) => {
      const next = typeof updater === 'function' ? updater(current) : updater;
      writeUploadQueue(uploadCacheKey, next);
      return next;
    });
  }

  function normalizeFileName(value = '') {
    return String(value || '')
      .trim()
      .toLowerCase()
      .replace(/\.[a-z0-9]{2,8}$/i, '')
      .replace(/[_\-]+/g, ' ')
      .replace(/\s+/g, ' ');
  }

  function isDocumentProcessedForUpload(item, docs = documents) {
    const rawName = String(item?.name || '').trim().toLowerCase();
    const name = normalizeFileName(item?.name || '');
    if (!name && !rawName) return false;
    return (docs || []).some((doc) => {
      const docNameRaw = String(doc.source_file_name || doc.title || '').trim().toLowerCase();
      const docTitleRaw = String(doc.title || '').trim().toLowerCase();
      const docName = normalizeFileName(doc.source_file_name || doc.title || '');
      const docTitle = normalizeFileName(doc.title || '');
      const status = String(doc.status || 'processed').toLowerCase();
      const statusOk = ['processed', 'completed', 'ready_for_studio_context_retrieval'].includes(status);
      if (!statusOk) return false;
      return docName === name || docTitle === name || docNameRaw === rawName || docTitleRaw === rawName || docName.includes(name) || name.includes(docName);
    });
  }

  function reconcileUploadQueue(nextDocs = documents) {
    setPersistedUploadQueue((items) => (items || []).filter((item) => {
      if (String(item.status || '').toLowerCase() === 'failed') return true;
      if (isDocumentProcessedForUpload(item, nextDocs)) return false;
      return true;
    }));
  }

  async function handleUpload(e) {
    e.preventDefault();
    const formEl = e.currentTarget;
    const rawForm = new FormData(formEl);
    const selectedFiles = Array.from(formEl.elements.files?.files || []);
    // Folder picker (webkitdirectory): the browser flattens the chosen folder into
    // a file list where each file carries webkitRelativePath ("Folder/sub/f.pdf").
    // Unsupported file types are skipped up front so junk files (.DS_Store etc.)
    // don't show up as failed uploads.
    const folderFiles = Array.from(formEl.elements.folder?.files || []);
    const SUPPORTED_EXT = /\.(pdf|docx?|pptx?|xlsx?|csv|txt|json|jpe?g|png)$/i;
    const folderEntries = folderFiles
      .filter((f) => SUPPORTED_EXT.test(f.name))
      .map((file) => ({ file, relativePath: file.webkitRelativePath || file.name }));
    const skippedUnsupported = folderFiles.length - folderEntries.length;
    const entries = [
      ...selectedFiles.map((file) => ({ file, relativePath: '' })),
      ...folderEntries,
    ];
    if (!entries.length) {
      setError('Please select one or more files, or a folder.');
      return;
    }
    const selectedPurpose = String(rawForm.get('purpose') || 'general_reference');
    const selectedDocumentType = String(rawForm.get('document_type') || '').trim();
    const queue = entries.map((entry, idx) => ({
      id: `${Date.now()}-${idx}`,
      name: entry.relativePath || entry.file.name,
      size: entry.file.size,
      status: 'pending',
      progress: 0,
      purpose: selectedPurpose,
      document_type: selectedDocumentType || 'auto-detect',
      detail: 'Waiting to upload',
    }));
    setPersistedUploadQueue((current) => [...(current || []), ...queue]);
    setUploading(true);
    setUploadProgress(0);
    setError('');
    let failed = 0;

    for (let i = 0; i < entries.length; i += 1) {
      const { file, relativePath } = entries[i];
      setPersistedUploadQueue((q) => q.map((item) => (item.id === queue[i].id ? { ...item, status: 'uploading', progress: 0, detail: 'Uploading to DIS and processing'  } : item)));
      const fd = new FormData();
      fd.append('files', file);
      for (const [key, value] of rawForm.entries()) {
        if (key !== 'files' && key !== 'folder') fd.set(key, value);
      }
      if (relativePath) {
        fd.set('source_relative_path', relativePath);
        fd.set('source_root', relativePath.split('/')[0] || '');
      }
      if (scopedProjectId) fd.set('project_id', scopedProjectId);
      if (scopedCourseId) fd.set('course_id', scopedCourseId);
      try {
        const result = await sourceLibraryApi.uploadDocument(fd, (pct) => {
          setUploadProgress(Math.round(((i + pct / 100) / entries.length) * 100));
          setPersistedUploadQueue((q) => q.map((item) => (item.id === queue[i].id ? { ...item, progress: pct } : item)));
        });
        if (result?.failed) {
          failed += 1;
          setPersistedUploadQueue((q) => q.map((item) => (item.id === queue[i].id ? { ...item, status: 'failed', progress: 100, detail: 'DIS could not process this file. Check backend logs.' } : item)));
        } else {
          setPersistedUploadQueue((q) => q.filter((item) => item.id !== queue[i].id));
          await loadDocuments(filters);
        }
      } catch (err) {
        failed += 1;
        setPersistedUploadQueue((q) => q.map((item) => (item.id === queue[i].id ? { ...item, status: 'failed', progress: 100, detail: errorMessage(err, 'Upload failed.') } : item)));
      }
    }

    const notes = [];
    if (failed) notes.push(`${failed} file(s) failed. Previous processed documents are still available and unaffected.`);
    if (skippedUnsupported) notes.push(`${skippedUnsupported} unsupported file(s) in the folder were skipped.`);
    if (notes.length) setError(notes.join(' '));
    if (!failed) {
      formEl.reset();
    }
    await loadDocuments(filters);
    setUploading(false);
  }

  async function handleFolderScan(e) {
    e.preventDefault();
    const formEl = e.currentTarget;
    const fd = new FormData(formEl);
    const folderPath = String(fd.get('folder_path') || '').trim();
    if (!folderPath) {
      setError('Please enter a folder path from the machine where DIS backend is running.');
      return;
    }
    setScanning(true);
    setError('');
    setScanResult(null);
    try {
      const payload = {
        folder_path: folderPath,
        recursive: fd.get('recursive') === 'on',
        dry_run: fd.get('dry_run') === 'on',
        skip_duplicates: fd.get('skip_duplicates') !== 'off',
        client_id: selectedClientId || undefined,
      };
      const result = await sourceLibraryApi.scanFolder(payload, withScope({}));
      setScanResult(result);
      await loadDocuments(filters);
    } catch (err) {
      setError(errorMessage(err, 'Folder scan failed.'));
    } finally {
      setScanning(false);
    }
  }

  const documentTypeOptions = useMemo(() => unique(filterOptions.document_types), [filterOptions]);
  const statusOptions = useMemo(() => unique([...(filterOptions.statuses || []), ...(documents || []).map((doc) => doc.status || 'processed'), ...(uploadQueue || []).map((item) => item.status)]), [filterOptions, documents, uploadQueue]);
  const uploadStatusItems = useMemo(() => {
    return (uploadQueue || []).filter((item) => {
      if (isDocumentProcessedForUpload(item)) return false;
      if (uploadStatusFilters.status && item.status !== uploadStatusFilters.status) return false;
      if (uploadStatusFilters.purpose && item.purpose !== uploadStatusFilters.purpose) return false;
      if (uploadStatusFilters.document_type && item.document_type !== uploadStatusFilters.document_type) return false;
      return true;
    });
  }, [uploadQueue, uploadStatusFilters, documents]);
  const uploadPurposeOptions = useMemo(() => unique((uploadQueue || []).map((i) => i.purpose)), [uploadQueue]);
  const uploadDocTypeOptions = useMemo(() => unique((uploadQueue || []).map((i) => i.document_type)), [uploadQueue]);
  const displayedDocuments = useMemo(() => {
    const q = String(filters.search || '').trim().toLowerCase();
    return (documents || []).filter((doc) => {
      if (filters.purpose && doc.purpose !== filters.purpose) return false;
      if (filters.document_type && doc.document_type !== filters.document_type) return false;
      if (filters.status && String(doc.status || 'processed') !== filters.status) return false;
      if (!q) return true;
      return [doc.title, doc.source_file_name, doc.document_type, doc.purpose, doc.document_id, doc.job_id]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(q);
    });
  }, [documents, filters]);

  return (
    <PageContainer title="" breadcrumbs={[{ label: 'Source Library' }]} noPadding>
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.eyebrow}>Knowledge Sources</div>
          <h1 className={styles.title}>Source Library</h1>
          <p className={styles.subtitle}>Upload, review, and trace source documents used by Style, CDD, Blueprint, and Title Generation. The list auto-loads from S3 whenever you reopen this page. Project client: <strong>{selectedClientId || 'auto'}</strong>.</p>
        </div>
        <div className={styles.actions}>
          <button type="button" className={`${styles.button} ${styles.buttonSecondary}`} onClick={() => setShowFolderScan((v) => !v)}>Scan Folder</button>
          <button type="button" className={styles.button} onClick={() => setShowUpload((v) => !v)}>+ Upload Sources</button>
        </div>
      </div>

      {error && <div className={styles.errorBox}>{error}</div>}
      {showFolderScan && (
        <section className={styles.card} style={{ marginBottom: 16 }}>
          <form className={styles.cardBody} onSubmit={handleFolderScan}>
            <h2 className={styles.filtersTitle}>Scan server folder</h2>
            <p className={styles.muted}>Bulk ingestion reads a folder on the same machine where DIS backend is running.</p>
            <div className={styles.uploadGrid}>
              <div style={{ gridColumn: '1 / -1' }}>
                <label className={styles.label}>Folder Path</label>
                <input className={styles.input} name="folder_path" placeholder="e.g. C:\\data\\cengage-source-files" />
              </div>
              <label className={styles.checkLabel}><input type="checkbox" name="recursive" defaultChecked /> Include subfolders</label>
              <label className={styles.checkLabel}><input type="checkbox" name="skip_duplicates" defaultChecked /> Skip duplicates</label>
              <label className={styles.checkLabel}><input type="checkbox" name="dry_run" /> Dry run only</label>
            </div>
            <div style={{ marginTop: 12 }}>
              <button type="submit" className={styles.button} disabled={scanning}>{scanning ? 'Scanning…' : 'Start Folder Scan'}</button>
            </div>
            {scanResult && <pre className={styles.pre} style={{ marginTop: 12 }}>{JSON.stringify(scanResult, null, 2)}</pre>}
          </form>
        </section>
      )}

      {showUpload && (
        <section className={styles.card} style={{ marginBottom: 16 }}>
          <form className={styles.cardBody} onSubmit={handleUpload}>
            <h2 className={styles.filtersTitle}>Upload source documents</h2>
            <div className={styles.uploadGrid}>
              <div>
                <label className={styles.label}>Files</label>
                <input className={styles.input} type="file" name="files" multiple accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.csv,.txt,.json,.jpg,.jpeg,.png" />
              </div>
              <div>
                <label className={styles.label}>Or Folder (uploads all supported files inside, keeping subfolders)</label>
                <input className={styles.input} type="file" name="folder" webkitdirectory="" directory="" multiple />
              </div>
              <div>
                <label className={styles.label}>Purpose</label>
                <select className={styles.select} name="purpose" defaultValue="general_reference">
                  {PURPOSES.slice(1).map(([v, l]) => <option key={v} value={v}>{purposeLabels[v] || l}</option>)}
                </select>
              </div>
              <div>
                <label className={styles.label}>Document Type</label>
                <input className={styles.input} name="document_type" placeholder="Optional, auto-detect if blank" />
              </div>
            </div>
            <div style={{ marginTop: 12 }}>
              <button className={styles.button} disabled={uploading}>{uploading ? `Uploading ${uploadProgress}%` : 'Upload and Ingest'}</button>
            </div>
            {uploadStatusItems.length > 0 && (
              <div className={styles.uploadQueue}>
                {uploadStatusItems.map((item) => (
                  <div key={item.id} className={styles.uploadQueueItem}>
                    <div>
                      <strong>{item.name}</strong>
                      <div className={styles.muted}>{Math.round((item.size || 0) / 1024 / 1024 * 10) / 10} MB · {item.detail || item.status}</div>
                    </div>
                    <span className={`${styles.statusPill} ${styles[`status_${item.status}`] || ''}`}>{item.status}</span>
                  </div>
                ))}
              </div>
            )}
          </form>
        </section>
      )}

      {!showUpload && uploadStatusItems.length > 0 && (
        <section className={styles.card} style={{ marginBottom: 16 }}>
          <div className={styles.cardBody}>
            <div className={styles.statusHeader}>
              <h2 className={styles.filtersTitle}>Processing documents ({uploadStatusItems.length})</h2>
              <div className={styles.statusActions}>
                <button type="button" className={`${styles.button} ${styles.buttonSecondary}`} onClick={() => loadDocuments(filters)}>Refresh status</button>
                <button type="button" className={`${styles.button} ${styles.buttonSecondary}`} onClick={() => setShowUploadStatusPanel((v) => !v)}>{showUploadStatusPanel ? 'Hide status' : 'Show status'}</button>
                <button type="button" className={`${styles.button} ${styles.buttonSecondary}`} onClick={() => setShowUpload(true)}>Open upload panel</button>
              </div>
            </div>
            {showUploadStatusPanel && (
              <>
                <div className={styles.statusFilters}>
                  <select className={styles.select} value={uploadStatusFilters.status} onChange={(e) => setUploadStatusFilters((f) => ({ ...f, status: e.target.value }))}>
                    <option value="">All statuses</option>
                    <option value="pending">Pending</option>
                    <option value="uploading">Uploading</option>
                    <option value="failed">Failed</option>
                  </select>
                  <select className={styles.select} value={uploadStatusFilters.purpose} onChange={(e) => setUploadStatusFilters((f) => ({ ...f, purpose: e.target.value }))}>
                    <option value="">All purposes</option>
                    {uploadPurposeOptions.map((v) => <option key={v} value={v}>{purposeLabels[v] || v}</option>)}
                  </select>
                  <select className={styles.select} value={uploadStatusFilters.document_type} onChange={(e) => setUploadStatusFilters((f) => ({ ...f, document_type: e.target.value }))}>
                    <option value="">All document types</option>
                    {uploadDocTypeOptions.map((v) => <option key={v} value={v}>{v}</option>)}
                  </select>
                </div>
                <div className={styles.uploadQueue}>
                  {uploadStatusItems.map((item) => (
                    <div key={item.id} className={styles.uploadQueueItem}>
                      <div>
                        <strong>{item.name}</strong>
                        <div className={styles.muted}>{Math.round((item.size || 0) / 1024 / 1024 * 10) / 10} MB · {purposeLabels[item.purpose] || item.purpose || 'purpose'} · {item.document_type || 'auto-detect'} · {item.detail || item.status}</div>
                      </div>
                      <span className={`${styles.statusPill} ${styles[`status_${item.status}`] || ''}`}>{item.status}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </section>
      )}

      <div className={styles.grid}>
        <aside className={`${styles.card} ${styles.filtersCard}`}>
          <div className={styles.cardBody}>
            <h2 className={styles.filtersTitle}>Filters</h2>
            <label className={styles.label}>Search</label>
            <input className={styles.input} value={filters.search || ''} onChange={(e) => updateFilter('search', e.target.value)} placeholder="File or title" />
            <label className={styles.label}>Purpose</label>
            <select className={styles.select} value={filters.purpose || ''} onChange={(e) => updateFilter('purpose', e.target.value)}>
              {PURPOSES.map(([v, l]) => <option key={v || 'all'} value={v}>{v ? (purposeLabels[v] || l) : l}</option>)}
            </select>
            <label className={styles.label}>Document Type</label>
            <select className={styles.select} value={filters.document_type || ''} onChange={(e) => updateFilter('document_type', e.target.value)}>
              <option value="">All document types</option>
              {documentTypeOptions.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
            <label className={styles.label}>Status</label>
            <select className={styles.select} value={filters.status || ''} onChange={(e) => updateFilter('status', e.target.value)}>
              <option value="">All statuses</option>
              {statusOptions.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
        </aside>

        <section className={`${styles.card} ${styles.docsCard}`}>
          <div className={`${styles.cardBody} ${styles.docsCardBody}`}>
            <h2 className={styles.filtersTitle}>Processed Documents {loading && !documents.length ? '…' : `(${displayedDocuments.length})`}</h2>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead><tr><th>Document</th><th>Type</th><th>Size</th><th>Purpose</th><th>Status</th><th /></tr></thead>
                <tbody>
                  {displayedDocuments.map((doc) => (
                    <tr key={doc.job_id || doc.document_id}>
                      <td><strong>{doc.title || doc.source_file_name}</strong><div className={styles.muted}>{doc.source_file_name}</div></td>
                      <td>{doc.document_type || '—'}</td>
                      <td>{formatFileSize(doc)}</td>
                      <td>{doc.purpose || '—'}</td>
                      <td>{doc.status || 'processed'}</td>
                      <td><button type="button" className={`${styles.button} ${styles.buttonSecondary}`} onClick={() => openStructure(doc)}>View</button></td>
                    </tr>
                  ))}
                  {!displayedDocuments.length && <tr><td colSpan="6" className={styles.empty}>No source documents found.</td></tr>}
                </tbody>
              </table>
            </div>
            {selected && (
              <div className={styles.detail}>
                <div className={styles.detailHeader}>
                  <h3>{selected.title || selected.source_file_name}</h3>
                  <button className={`${styles.button} ${styles.buttonSecondary}`} onClick={() => { setSelected(null); setStructure(null); setOverview(null); setPages(null); setUnits(null); setSelectedUnit(null); }}>Close</button>
                </div>
                {structure?.error ? (
                  <div className={styles.errorBox}>{structure.error}</div>
                ) : (
                  <>
                    {units?.units?.length > 0 && (
                      <div className={styles.sectionsBox}>
                        <div className={styles.sectionNavHeader}>
                          <h4>Sections</h4>
                          <select
                            className={styles.select}
                            value={String(sectionIndex)}
                            onChange={(e) => loadSectionByIndex(Number(e.target.value || 0))}
                          >
                            {units.units.map((u, idx) => (
                              <option key={u.unit_id || idx} value={String(idx)}>{`Section ${idx + 1}`}</option>
                            ))}
                          </select>
                        </div>
                      </div>
                    )}

                    {sectionLoading ? (
                      <div className={styles.unitDetail}>
                        <h4>Loading section…</h4>
                        <textarea className={styles.previewText} readOnly value="" />
                      </div>
                    ) : selectedUnit ? (
                      <div className={styles.unitDetail}>
                        <h4>{`Section ${sectionIndex + 1}`}</h4>
                        <textarea className={styles.previewText} readOnly value={selectedUnit.text || ''} />
                      </div>
                    ) : (
                      <textarea className={styles.previewText} readOnly value={(overview?.preview || structure?.preview || '').slice(0, 30000)} />
                    )}

                  </>
                )}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
    </PageContainer>
  );
}
