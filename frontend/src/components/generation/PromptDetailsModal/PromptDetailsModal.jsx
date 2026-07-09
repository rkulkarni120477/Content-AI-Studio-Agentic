import { useEffect, useMemo, useState } from 'react';
import { promptsService } from '@features/prompts/services/promptsService';
import { COMPONENT_LABELS, buildPromptDownloadMd } from '@utils/promptDefaults';
import { downloadBlob } from '@utils/helpers';
import Modal from '@components/common/Modal/Modal';
import Button from '@components/common/Button/Button';
import Loader from '@components/common/Loader/Loader';
import styles from './PromptDetailsModal.module.scss';

function formatDate(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toISOString().slice(0, 10);
}

export default function PromptDetailsModal({
  open,
  onClose,
  component = 'blueprint',
  promptMeta,
  systemPrompt = '',
  userPrompt = '',
  extraInstructions = '',
  isAiOverride = false,
  projectName,
  clusterName,
  courseName,
  onEditPrompt,
}) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);

  const compLabel = COMPONENT_LABELS[component] || component;

  useEffect(() => {
    if (!open || !promptMeta?.id) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    promptsService.getPromptDetail(promptMeta.id)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch(() => { if (!cancelled) setDetail(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, promptMeta?.id]);

  const name = detail?.name || promptMeta?.name || '—';
  const version = detail?.active_version || promptMeta?.active_version || 'v1';
  const owner = detail?.owner || promptMeta?.owner || '—';
  const updated = formatDate(detail?.updated_at || promptMeta?.updated_at);
  const isDefault = detail?.is_default ?? promptMeta?.is_default;

  const scopeParts = useMemo(() => {
    const parts = [];
    if (projectName) parts.push({ label: 'Project', value: projectName });
    if (clusterName) parts.push({ label: 'Category', value: clusterName });
    if (courseName) parts.push({ label: 'Course', value: courseName });
    return parts;
  }, [projectName, clusterName, courseName]);

  const viewSystem = (systemPrompt || detail?.system_prompt || '').trim() || '(empty)';
  const viewUser = (userPrompt || detail?.user_prompt_template || '').trim() || '(empty)';
  const viewExtra = (extraInstructions || '').trim();

  function handleDownload() {
    const md = buildPromptDownloadMd({
      projectName,
      clusterName,
      courseName,
      component,
      promptName: name,
      promptVersion: version,
      systemPrompt: systemPrompt || detail?.system_prompt,
      userPromptTemplate: userPrompt || detail?.user_prompt_template,
      extraInstructions: viewExtra,
      isAiOverride,
    });
    const safeVer = (version || 'v1').replace(/\//g, '-');
    downloadBlob(
      new Blob([md], { type: 'application/msword' }),
      `prompt_${component}_${safeVer}.doc`,
    );
  }

  function handleEdit() {
    onClose();
    onEditPrompt?.();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="🔍 Prompt Details"
      size="xl"
      footer={(
        <div className={styles.footer}>
          <Button variant="secondary" fullWidth onClick={handleDownload}>
            ⬇️ Download Prompt
          </Button>
          {onEditPrompt && (
            <Button variant="secondary" fullWidth onClick={handleEdit} disabled={!promptMeta}>
              ✏️ Edit Prompt
            </Button>
          )}
          <Button variant="primary" fullWidth onClick={onClose}>
            ✕ Close
          </Button>
        </div>
      )}
    >
      <div className={styles.body}>
        {loading ? (
          <div className={styles.center}><Loader /></div>
        ) : (
          <>
            <div className={styles.metaCard}>
              <dl className={styles.metaGrid}>
                <dt className={styles.metaLabel}>Name</dt>
                <dd className={styles.metaValue}>
                  {name}
                  {isDefault && <span className={styles.badge}>default</span>}
                </dd>

                <dt className={styles.metaLabel}>Component</dt>
                <dd className={styles.metaValueComponent}>{compLabel}</dd>

                <dt className={styles.metaLabel}>Version</dt>
                <dd className={styles.metaValueVersion}>
                  <code>{version}</code>
                  {isAiOverride && (
                    <span className={styles.badgeAi}>AI-improved · not saved</span>
                  )}
                </dd>

                <dt className={styles.metaLabel}>Owner</dt>
                <dd className={styles.metaValue}>{owner}</dd>

                <dt className={styles.metaLabel}>Updated</dt>
                <dd className={styles.metaValue}>{updated}</dd>

                <dt className={styles.metaLabel}>Scope</dt>
                <dd>
                  {scopeParts.length === 0 ? (
                    <span className={styles.scope}>—</span>
                  ) : (
                    <span className={styles.scope}>
                      {scopeParts.map((p, i) => (
                        <span key={p.label}>
                          {i > 0 && <span className={styles.sep}>›</span>}
                          {p.label}: <strong>{p.value}</strong>
                        </span>
                      ))}
                    </span>
                  )}
                </dd>
              </dl>
            </div>

            {isAiOverride && (
              <div className={styles.aiNotice} role="status">
                🤖 <strong>AI-improved prompt is active.</strong>
                {' '}The prompts shown here reflect the in-session AI version and have not
                been saved to the database.
              </div>
            )}

            <div className={styles.codeSection}>
              <p className={styles.codeLabel}>System Prompt</p>
              <pre className={styles.codeBlock}>{viewSystem}</pre>
            </div>

            <div className={styles.codeSection}>
              <p className={styles.codeLabel}>User Prompt Template</p>
              <pre className={styles.codeBlock}>{viewUser}</pre>
            </div>

            {viewExtra && (
              <div className={styles.codeSection}>
                <p className={styles.codeLabel}>Additional Instructions</p>
                <pre className={styles.codeBlock}>{viewExtra}</pre>
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
