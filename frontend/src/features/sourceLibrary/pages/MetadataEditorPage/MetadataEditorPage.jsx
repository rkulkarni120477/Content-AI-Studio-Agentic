import { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useSelector } from 'react-redux';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Modal from '@components/common/Modal/Modal';
import Button from '@components/common/Button/Button';
import Loader from '@components/common/Loader/Loader';
import { selectSelectedProject } from '@features/dashboard/dashboardSlice';
import { selectIsAdmin } from '@features/auth/authSlice';
import sourceLibraryApi from '@features/sourceLibrary/services/sourceLibraryApi';
import DocumentSummaryPanel from '@features/sourceLibrary/components/metadataEditor/DocumentSummaryPanel';
import DocumentContentPanel from '@features/sourceLibrary/components/metadataEditor/DocumentContentPanel';
import MetadataField from '@features/sourceLibrary/components/metadataEditor/MetadataField';
import MetadataTagInput from '@features/sourceLibrary/components/metadataEditor/MetadataTagInput';
import MetadataEditorFooter from '@features/sourceLibrary/components/metadataEditor/MetadataEditorFooter';
import ProvenanceBanner from '@features/sourceLibrary/components/metadataEditor/ProvenanceBanner';
import styles from './MetadataEditorPage.module.scss';

const EDIT_TABS = [
  { id: 'ai', label: 'AI Metadata' },
  { id: 'taxonomy', label: 'Taxonomy & Standards' },
  { id: 'relationships', label: 'Relationships' },
];

const VIEW_TABS = [
  ...EDIT_TABS,
  { id: 'content', label: 'Content' },
];

const DOCUMENT_SECTION = 'document';

const EMPTY_FORM = {
  ai_metadata: {
    title: '', author: '', subject: '', language: '', description: '', keywords: [],
  },
  taxonomy_standards: {
    subject_area: '', domain: '', subdomain: '', blooms_level: '', skill_level: '',
    learning_standards: [], skills_mapped: [],
  },
  relationships: {
    series_collection: '', related_documents: [], prerequisites: [], cross_references: [],
  },
};

function cloneForm(data) {
  return JSON.parse(JSON.stringify(data || EMPTY_FORM));
}

function formFromResponse(data) {
  return {
    ai_metadata: {
      title: data?.ai_metadata?.title || '',
      author: data?.ai_metadata?.author || '',
      subject: data?.ai_metadata?.subject || '',
      language: data?.ai_metadata?.language || '',
      description: data?.ai_metadata?.description || '',
      keywords: [...(data?.ai_metadata?.keywords || [])],
    },
    taxonomy_standards: {
      subject_area: data?.taxonomy_standards?.subject_area || '',
      domain: data?.taxonomy_standards?.domain || '',
      subdomain: data?.taxonomy_standards?.subdomain || '',
      blooms_level: data?.taxonomy_standards?.blooms_level || '',
      skill_level: data?.taxonomy_standards?.skill_level || '',
      learning_standards: [...(data?.taxonomy_standards?.learning_standards || [])],
      skills_mapped: [...(data?.taxonomy_standards?.skills_mapped || [])],
    },
    relationships: {
      series_collection: data?.relationships?.series_collection || '',
      related_documents: [...(data?.relationships?.related_documents || [])],
      prerequisites: [...(data?.relationships?.prerequisites || [])],
      cross_references: [...(data?.relationships?.cross_references || [])],
    },
  };
}

function formFromUnit(unit, listUnit = null) {
  const meta = unit?.metadata || {};
  const topics = unit?.topics || meta.topics || listUnit?.topics || [];
  const acs = meta.acs_codes || listUnit?.acs_codes || [];
  const summary = meta.summary || listUnit?.summary || '';
  return {
    ai_metadata: {
      title: unit?.title || listUnit?.title || '',
      author: '',
      subject: '',
      language: '',
      description: String(summary || ''),
      keywords: [...(Array.isArray(topics) ? topics : [])],
    },
    taxonomy_standards: {
      subject_area: '',
      domain: '',
      subdomain: '',
      blooms_level: '',
      skill_level: '',
      learning_standards: [...(Array.isArray(acs) ? acs : [])],
      skills_mapped: [],
    },
    relationships: {
      series_collection: '',
      related_documents: [],
      prerequisites: [],
      cross_references: [],
    },
  };
}

