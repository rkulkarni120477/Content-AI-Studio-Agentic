import { useCallback, useEffect, useState } from 'react';
import { api } from '@services/apiClient';
import { BLOCKS, MODULES } from '@services/endpoints';
import { WORKFLOW_STATES } from '@utils/constants';
import { truncate, downloadBlob, downloadText } from '@utils/helpers';
import Button from '@components/common/Button/Button';
import Loader from '@components/common/Loader/Loader';
import Modal from '@components/common/Modal/Modal';
import toast from 'react-hot-toast';
import styles from './PublishedTocPanel.module.scss';

function htmlFilename(block) {
  const base = (block.block_label || `block_${block.id}`)
    .replace(/[^\w\s-]/g, '')
    .replace(/\s+/g, '_')
    .slice(0, 60) || `block_${block.id}`;
  return `${base}.html`;
}

function totalBlocks(modules, unassigned) {
  return modules.reduce((n, m) => n + (m.blocks?.length || 0), 0) + unassigned.length;
}

function BlockRow({
  block,
  index,
  regenerating,
  downloadId,
  onPreview,
  onDownload,
  onRegenerate,
}) {
  return (
    <li className={styles.item}>
      <span className={styles.index}>{index}</span>
      <div className={styles.body}>
        <p className={styles.label}>{truncate(block.block_label || `Block ${block.id}`, 60)}</p>
        <p className={styles.meta}>
          #{block.id}
          {block.has_html && <span className={styles.htmlBadge} title="Canvas HTML ready">● HTML</span>}
        </p>
      </div>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.iconBtn}
          disabled={!block.has_html}
          onClick={() => onPreview(block)}
          title={block.has_html ? 'Preview Canvas HTML' : 'No HTML generated yet — use Regenerate'}
          aria-label="Preview Canvas HTML"
        >
          👁
        </button>
        <button
          type="button"
          className={styles.iconBtn}
          disabled={!block.has_html || downloadId === block.id}
          onClick={() => onDownload(block)}
          title={downloadId === block.id ? 'Downloading…' : 'Download Canvas HTML'}
          aria-label="Download Canvas HTML"
        >
          {downloadId === block.id ? '⏳' : '⬇'}
        </button>
        <button
          type="button"
          className={styles.iconBtn}
          disabled={regenerating}
          onClick={() => onRegenerate(block)}
          title={regenerating ? 'Generating HTML…' : 'Regenerate Canvas HTML'}
          aria-label="Regenerate Canvas HTML"
        >
          {regenerating ? '⏳' : '↻'}
        </button>
      </div>
    </li>
  );
}

