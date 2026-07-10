import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@services/apiClient';
import { BLOCKS, MODULES } from '@services/endpoints';
import { WORKFLOW_STATES } from '@utils/constants';
import { truncate, downloadBlob, downloadText } from '@utils/helpers';
import Button from '@components/common/Button/Button';
import Loader from '@components/common/Loader/Loader';
import Modal from '@components/common/Modal/Modal';
import toast from 'react-hot-toast';
import styles from './PublishedTocPanel.module.scss';

function moveItem(list, fromIndex, toIndex) {
  const next = [...list];
  const [item] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, item);
  return next;
}

function htmlFilename(block) {
  const base = (block.block_label || `block_${block.id}`)
    .replace(/[^\w\s-]/g, '')
    .replace(/\s+/g, '_')
    .slice(0, 60) || `block_${block.id}`;
  return `${base}.html`;
}

function totalBlocks(modules, unassigned) {
  return modules.reduce((n, m) => n + m.blocks.length, 0) + unassigned.length;
}

function BlockRow({
  block,
  index,
  regenId,
  downloadId,
  onPreview,
  onDownload,
  onRegenerate,
  onDragStart,
  onDragOver,
  onDragEnd,
  dragging,
}) {
  return (
    <li
      className={`${styles.item} ${dragging ? styles['item--dragging'] : ''}`}
      draggable
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDragEnd={onDragEnd}
    >
      <span className={styles.handle} title="Drag to move" aria-hidden="true">⠿</span>
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
          disabled={regenId === block.id}
          onClick={() => onRegenerate(block)}
          title={regenId === block.id ? 'Generating HTML…' : 'Regenerate Canvas HTML'}
          aria-label="Regenerate Canvas HTML"
        >
          {regenId === block.id ? '⏳' : '↻'}
        </button>
      </div>
    </li>
  );
}

export default function PublishedTocPanel({ courseId, courseName, projectCourses = [] }) {
  const [modules, setModules] = useState([]);
  const [unassigned, setUnassigned] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [regenId, setRegenId] = useState(null);
  const [downloadId, setDownloadId] = useState(null);
  const [preview, setPreview] = useState(null);
  const [moduleDragIndex, setModuleDragIndex] = useState(null);
  const [blockDrag, setBlockDrag] = useState(null); // { blockId, sourceKey }

  const layoutRef = useRef({ modules: [], unassigned: [] });
  layoutRef.current = { modules, unassigned };
  const droppedRef = useRef(false);

  const loadLayout = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    try {
      const res = await api.get(MODULES.LIST(courseId));
      setModules((res.modules || []).map((m) => ({
        ...m,
        blocks: m.blocks || [],
      })));
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

  async function persistLayout(nextModules, nextUnassigned) {
    setSaving(true);
    try {
      await api.put(MODULES.SAVE_LAYOUT(courseId), {
        modules: nextModules.map((m) => ({
          module_id: m.id,
          block_ids: m.blocks.map((b) => b.id),
        })),
      });
    } catch {
      toast.error('Failed to save layout.');
      loadLayout();
    } finally {
      setSaving(false);
    }
  }

  function applyLayout(nextModules, nextUnassigned) {
    setModules(nextModules);
    setUnassigned(nextUnassigned);
    persistLayout(nextModules, nextUnassigned);
  }

  function patchBlockInLayout(blockId, patch) {
    const patchList = (list) => list.map((b) => (b.id === blockId ? { ...b, ...patch } : b));
    setModules((prev) => prev.map((m) => ({ ...m, blocks: patchList(m.blocks) })));
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
    setRegenId(block.id);
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
      setRegenId(null);
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
    regenId,
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
              <>Course: <strong>{courseName}</strong> — arrange modules and items like Canvas, then export IMS CC.</>
            ) : (
              <>Create modules, drag items between them, and export as an IMS CC package.</>
            )}
          </p>
        </div>
        <div className={styles.headerActions}>
          <Button variant="secondary" onClick={handleAddModule}>
            + Add Module
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
            Publish blocks from the <strong>Workflow</strong> page, then return here to arrange
            modules and export.
          </p>
          {projectCourses.length > 1 && (
            <p className={styles.hintCourses}>
              Courses in this project: {projectCourses.map((c) => c.name).join(', ')}
            </p>
          )}
        </div>
      ) : (
        <div className={styles.moduleList}>
          {modules.map((mod, modIndex) => (
            <div
              key={mod.id}
              className={`${styles.moduleCard} ${moduleDragIndex === modIndex ? styles['moduleCard--dragging'] : ''}`}
              onDragOver={(e) => handleModuleDragOver(e, modIndex)}
            >
              <div className={styles.moduleHeader}>
                <span
                  className={styles.moduleHandle}
                  draggable
                  onDragStart={() => handleModuleDragStart(modIndex)}
                  onDragEnd={handleModuleDragEnd}
                  title="Drag to reorder module"
                >
                  ⠿
                </span>
                <h3 className={styles.moduleTitle}>{mod.title}</h3>
                <span className={styles.moduleCount}>{mod.blocks.length} item{mod.blocks.length !== 1 ? 's' : ''}</span>
                <div className={styles.moduleActions}>
                  <button
                    type="button"
                    className={styles.iconBtn}
                    onClick={() => handleRenameModule(mod.id, mod.title)}
                    title="Rename module"
                    aria-label="Rename module"
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    className={styles.iconBtn}
                    onClick={() => handleDeleteModule(mod.id, mod.title)}
                    title="Delete module"
                    aria-label="Delete module"
                  >
                    🗑
                  </button>
                </div>
              </div>
              <ol
                className={styles.list}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => handleDropOnModule(e, mod.id)}
              >
                {mod.blocks.length === 0 ? (
                  <li className={styles.dropHint}>Drop items here</li>
                ) : (
                  mod.blocks.map((block, blockIndex) => (
                    <BlockRow
                      key={block.id}
                      block={block}
                      index={blockIndex + 1}
                      dragging={blockDrag?.blockId === block.id}
                      onDragStart={() => handleBlockDragStart(block.id, `m:${mod.id}`)}
                      onDragOver={(e) => handleBlockDragOver(e, `m:${mod.id}`, blockIndex)}
                      onDragEnd={handleBlockDragEnd}
                      {...blockRowProps}
                    />
                  ))
                )}
              </ol>
            </div>
          ))}

          <div
            className={styles.moduleCard}
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDropOnUnassigned}
          >
            <div className={styles.moduleHeader}>
              <h3 className={styles.moduleTitle}>Unassigned</h3>
              <span className={styles.moduleHint}>
                Exported into a default &quot;Course Content&quot; module
              </span>
            </div>
            <ol className={styles.list}>
              {unassigned.length === 0 ? (
                <li className={styles.dropHint}>Drag items here to unassign</li>
              ) : (
                unassigned.map((block, blockIndex) => (
                  <BlockRow
                    key={block.id}
                    block={block}
                    index={blockIndex + 1}
                    dragging={blockDrag?.blockId === block.id}
                    onDragStart={() => handleBlockDragStart(block.id, 'unassigned')}
                    onDragOver={(e) => handleBlockDragOver(e, 'unassigned', blockIndex)}
                    onDragEnd={handleBlockDragEnd}
                    {...blockRowProps}
                  />
                ))
              )}
            </ol>
          </div>
        </div>
      )}
      {saving && <p className={styles.saving}>Saving layout…</p>}

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
                  disabled={regenId === preview.block?.id}
                  onClick={() => handleRegenerateHtml(preview.block)}
                >
                  {regenId === preview.block?.id ? 'Generating…' : '↻ Regenerate'}
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