function errorMessage(err, fallback) {
  return err?.response?.data?.detail || err?.message || fallback;
}

export default function MetadataEditorPage() {
  const { courseId, jobId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const project = useSelector(selectSelectedProject);
  const isAdmin = useSelector(selectIsAdmin);

  const isView = /\/view\/?$/.test(location.pathname);
  const tabs = isView ? VIEW_TABS : EDIT_TABS;

  const activeTab = tabs.some((t) => t.id === searchParams.get('tab'))
    ? searchParams.get('tab')
    : 'ai';

  const selectedSectionId = searchParams.get('section') || DOCUMENT_SECTION;
  const isDocumentScope = selectedSectionId === DOCUMENT_SECTION;

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [serverData, setServerData] = useState(null);
  const [documentForm, setDocumentForm] = useState(cloneForm());
  const [savedDocumentForm, setSavedDocumentForm] = useState(cloneForm());
  const [sectionForm, setSectionForm] = useState(cloneForm());
  const [savedSectionForm, setSavedSectionForm] = useState(cloneForm());
  const [picker, setPicker] = useState(null);
  const [pickerDocs, setPickerDocs] = useState([]);
  const [pickerSearch, setPickerSearch] = useState('');

  const [overview, setOverview] = useState(null);
  const [unitsPayload, setUnitsPayload] = useState(null);
  const [selectedUnit, setSelectedUnit] = useState(null);
  const [sectionLoading, setSectionLoading] = useState(false);
  const [structureError, setStructureError] = useState('');
  const [retagging, setRetagging] = useState(false);
  const [retagMessage, setRetagMessage] = useState('');
  const [retagProgress, setRetagProgress] = useState(null);
  const [reindexing, setReindexing] = useState(false);

  const scopeParams = useMemo(() => ({
    course_id: courseId,
    project_id: project?.id,
  }), [courseId, project?.id]);

  const units = unitsPayload?.units || [];
  const sectionIndex = Math.max(0, units.findIndex((u) => String(u.unit_id) === String(selectedSectionId)));
  const listUnit = !isDocumentScope && sectionIndex >= 0 ? units[sectionIndex] : null;

  const form = isDocumentScope ? documentForm : sectionForm;
  const savedForm = isDocumentScope ? savedDocumentForm : savedSectionForm;

  const isDirty = useMemo(
    () => !isView && JSON.stringify(form) !== JSON.stringify(savedForm),
    [form, savedForm, isView],
  );

  const keywordsMax = isDocumentScope
    ? (serverData?.limits?.keywords_max || 12)
    : 8;
  const pageTitle = isView ? 'View document' : 'Edit metadata';
  const isSyntheticSection = String(selectedSectionId || '').startsWith('view_page_');

  const loadMetadata = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await sourceLibraryApi.getMetadata(jobId, scopeParams);
      const nextForm = formFromResponse(data);
      setServerData(data);
      setDocumentForm(nextForm);
      setSavedDocumentForm(cloneForm(nextForm));
    } catch (err) {
      setError(errorMessage(err, 'Could not load metadata.'));
    } finally {
      setLoading(false);
    }
  }, [jobId, scopeParams]);

  const loadUnits = useCallback(async () => {
    setStructureError('');
    try {
      const ov = await sourceLibraryApi.getOverview(jobId, scopeParams);
      setOverview(ov);
      const un = await sourceLibraryApi.getUnits(jobId, scopeParams);
      setUnitsPayload(un);
      return un;
    } catch (err) {
      setStructureError(errorMessage(err, 'Could not load document sections.'));
      setOverview(null);
      setUnitsPayload(null);
      return null;
    }
  }, [jobId, scopeParams]);

  useEffect(() => { loadMetadata(); }, [loadMetadata]);
  useEffect(() => { loadUnits(); }, [loadUnits]);

  const applySectionForm = useCallback((unit, listEntry) => {
    const next = formFromUnit(unit, listEntry);
    setSectionForm(next);
    setSavedSectionForm(cloneForm(next));
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadSelectedSection() {
      if (isDocumentScope) {
        setSelectedUnit(null);
        setSectionLoading(false);
        return;
      }
      if (!units.length) return;
      const entry = units.find((u) => String(u.unit_id) === String(selectedSectionId));
      if (!entry?.unit_id) {
        setSearchParams((prev) => {
          const next = new URLSearchParams(prev);
          next.set('section', DOCUMENT_SECTION);
          return next;
        }, { replace: true });
        return;
      }
      setSectionLoading(true);
      try {
        const data = await sourceLibraryApi.getUnitDetail(jobId, entry.unit_id, scopeParams);
        if (cancelled) return;
        const unit = data.unit || null;
        setSelectedUnit(unit);
        applySectionForm(unit, entry);
      } catch (err) {
        if (cancelled) return;
        setSelectedUnit({
          title: 'Error',
          text: errorMessage(err, 'Could not load content unit.'),
          metadata: {},
        });
        applySectionForm({ title: entry.title || '', topics: entry.topics || [], metadata: {
          summary: entry.summary || '',
          acs_codes: entry.acs_codes || [],
          topics: entry.topics || [],
        } }, entry);
      } finally {
        if (!cancelled) setSectionLoading(false);
      }
    }
    loadSelectedSection();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, selectedSectionId, isDocumentScope, JSON.stringify(units.map((u) => u.unit_id)), scopeParams]);

  const ingestStatus = String(overview?.status || '').toLowerCase();
  const isProcessing = ingestStatus === 'processing' || ingestStatus === 'pending';
  const retagLive = Boolean(
    retagProgress
    && ['starting', 'running'].includes(String(retagProgress.state || '').toLowerCase()),
  );

  useEffect(() => {
    if (!jobId || !isProcessing) return undefined;
    const timer = window.setInterval(async () => {
      try {
        await loadUnits();
      } catch {
        // Keep last known state; next poll may succeed.
      }
    }, 8000);
    return () => window.clearInterval(timer);
  }, [jobId, isProcessing, loadUnits]);

  // Resume / poll background Retry-all progress (survives refresh + navigation back).
  useEffect(() => {
    if (!jobId || !isView) return undefined;
    let cancelled = false;
    let unitsTick = 0;
    let announcedTerminal = false;

    async function pollRetag() {
      try {
        const res = await sourceLibraryApi.getRetagProgress(jobId, scopeParams);
        if (cancelled) return;
        const progress = res?.progress ?? null;
        setRetagProgress(progress);
        const state = String(progress?.state || '').toLowerCase();
        if (state === 'starting' || state === 'running') {
          announcedTerminal = false;
          setRetagging(true);
          unitsTick += 1;
          // Refresh section pills every ~8s (4 × 2s polls) while tagging runs.
          if (unitsTick % 4 === 0) {
            try { await loadUnits(); } catch { /* keep last */ }
          }
          return;
        }
        if ((state === 'done' || state === 'failed') && !announcedTerminal) {
          announcedTerminal = true;
          setRetagging(false);
          const failedLeft = Number(progress?.tagging_failed_count || 0);
          const done = Number(progress?.done || 0);
          if (state === 'failed') {
            setRetagMessage(progress?.error || 'Re-tag failed.');
          } else {
            setRetagMessage(
              failedLeft > 0
                ? `Re-tagged ${done} page(s); ${failedLeft} still need tagging.`
                : `Re-tagged ${done} page(s) successfully.`,
            );
          }
          try { await loadUnits(); } catch { /* keep last */ }
          return;
        }
        if (!progress) {
          setRetagging(false);
        }
      } catch {
        // Keep last known progress; next poll may succeed.
      }
    }

    pollRetag();
    const timer = window.setInterval(pollRetag, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobId, isView, scopeParams, loadUnits]);

  function setTab(tabId) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('tab', tabId);
      if (!next.get('section')) next.set('section', selectedSectionId);
      return next;
    }, { replace: true });
  }

  function handleSectionChange(nextId) {
    if (!isView && isDirty && !window.confirm('Discard unsaved changes?')) return;
    if (!isView && isDirty) {
      if (isDocumentScope) setDocumentForm(cloneForm(savedDocumentForm));
      else setSectionForm(cloneForm(savedSectionForm));
    }
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('section', nextId || DOCUMENT_SECTION);
      if (!next.get('tab')) next.set('tab', activeTab);
      return next;
    }, { replace: true });
  }

  function setFormUpdater(updater) {
    if (isDocumentScope) setDocumentForm(updater);
    else setSectionForm(updater);
  }

  function updateAi(field, value) {
    if (isView) return;
    if (!isDocumentScope && isSyntheticSection) return;
    setFormUpdater((f) => ({ ...f, ai_metadata: { ...f.ai_metadata, [field]: value } }));
  }

  function updateTaxonomy(field, value) {
    if (isView) return;
    if (!isDocumentScope && field !== 'learning_standards') return;
    if (!isDocumentScope && isSyntheticSection) return;
    setFormUpdater((f) => ({ ...f, taxonomy_standards: { ...f.taxonomy_standards, [field]: value } }));
  }

  function updateRelationships(field, value) {
    if (isView || !isDocumentScope) return;
    setDocumentForm((f) => ({ ...f, relationships: { ...f.relationships, [field]: value } }));
  }

  function addKeyword() {
    const value = window.prompt(isDocumentScope ? 'Add keyword' : 'Add topic');
    if (!value?.trim()) return;
    const next = [...form.ai_metadata.keywords, value.trim()];
    if (next.length > keywordsMax) {
      toast.error(`Maximum ${keywordsMax} ${isDocumentScope ? 'keywords' : 'topics'}`);
      return;
    }
    updateAi('keywords', next);
  }

  function addTaxonomyItem(field) {
    const value = window.prompt(field === 'learning_standards' && !isDocumentScope ? 'Add ACS code' : 'Add value');
    if (!value?.trim()) return;
    updateTaxonomy(field, [...form.taxonomy_standards[field], value.trim()]);
  }

  function openRelationshipPicker(relType, { green = false, allowTextOnly = false } = {}) {
    if (!isDocumentScope) return;
    setPicker({ relType, green, allowTextOnly });
    setPickerSearch('');
    sourceLibraryApi.listDocuments({ ...scopeParams, limit: 200 })
      .then((res) => setPickerDocs(res?.documents || res?.sources || []))
      .catch(() => setPickerDocs([]));
  }

  function addRelationshipItem(relType, item) {
    const current = form.relationships[relType] || [];
    updateRelationships(relType, [...current, item]);
    setPicker(null);
  }

  function addTextRelationship(relType) {
    const value = window.prompt('Enter name');
    if (!value?.trim()) return;
    addRelationshipItem(relType, { job_id: '', label: value.trim() });
  }

  async function handleSave() {
    setSaving(true);
    try {
      if (isDocumentScope) {
        const data = await sourceLibraryApi.patchMetadata(jobId, form, scopeParams);
        const nextForm = formFromResponse(data);
        setServerData(data);
        setDocumentForm(nextForm);
        setSavedDocumentForm(cloneForm(nextForm));
        toast.success('Metadata saved');
      } else {
        if (isSyntheticSection) {
          toast.error('Synthetic view sections cannot be edited');
          return;
        }
        const payload = {
          title: form.ai_metadata.title,
          summary: form.ai_metadata.description,
          topics: form.ai_metadata.keywords,
          acs_codes: form.taxonomy_standards.learning_standards,
        };
        const data = await sourceLibraryApi.patchUnitMetadata(
          jobId,
          selectedSectionId,
          payload,
          scopeParams,
        );
        const unit = data.unit || null;
        setSelectedUnit(unit);
        applySectionForm(unit, listUnit);
        await loadUnits();
        toast.success('Section metadata saved');
      }
    } catch (err) {
      toast.error(errorMessage(err, 'Could not save metadata.'));
    } finally {
      setSaving(false);
    }
  }

  async function handleRevert() {
    if (!isDocumentScope) {
      toast.error('Revert is only available for whole-document metadata');
      return;
    }
    if (!serverData?.ai_baseline_available) {
      toast.error('AI baseline not available for this document');
      return;
    }
    try {
      const data = await sourceLibraryApi.revertMetadata(jobId, scopeParams);
      const nextForm = formFromResponse(data);
      setServerData(data);
      setDocumentForm(nextForm);
      setSavedDocumentForm(cloneForm(nextForm));
      toast.success('Restored AI values');
    } catch (err) {
      toast.error(errorMessage(err, 'Could not revert to AI values.'));
    }
  }

  function handleCancel() {
    if (!isView && isDirty && !window.confirm('Discard unsaved changes?')) return;
    if (!isView) {
      if (isDocumentScope) setDocumentForm(cloneForm(savedDocumentForm));
      else setSectionForm(cloneForm(savedSectionForm));
    }
    navigate(`/workspace/${courseId}/sources`);
  }

  async function handleRetag({ unitIds = null, allFailed = false } = {}) {
    setRetagging(true);
    setRetagMessage('');
    try {
      const payload = allFailed ? { all_failed: true } : { unit_ids: unitIds || [] };
      const result = await sourceLibraryApi.retagContent(jobId, payload, scopeParams);

      // Retry-all is a background job — progress comes from polling.
      if (allFailed || result?.started || result?.already_running) {
        if (result?.progress) setRetagProgress(result.progress);
        else {
          try {
            const res = await sourceLibraryApi.getRetagProgress(jobId, scopeParams);
            setRetagProgress(res?.progress ?? null);
          } catch { /* poller will retry */ }
        }
        setRetagMessage(result?.already_running
          ? 'Tagging already in progress…'
          : 'Tagging started in the background…');
        return;
      }

      const failedLeft = Number(result?.tagging_failed_count || 0);
      const n = Number(result?.retagged || 0);
      setRetagMessage(
        failedLeft > 0
          ? `Re-tagged ${n} page(s); ${failedLeft} still need tagging.`
          : `Re-tagged ${n} page(s) successfully.`,
      );
      const un = await loadUnits();
      if (!isDocumentScope && selectedSectionId) {
        const detail = await sourceLibraryApi.getUnitDetail(jobId, selectedSectionId, scopeParams);
        const unit = detail.unit || null;
        setSelectedUnit(unit);
        const entry = (un?.units || []).find((u) => String(u.unit_id) === String(selectedSectionId));
        applySectionForm(unit, entry);
      }
      setRetagging(false);
    } catch (e) {
      setRetagMessage(errorMessage(e, 'Re-tag failed.'));
      setRetagging(false);
    }
  }

  async function handleReindex() {
    setReindexing(true);
    try {
      const result = await sourceLibraryApi.reindexDocument(jobId, scopeParams);
      const n = Number(result?.units_indexed || 0);
      toast.success(n ? `Indexed ${n} unit${n === 1 ? '' : 's'} for search.` : 'Re-index finished.');
    } catch (err) {
      toast.error(errorMessage(err, 'Could not re-index this document.'));
    } finally {
      setReindexing(false);
    }
  }

  const filteredPickerDocs = pickerDocs.filter((doc) => {
    if (String(doc.job_id || doc.document_id) === String(jobId)) return false;
    const q = pickerSearch.trim().toLowerCase();
    if (!q) return true;
    const hay = `${doc.title || ''} ${doc.source_file_name || ''}`.toLowerCase();
    return hay.includes(q);
  });

  const aiDate = serverData?.provenance?.ai_extracted_at
    ? String(serverData.provenance.ai_extracted_at).slice(0, 10)
    : 'extraction';

  if (loading) {
    return (
      <PageContainer title={pageTitle}>
        <Loader size="lg" overlay />
      </PageContainer>
    );
  }

  return (
    <PageContainer title={pageTitle}>
      <div className={styles.page}>
        <button type="button" className={styles.backLink} onClick={handleCancel}>
          ← Source Library
        </button>

        <div className={styles.headerRow}>
          <div>
            <h1 className={styles.title}>{pageTitle}</h1>
            <p className={styles.subtitle}>
              {isView
                ? 'Review document metadata, sections, and raw content'
                : 'Review and refine the tags extracted from this document'}
            </p>
          </div>
          <div className={styles.headerActions}>
            {isDirty ? <span className={styles.unsavedBadge}>● Unsaved changes</span> : null}
            {isAdmin && Number(overview?.total_characters || 0) > 0 && (
              <Button
                variant="secondary"
                onClick={handleReindex}
                loading={reindexing}
                disabled={isProcessing}
              >
                Re-index
              </Button>
            )}
          </div>
        </div>

        {error ? <div className={styles.error}>{error}</div> : null}

        <div className={styles.layout}>
          <DocumentSummaryPanel
            summary={serverData?.document_summary}
            units={units}
            selectedSectionId={selectedSectionId}
            onSectionChange={handleSectionChange}
          />

          <div className={styles.editorCard}>
            <div className={styles.tabs}>
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  className={activeTab === tab.id ? styles.tabActive : styles.tab}
                  onClick={() => setTab(tab.id)}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {activeTab === 'ai' && (
              <>
                <ProvenanceBanner>
                  {isDocumentScope
                    ? (isView
                      ? `Extracted by AI on ${aiDate}`
                      : `Extracted by AI on ${aiDate} · edits override the extracted values`)
                    : (isView
                      ? 'Section tags from page content tagging'
                      : 'Section tags from page content tagging · edits update this section only')}
                </ProvenanceBanner>
                {isDocumentScope ? (
                  <>
                    <div className={styles.grid2}>
                      <MetadataField readOnly={isView} label="Title" value={form.ai_metadata.title} onChange={(v) => updateAi('title', v)} />
                      <MetadataField readOnly={isView} label="Author" value={form.ai_metadata.author} onChange={(v) => updateAi('author', v)} />
                      <MetadataField readOnly={isView} label="Subject" value={form.ai_metadata.subject} onChange={(v) => updateAi('subject', v)} />
                      <MetadataField readOnly={isView} label="Language" value={form.ai_metadata.language} onChange={(v) => updateAi('language', v)} />
                    </div>
                    <div className={styles.fieldBlock}>
                      <label className={styles.fieldLabel}>Description</label>
                      <textarea
                        className={styles.textarea}
                        rows={3}
                        readOnly={isView}
                        value={form.ai_metadata.description}
                        onChange={isView ? undefined : (e) => updateAi('description', e.target.value)}
                      />
                    </div>
                    <MetadataTagInput
                      readOnly={isView}
                      label="Keywords"
                      items={form.ai_metadata.keywords}
                      countLabel={`${form.ai_metadata.keywords.length} of ${keywordsMax} max`}
                      addLabel="+ Add keyword..."
                      onAdd={addKeyword}
                      onRemove={(idx) => updateAi('keywords', form.ai_metadata.keywords.filter((_, i) => i !== idx))}
                      maxItems={keywordsMax}
                    />
                  </>
                ) : (
                  <>
                    <div className={styles.fieldBlock}>
                      <MetadataField
                        readOnly={isView || isSyntheticSection}
                        label="Title"
                        value={form.ai_metadata.title}
                        onChange={(v) => updateAi('title', v)}
                      />
                    </div>
                    <div className={styles.fieldBlock}>
                      <label className={styles.fieldLabel}>Description</label>
                      <textarea
                        className={styles.textarea}
                        rows={3}
                        readOnly={isView || isSyntheticSection}
                        value={form.ai_metadata.description}
                        onChange={(isView || isSyntheticSection) ? undefined : (e) => updateAi('description', e.target.value)}
                      />
                    </div>
                    <MetadataTagInput
                      readOnly={isView || isSyntheticSection}
                      label="Keywords"
                      items={form.ai_metadata.keywords}
                      countLabel={`${form.ai_metadata.keywords.length} of ${keywordsMax} max`}
                      addLabel="+ Add topic..."
                      onAdd={addKeyword}
                      onRemove={(idx) => updateAi('keywords', form.ai_metadata.keywords.filter((_, i) => i !== idx))}
                      maxItems={keywordsMax}
                    />
                  </>
                )}
              </>
            )}

            {activeTab === 'taxonomy' && (
              <>
                <ProvenanceBanner>
                  {isDocumentScope
                    ? 'Derived from Cengage taxonomy v4 · aligned to AACSB and AMA frameworks'
                    : 'ACS codes tagged for this section'}
                </ProvenanceBanner>
                {isDocumentScope ? (
                  <>
                    <div className={styles.grid2}>
                      <MetadataField readOnly={isView} label="Subject area" value={form.taxonomy_standards.subject_area} onChange={(v) => updateTaxonomy('subject_area', v)} />
                      <MetadataField readOnly={isView} label="Domain" value={form.taxonomy_standards.domain} onChange={(v) => updateTaxonomy('domain', v)} />
                      <MetadataField readOnly={isView} label="Subdomain" value={form.taxonomy_standards.subdomain} onChange={(v) => updateTaxonomy('subdomain', v)} />
                      <MetadataField readOnly={isView} label="Bloom's level" value={form.taxonomy_standards.blooms_level} onChange={(v) => updateTaxonomy('blooms_level', v)} />
                    </div>
                    <div className={styles.fieldBlock}>
                      <label className={styles.fieldLabel}>Skill level</label>
                      <input
                        className={styles.inputNarrow}
                        readOnly={isView}
                        value={form.taxonomy_standards.skill_level}
                        onChange={isView ? undefined : (e) => updateTaxonomy('skill_level', e.target.value)}
                      />
                    </div>
                    <MetadataTagInput
                      readOnly={isView}
                      label="Learning standards"
                      items={form.taxonomy_standards.learning_standards}
                      countLabel={`${form.taxonomy_standards.learning_standards.length} mapped`}
                      tone="green"
                      onAdd={() => addTaxonomyItem('learning_standards')}
                      onRemove={(idx) => updateTaxonomy('learning_standards', form.taxonomy_standards.learning_standards.filter((_, i) => i !== idx))}
                    />
                    <MetadataTagInput
                      readOnly={isView}
                      label="Skills mapped"
                      items={form.taxonomy_standards.skills_mapped}
                      countLabel={`${form.taxonomy_standards.skills_mapped.length} mapped`}
                      onAdd={() => addTaxonomyItem('skills_mapped')}
                      onRemove={(idx) => updateTaxonomy('skills_mapped', form.taxonomy_standards.skills_mapped.filter((_, i) => i !== idx))}
                    />
                  </>
                ) : (
                  <MetadataTagInput
                    readOnly={isView || isSyntheticSection}
                    label="Learning standards"
                    items={form.taxonomy_standards.learning_standards}
                    countLabel={`${form.taxonomy_standards.learning_standards.length} mapped`}
                    tone="green"
                    onAdd={() => addTaxonomyItem('learning_standards')}
                    onRemove={(idx) => updateTaxonomy('learning_standards', form.taxonomy_standards.learning_standards.filter((_, i) => i !== idx))}
                  />
                )}
              </>
            )}

            {activeTab === 'relationships' && (
              <>
                <ProvenanceBanner>
                  Links detected across the Source Library · edits affect retrieval, not file storage
                </ProvenanceBanner>
                {isDocumentScope ? (
                  <>
                    <MetadataField
                      readOnly={isView}
                      label="Series / collection"
                      value={form.relationships.series_collection}
                      onChange={(v) => updateRelationships('series_collection', v)}
                    />
                    <MetadataTagInput
                      readOnly={isView}
                      label="Related documents"
                      items={form.relationships.related_documents}
                      countLabel={`${form.relationships.related_documents.length} linked`}
                      tone="green"
                      onAdd={() => openRelationshipPicker('related_documents', { green: true })}
                      onRemove={(idx) => updateRelationships('related_documents', form.relationships.related_documents.filter((_, i) => i !== idx))}
                    />
                    <div className={styles.fieldBlock}>
                      <div className={styles.fieldHeader}>
                        <label className={styles.fieldLabel}>Prerequisites</label>
                        <span className={styles.fieldCount}>
                          {form.relationships.prerequisites.length ? `${form.relationships.prerequisites.length} linked` : 'none set'}
                        </span>
                      </div>
                      {form.relationships.prerequisites.length === 0 ? (
                        <div className={styles.emptyRel}>
                          No prerequisites — this document stands alone
                          {!isView && (
                            <button type="button" className={styles.chipAdd} style={{ marginLeft: 12 }} onClick={() => addTextRelationship('prerequisites')}>
                              + Add...
                            </button>
                          )}
                        </div>
                      ) : (
                        <MetadataTagInput
                          readOnly={isView}
                          label=""
                          items={form.relationships.prerequisites}
                          onAdd={() => openRelationshipPicker('prerequisites', { allowTextOnly: true })}
                          onRemove={(idx) => updateRelationships('prerequisites', form.relationships.prerequisites.filter((_, i) => i !== idx))}
                        />
                      )}
                    </div>
                    <MetadataTagInput
                      readOnly={isView}
                      label="Cross-references"
                      items={form.relationships.cross_references}
                      countLabel={`${form.relationships.cross_references.length} linked`}
                      onAdd={() => addTextRelationship('cross_references')}
                      onRemove={(idx) => updateRelationships('cross_references', form.relationships.cross_references.filter((_, i) => i !== idx))}
                    />
                  </>
                ) : (
                  <div className={styles.emptyRel}>
                    Relationships are document-level. Select Whole document to view or edit them.
                  </div>
                )}
              </>
            )}

            {isView && activeTab === 'content' && (
              <DocumentContentPanel
                overview={overview}
                units={unitsPayload}
                selectedUnit={selectedUnit}
                sectionIndex={sectionIndex >= 0 ? sectionIndex : 0}
                sectionLoading={sectionLoading}
                structureError={structureError}
                retagging={retagging || retagLive}
                retagProgress={retagProgress}
                retagMessage={retagMessage}
                onRetag={handleRetag}
                isDocumentScope={isDocumentScope}
              />
            )}

            {!isView && (
              <MetadataEditorFooter
                onRevert={handleRevert}
                onCancel={handleCancel}
                onSave={handleSave}
                saving={saving}
                revertDisabled={!isDocumentScope || !serverData?.ai_baseline_available}
              />
            )}
          </div>
        </div>
      </div>

      {!isView && (
        <Modal
          open={Boolean(picker)}
          onClose={() => setPicker(null)}
          title="Select document"
          footer={<Button variant="secondary" onClick={() => setPicker(null)}>Close</Button>}
        >
          <input
            className={styles.pickerSearch}
            placeholder="Search documents..."
            value={pickerSearch}
            onChange={(e) => setPickerSearch(e.target.value)}
          />
          <div className={styles.pickerList}>
            {filteredPickerDocs.map((doc) => {
              const id = doc.job_id || doc.document_id;
              const label = doc.title || doc.source_file_name || id;
              return (
                <button
                  key={id}
                  type="button"
                  className={styles.pickerItem}
                  onClick={() => addRelationshipItem(picker.relType, { job_id: String(id), label })}
                >
                  <span>{label}</span>
                  <span className={styles.fieldCount}>{doc.document_type || ''}</span>
                </button>
              );
            })}
            {picker?.allowTextOnly ? (
              <button type="button" className={styles.chipAdd} onClick={() => addTextRelationship(picker.relType)}>
                + Add by name...
              </button>
            ) : null}
          </div>
        </Modal>
      )}
    </PageContainer>
  );
}
