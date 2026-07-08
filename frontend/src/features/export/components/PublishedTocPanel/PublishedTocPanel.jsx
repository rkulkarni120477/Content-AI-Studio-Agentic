import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@services/apiClient';
import { BLOCKS, WORKFLOW } from '@services/endpoints';
import { WORKFLOW_STATES } from '@utils/constants';
import { truncate, downloadBlob, downloadText } from '@utils/helpers';
import Button from '@components/common/Button/Button';
import Loader from '@components/common/Loader/Loader';
import Modal from '@components/common/Modal/Modal';
import toast from 'react-hot-toast';
import styles from './PublishedTocPanel.module.scss';

function sortBlocks(blocks) {
  return [...blocks].sort((a, b) => {
    const posA = a.position ?? 0;
    const posB = b.position ?? 0;
    if (posA !== posB) return posA - posB;
    return a.id - b.id;
  });
}

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

export default function PublishedTocPanel({ courseId, courseName, projectCourses = [] }) {
  const [blocks, setBlocks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [dragIndex, setDragIndex] = useState(null);
  const [regenId, setRegenId] = useState(null);
  const [downloadId, setDownloadId] = useState(null);
  const [preview, setPreview] = useState(null); // { block, html, at, loading }
  const blocksRef = useRef(blocks);
  blocksRef.current = blocks;

  const loadBlocks = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    try {
      const res = await api.get(BLOCKS.LIST_COURSE(courseId), {
        params: { workflow_state: WORKFLOW_STATES.PUBLISHED, page_size: 200 },
      });
      let items = res.items || res || [];
      if (!items.length) {
        const wf = await api.get(WORKFLOW.LIST, {
          params: {
            course_id: courseId,
            state: WORKFLOW_STATES.PUBLISHED,
            page_size: 200,
          },
        });
        items = (wf.items || wf || []).map((b) => ({
          id: b.id,
          block_label: b.block_label,
          position: b.position ?? 0,
          workflow_state: b.workflow_state,
          has_html: false,
        }));
      }
      setBlocks(sortBlocks(items));
    } catch {
      setBlocks([]);
      toast.error('Failed to load published blocks.');
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  useEffect(() => { loadBlocks(); }, [loadBlocks]);

  async function persistOrder(orderedBlocks) {
    setSaving(true);
    try {
      await api.put(BLOCKS.REORDER_COURSE(courseId), {
        block_ids: orderedBlocks.map((b) => b.id),
      });
      toast.success('Table of contents order saved.');
    } catch {
      toast.error('Failed to save TOC order.');
      loadBlocks();
    } finally {
      setSaving(false);
    }
  }

  function applyOrder(nextBlocks) {
    setBlocks(nextBlocks);
    persistOrder(nextBlocks);
  }

  function moveUp(index) {
    if (index <= 0) return;
    applyOrder(moveItem(blocks, index, index - 1));
  }

  function moveDown(index) {
    if (index >= blocks.length - 1) return;
    applyOrder(moveItem(blocks, index, index + 1));
  }

  function handleDragStart(index) {
    setDragIndex(index);
  }

  function handleDragOver(e, index) {
    e.preventDefault();
    if (dragIndex === null || dragIndex === index) return;
    setBlocks((prev) => moveItem(prev, dragIndex, index));
    setDragIndex(index);
  }

  function handleDragEnd() {
    if (dragIndex !== null) {
      persistOrder(blocksRef.current);
    }
    setDragIndex(null);
  }

  async function handleExportImscc() {
    if (!blocks.length) return;
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
      setBlocks((prev) =>
        prev.map((b) => (b.id === block.id ? { ...b, has_html: true } : b)),
      );
      if (preview && preview.block?.id === block.id) {
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

  if (!courseId) {
    return (
      <p className={styles.hint}>
        Open a course workspace to manage the published table of contents.
      </p>
    );
  }

  return (
    <section className={styles.panel}>
      <div className={styles.header}>
        <div>
          <h2 className={styles.title}>📑 Published Table of Contents</h2>
          <p className={styles.subtitle}>
            {courseName ? (
              <>Course: <strong>{courseName}</strong> — drag blocks to reorder, then export as IMS CC.</>
            ) : (
              <>Drag blocks to reorder, or use the arrows. Export as an IMS CC package for LMS import.</>
            )}
          </p>
        </div>
        <Button
          variant="primary"
          disabled={exporting || !blocks.length}
          onClick={handleExportImscc}
        >
          {exporting ? 'Exporting…' : '📦 Export IMSCC Package'}
        </Button>
      </div>

      {loading ? (
        <div className={styles.loading}><Loader size="md" /></div>
      ) : blocks.length === 0 ? (
        <div className={styles.hint}>
          <p>
            No published blocks in <strong>{courseName || `course #${courseId}`}</strong> yet.
          </p>
          <p>
            Publish blocks from the <strong>Workflow</strong> page, then return here to reorder
            and export.
          </p>
          {projectCourses.length > 1 && (
            <p className={styles.hintCourses}>
              Courses in this project: {projectCourses.map((c) => c.name).join(', ')}
            </p>
          )}
        </div>
      ) : (
        <ol className={styles.list}>
          {blocks.map((block, index) => (
            <li
              key={block.id}
              className={`${styles.item} ${dragIndex === index ? styles['item--dragging'] : ''}`}
              draggable
              onDragStart={() => handleDragStart(index)}
              onDragOver={(e) => handleDragOver(e, index)}
              onDragEnd={handleDragEnd}
            >
              <span className={styles.handle} title="Drag to reorder" aria-hidden="true">⠿</span>
              <span className={styles.index}>{index + 1}</span>
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
                  onClick={() => handlePreviewHtml(block)}
                  title={block.has_html ? 'Preview Canvas HTML' : 'No HTML generated yet — use Regenerate'}
                  aria-label="Preview Canvas HTML"
                >
                  👁
                </button>
                <button
                  type="button"
                  className={styles.iconBtn}
                  disabled={!block.has_html || downloadId === block.id}
                  onClick={() => handleDownloadHtml(block)}
                  title={downloadId === block.id ? 'Downloading…' : 'Download Canvas HTML'}
                  aria-label="Download Canvas HTML"
                >
                  {downloadId === block.id ? '⏳' : '⬇'}
                </button>
                <button
                  type="button"
                  className={styles.iconBtn}
                  disabled={regenId === block.id}
                  onClick={() => handleRegenerateHtml(block)}
                  title={regenId === block.id ? 'Generating HTML…' : 'Regenerate Canvas HTML'}
                  aria-label="Regenerate Canvas HTML"
                >
                  {regenId === block.id ? '⏳' : '↻'}
                </button>
                <div className={styles.arrowStack}>
                  <button
                    type="button"
                    className={styles.arrowBtn}
                    disabled={saving || index === 0}
                    onClick={() => moveUp(index)}
                    title="Move up"
                    aria-label="Move up"
                  >
                    ▲
                  </button>
                  <button
                    type="button"
                    className={styles.arrowBtn}
                    disabled={saving || index === blocks.length - 1}
                    onClick={() => moveDown(index)}
                    title="Move down"
                    aria-label="Move down"
                  >
                    ▼
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
      {saving && <p className={styles.saving}>Saving order…</p>}

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