export default function PublishedTocPanel({ courseId, courseName, projectCourses = [] }) {
  const [modules, setModules] = useState([]);
  const [unassigned, setUnassigned] = useState([]);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  /** Block IDs currently regenerating — each request is independent. */
  const [regenIds, setRegenIds] = useState(() => new Set());
  const [downloadId, setDownloadId] = useState(null);
  const [preview, setPreview] = useState(null);

  const isRegenerating = useCallback((blockId) => regenIds.has(blockId), [regenIds]);

  const loadLayout = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    try {
      const res = await api.get(MODULES.LIST(courseId));
      setModules(res.modules || []);
      setUnassigned(res.unassigned || []);
    } catch {
      setModules([]);
      setUnassigned([]);
      toast.error('Failed to load export layout.');
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  useEffect(() => { loadLayout(); }, [loadLayout]);

  function patchBlockInLayout(blockId, patch) {
    const patchList = (list) => list.map((b) => (b.id === blockId ? { ...b, ...patch } : b));
    setModules((prev) => prev.map((m) => ({ ...m, blocks: patchList(m.blocks || []) })));
    setUnassigned((prev) => patchList(prev));
  }

  // ── Module actions ──────────────────────────────────────────────────────────

  async function handleAddModule() {
    const title = window.prompt('Module title:', 'New Module');
    if (!title?.trim()) return;
    try {
      const created = await api.post(MODULES.CREATE(courseId), { title: title.trim() });
      setModules((prev) => [...prev, { ...created, blocks: [] }]);
      toast.success('Module created.');
    } catch {
      toast.error('Failed to create module.');
    }
  }

  async function handleRenameModule(moduleId, currentTitle) {
    const title = window.prompt('Rename module:', currentTitle);
    if (!title?.trim() || title.trim() === currentTitle) return;
    try {
      await api.put(MODULES.RENAME(moduleId), { title: title.trim() });
      setModules((prev) => prev.map((m) => (
        m.id === moduleId ? { ...m, title: title.trim() } : m
      )));
      toast.success('Module renamed.');
    } catch {
      toast.error('Failed to rename module.');
    }
  }

  async function handleDeleteModule(moduleId, title) {
    if (!window.confirm(`Delete module "${title}"? Its items will move to Unassigned.`)) return;
    try {
      await api.delete(MODULES.DELETE(moduleId));
      const mod = modules.find((m) => m.id === moduleId);
      const freed = mod?.blocks || [];
      const nextModules = modules.filter((m) => m.id !== moduleId);
      const nextUnassigned = [...unassigned, ...freed];
      applyLayout(nextModules, nextUnassigned);
      toast.success('Module deleted.');
    } catch {
      toast.error('Failed to delete module.');
    }
  }

  function handleModuleDragStart(index) {
    setModuleDragIndex(index);
  }

  function handleModuleDragOver(e, index) {
    e.preventDefault();
    if (moduleDragIndex === null || moduleDragIndex === index) return;
    setModules((prev) => moveItem(prev, moduleDragIndex, index));
    setModuleDragIndex(index);
  }

  function handleModuleDragEnd() {
    if (moduleDragIndex !== null) {
      persistLayout(layoutRef.current.modules, layoutRef.current.unassigned);
    }
    setModuleDragIndex(null);
  }

  // ── Block drag between modules ──────────────────────────────────────────────

  function findBlockLocation(blockId) {
    for (const m of modules) {
      const idx = m.blocks.findIndex((b) => b.id === blockId);
      if (idx >= 0) return { key: `m:${m.id}`, moduleId: m.id, index: idx };
    }
    const idx = unassigned.findIndex((b) => b.id === blockId);
    if (idx >= 0) return { key: 'unassigned', moduleId: null, index: idx };
    return null;
  }

  function removeBlockFromLayout(blockId, mods, unas) {
    const nextMods = mods.map((m) => ({
      ...m,
      blocks: m.blocks.filter((b) => b.id !== blockId),
    }));
    const nextUnas = unas.filter((b) => b.id !== blockId);
    return { nextMods, nextUnas, block: findBlockIn(mods, unas, blockId) };
  }

  function findBlockIn(mods, unas, blockId) {
    for (const m of mods) {
      const b = m.blocks.find((x) => x.id === blockId);
      if (b) return b;
    }
    return unas.find((b) => b.id === blockId);
  }

  function insertBlock(mods, unas, block, targetKey, targetIndex) {
    const nextMods = mods.map((m) => ({ ...m, blocks: [...m.blocks] }));
    let nextUnas = [...unas];
    if (targetKey === 'unassigned') {
      nextUnas.splice(targetIndex, 0, block);
      return { nextMods, nextUnas };
    }
    const moduleId = Number(targetKey.replace('m:', ''));
    const mod = nextMods.find((m) => m.id === moduleId);
    if (mod) mod.blocks.splice(targetIndex, 0, block);
    return { nextMods, nextUnas };
  }

  function handleBlockDragStart(blockId, sourceKey) {
    droppedRef.current = false;
    setBlockDrag({ blockId, sourceKey });
  }

  function handleBlockDragOver(e, targetKey, targetIndex) {
    e.preventDefault();
    if (!blockDrag) return;
    const { blockId, sourceKey } = blockDrag;
    if (sourceKey === targetKey) {
      // Reorder within same list
      const loc = findBlockLocation(blockId);
      if (!loc || loc.index === targetIndex) return;
      if (targetKey === 'unassigned') {
        setUnassigned((prev) => moveItem(prev, loc.index, targetIndex));
      } else {
        const moduleId = Number(targetKey.replace('m:', ''));
        setModules((prev) => prev.map((m) => (
          m.id === moduleId ? { ...m, blocks: moveItem(m.blocks, loc.index, targetIndex) } : m
        )));
      }
      setBlockDrag({ blockId, sourceKey: targetKey });
      return;
    }
    // Move across lists
    const { nextMods, nextUnas, block } = removeBlockFromLayout(
      blockId, layoutRef.current.modules, layoutRef.current.unassigned,
    );
    if (!block) return;
    const inserted = insertBlock(nextMods, nextUnas, block, targetKey, targetIndex);
    setModules(inserted.nextMods);
    setUnassigned(inserted.nextUnas);
    setBlockDrag({ blockId, sourceKey: targetKey });
  }

  function handleBlockDragEnd() {
    // If a container drop already persisted the computed layout, don't overwrite
    // it with the (possibly stale) ref state here.
    if (!droppedRef.current && blockDrag) {
      persistLayout(layoutRef.current.modules, layoutRef.current.unassigned);
    }
    droppedRef.current = false;
    setBlockDrag(null);
  }

  function commitDropTo(blockId, targetKey) {
    const cur = layoutRef.current;
    // If the block is already in the target list (moved live via dragover),
    // just persist the current arrangement as-is to preserve its position.
    const inTarget =
      targetKey === 'unassigned'
        ? cur.unassigned.some((b) => b.id === blockId)
        : cur.modules.some((m) => `m:${m.id}` === targetKey && m.blocks.some((b) => b.id === blockId));

    if (inTarget) {
      applyLayout(cur.modules, cur.unassigned);
      return;
    }

    const { nextMods, nextUnas, block } = removeBlockFromLayout(blockId, cur.modules, cur.unassigned);
    if (!block) return;
    const targetIndex =
      targetKey === 'unassigned'
        ? nextUnas.length
        : (nextMods.find((m) => `m:${m.id}` === targetKey)?.blocks.length ?? 0);
    const inserted = insertBlock(nextMods, nextUnas, block, targetKey, targetIndex);
    applyLayout(inserted.nextMods, inserted.nextUnas);
  }

  function handleDropOnModule(e, moduleId) {
    e.preventDefault();
    if (!blockDrag) return;
    droppedRef.current = true;
    commitDropTo(blockDrag.blockId, `m:${moduleId}`);
    setBlockDrag(null);
  }

  function handleDropOnUnassigned(e) {
    e.preventDefault();
    if (!blockDrag) return;
    droppedRef.current = true;
    commitDropTo(blockDrag.blockId, 'unassigned');
    setBlockDrag(null);
  }

  // ── HTML actions ────────────────────────────────────────────────────────────

  function handleLaunchPrintWorkflow() {
    const url = import.meta.env.VITE_PRINT_WORKFLOW_URL;
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer');
      return;
    }
    toast.error('Print workflow URL is not configured. Set VITE_PRINT_WORKFLOW_URL.');
  }

  async function handleExportImscc() {
    if (!totalBlocks(modules, unassigned)) return;
    setExporting(true);
    try {
      const safeName = (courseName || `course_${courseId}`).replace(/\s+/g, '_');
      const response = await api.download(BLOCKS.EXPORT_COURSE(courseId), {
        params: { format: 'imscc', workflow_state: WORKFLOW_STATES.PUBLISHED },
      });
      downloadBlob(response.data, `${safeName}_published.imscc`);
      toast.success('IMSCC package downloaded.');
    } catch {
      toast.error('IMSCC export failed.');
    } finally {
      setExporting(false);
    }
  }

  async function handlePreviewHtml(block) {
    setPreview({ block, html: null, at: null, loading: true });
    try {
      const res = await api.get(BLOCKS.CANVAS_HTML(block.id));
      setPreview({
        block,
        html: res.content_html || '',
        at: res.content_html_at || null,
        hasHtml: !!res.has_html,
        loading: false,
      });
    } catch {
      toast.error('Failed to load HTML preview.');
      setPreview(null);
    }
  }

  async function handleRegenerateHtml(block) {
    if (regenIds.has(block.id)) return;
    setRegenIds((prev) => new Set(prev).add(block.id));
    try {
      const res = await api.post(BLOCKS.CANVAS_HTML_REGEN(block.id));
      toast.success(`HTML regenerated for “${truncate(block.block_label || `Block ${block.id}`, 40)}”.`);
      patchBlockInLayout(block.id, { has_html: true });
      if (preview?.block?.id === block.id) {
        setPreview({
          block,
          html: res.content_html || '',
          at: res.content_html_at || null,
          hasHtml: true,
          loading: false,
        });
      }
    } catch (err) {
      toast.error(err?.message || 'HTML regeneration failed.');
    } finally {
      setRegenIds((prev) => {
        const next = new Set(prev);
        next.delete(block.id);
        return next;
      });
    }
  }

  async function handleDownloadHtml(block) {
    setDownloadId(block.id);
    try {
      let html = '';
      if (preview?.block?.id === block.id && preview.html) {
        html = preview.html;
      } else {
        const res = await api.get(BLOCKS.CANVAS_HTML(block.id));
        if (!res.has_html || !res.content_html) {
          toast.error('No Canvas HTML available to download.');
          return;
        }
        html = res.content_html;
      }
      downloadText(html, htmlFilename(block), 'text/html;charset=utf-8');
      toast.success('HTML downloaded.');
    } catch {
      toast.error('Failed to download HTML.');
    } finally {
      setDownloadId(null);
    }
  }

  const blockCount = totalBlocks(modules, unassigned);

  if (!courseId) {
    return (
      <p className={styles.hint}>
        Open a course workspace to manage the published table of contents.
      </p>
    );
  }

  const blockRowProps = {
    downloadId,
    onPreview: handlePreviewHtml,
    onDownload: handleDownloadHtml,
    onRegenerate: handleRegenerateHtml,
  };

  return (
    <section className={styles.panel}>
      <div className={styles.header}>
        <div>
          <h2 className={styles.title}>📑 Export Layout</h2>
          <p className={styles.subtitle}>
            {courseName ? (
              <>
                Course: <strong>{courseName}</strong> — modules and sequence follow the CDD /
                Blueprint. Preview or regenerate Canvas HTML, then export IMS CC.
              </>
            ) : (
              <>Modules follow the Blueprint structure. Export as an IMS CC package for LMS import.</>
            )}
          </p>
        </div>
        <div className={styles.headerActions}>
          <Button variant="secondary" onClick={handleLaunchPrintWorkflow}>
            🖨️ Launch Print Workflow
          </Button>
          <Button
            variant="primary"
            disabled={exporting || !blockCount}
            onClick={handleExportImscc}
          >
            {exporting ? 'Exporting…' : '📦 Export IMSCC'}
          </Button>
        </div>
      </div>

      {loading ? (
        <div className={styles.loading}><Loader size="md" /></div>
      ) : blockCount === 0 ? (
        <div className={styles.hint}>
          <p>
            No published blocks in <strong>{courseName || `course #${courseId}`}</strong> yet.
          </p>
          <p>
            Publish blocks from the <strong>Workflow</strong> page after generating content from
            the Blueprint, then return here to export.
          </p>
          {projectCourses.length > 1 && (
            <p className={styles.hintCourses}>
              Courses in this project: {projectCourses.map((c) => c.name).join(', ')}
            </p>
          )}
        </div>
      ) : (
        <div className={styles.moduleList}>
          {modules.map((mod) => (
            <div key={mod.id} className={styles.moduleCard}>
              <div className={styles.moduleHeader}>
                <h3 className={styles.moduleTitle}>{mod.title}</h3>
                <span className={styles.moduleCount}>
                  {(mod.blocks || []).length} item{(mod.blocks || []).length !== 1 ? 's' : ''}
                </span>
              </div>
              <ol className={styles.list}>
                {(mod.blocks || []).length === 0 ? (
                  <li className={styles.dropHint}>No published content for this module yet</li>
                ) : (
                  (mod.blocks || []).map((block, blockIndex) => (
                    <BlockRow
                      key={block.id}
                      block={block}
                      index={blockIndex + 1}
                      regenerating={isRegenerating(block.id)}
                      {...blockRowProps}
                    />
                  ))
                )}
              </ol>
            </div>
          ))}

          {unassigned.length > 0 && (
            <div className={styles.moduleCard}>
              <div className={styles.moduleHeader}>
                <h3 className={styles.moduleTitle}>Other published content</h3>
                <span className={styles.moduleHint}>
                  Not matched to a Blueprint lesson — exported after Blueprint modules
                </span>
              </div>
              <ol className={styles.list}>
                {unassigned.map((block, blockIndex) => (
                  <BlockRow
                    key={block.id}
                    block={block}
                    index={blockIndex + 1}
                    regenerating={isRegenerating(block.id)}
                    {...blockRowProps}
                  />
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      <Modal
        open={!!preview}
        onClose={() => setPreview(null)}
        title={preview ? `Canvas HTML — ${truncate(preview.block?.block_label || `Block ${preview.block?.id}`, 50)}` : 'Canvas HTML'}
        size="xl"
        footer={
          preview && (
            <>
              <span className={styles.previewMeta}>
                {preview.at
                  ? `Generated ${new Date(preview.at).toLocaleString()}`
                  : 'Not generated yet'}
              </span>
              <span className={styles.previewFooterActions}>
                <Button
                  variant="secondary"
                  disabled={isRegenerating(preview.block?.id)}
                  onClick={() => handleRegenerateHtml(preview.block)}
                >
                  {isRegenerating(preview.block?.id) ? 'Generating…' : '↻ Regenerate'}
                </Button>
                <Button variant="primary" onClick={() => setPreview(null)}>Close</Button>
              </span>
            </>
          )
        }
      >
        {preview?.loading ? (
          <div className={styles.loading}><Loader size="md" /></div>
        ) : preview?.html ? (
          <iframe
            title="Canvas HTML preview"
            className={styles.previewFrame}
            sandbox="allow-same-origin"
            srcDoc={preview.html}
          />
        ) : (
          <div className={styles.previewEmpty}>
            <p>No Canvas HTML has been generated for this block yet.</p>
            <p>Click <strong>Regenerate</strong> to build it now, or re-publish the block.</p>
          </div>
        )}
      </Modal>
    </section>
  );
}
