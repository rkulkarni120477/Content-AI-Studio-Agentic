import { useEffect, useMemo, useState } from 'react';
import { buildCddUiBlocks } from '@utils/cddContent';
import { detectDluCddContent, buildDluWorksheetBlocks, replaceWorksheet } from '@utils/cddWorksheets';
import { renderMarkdownPreview, renderInlineMarkdown } from '@utils/markdownPreview';
import { parseItemsFromSection } from '@utils/blockItems';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import styles from './CddContentView.module.scss';

export default function CddContentView({
  fullContent,
  sections,
  editable = false,
  saving = false,
  onSaveBlock,
  onRegenerateSection,
  onRegenerateItem,
}) {
  // DLU (worksheet-based) CDDs render as tabs; standard CDDs use the accordion.
  const isDlu = useMemo(() => detectDluCddContent(fullContent), [fullContent]);

  const { csText, blocks: dluBlocks } = useMemo(
    () => (isDlu ? buildDluWorksheetBlocks(fullContent, sections) : { csText: '', blocks: [] }),
    [isDlu, fullContent, sections],
  );

  const uiBlocks = useMemo(
    () => (isDlu ? [] : buildCddUiBlocks(fullContent, sections)),
    [isDlu, fullContent, sections],
  );

  const [expanded, setExpanded] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [reasons, setReasons] = useState({});
  const [scopes, setScopes] = useState({});
  const [regenSection, setRegenSection] = useState(null);
  const [regenItem, setRegenItem] = useState(null);

  useEffect(() => {
    // Content/version changed — clear any in-progress edits. Keep all accordion
    // sections closed by default; users open only the section they need.
    setExpanded(null);
    setDrafts({});
    setReasons({});
    setScopes({});
  }, [fullContent, sections]);

  function toggle(key) {
    setExpanded((prev) => (prev === key ? null : key));
  }

  function getDraft(block) {
    return drafts[block.key] ?? block.content;
  }

  function isChanged(block) {
    return getDraft(block).trim() !== block.content.trim();
  }

  async function handleSave(block) {
    const reason = (reasons[block.key] || '').trim();
    if (!reason) return;
    const scope = scopes[block.key] === 'learning' ? 'learning' : 'one_time';
    if (block.dluKey) {
      // Splice the edited worksheet back into the single Course Structure blob,
      // then commit it through the standard single-section save path.
      const newCs = replaceWorksheet(csText, block.dluKey, getDraft(block));
      await onSaveBlock?.({ blockKey: 'Course Structure', content: newCs, reason, scope });
    } else {
      await onSaveBlock?.({ blockKey: block.key, content: getDraft(block), reason, scope });
    }
    setDrafts((d) => {
      const next = { ...d };
      delete next[block.key];
      return next;
    });
    setReasons((r) => {
      const next = { ...r };
      delete next[block.key];
      return next;
    });
  }

  async function handleRegenSection(block) {
    if (!onRegenerateSection) return;
    setRegenSection(block.key);
    try {
      if (block.dluKey) {
        await onRegenerateSection({
          blockKey: block.dluTitle,
          instruction: (reasons[block.key] || '').trim(),
          dluWorksheetKey: block.dluKey,
          dluCourseStructure: csText,
        });
      } else {
        await onRegenerateSection({
          blockKey: block.key,
          instruction: (reasons[block.key] || '').trim(),
        });
      }
    } finally {
      setRegenSection(null);
    }
  }

  async function handleRegenItem(block, idx) {
    if (!onRegenerateItem) return;
    setRegenItem(`${block.key}:${idx}`);
    try {
      if (block.dluKey) {
        await onRegenerateItem({
          blockKey: block.dluTitle,
          sectionContent: block.content,
          itemIndex: idx,
          instruction: (reasons[block.key] || '').trim(),
          dluWorksheetKey: block.dluKey,
          dluCourseStructure: csText,
        });
      } else {
        await onRegenerateItem({
          blockKey: block.key,
          sectionContent: block.content,
          itemIndex: idx,
          instruction: (reasons[block.key] || '').trim(),
        });
      }
    } finally {
      setRegenItem(null);
    }
  }

  // Shared editor body — used by both the accordion (standard) and tabs (DLU).
  function renderBody(block) {
    const draft = getDraft(block);
    const changed = isChanged(block);
    const reason = reasons[block.key] || '';
    const scope = scopes[block.key] || 'one_time';
    const canSave = Boolean(reason.trim());
    const items = editable ? parseItemsFromSection(block.content) : [];
    const sectionBusy = regenSection === block.key;

    return (
      <div className={styles.section__body}>
        <div
          className={`${styles.section__preview} markdown-content`}
          dangerouslySetInnerHTML={{ __html: renderMarkdownPreview(block.content) }}
        />

        {editable && (
          <>
            <label className={styles.editLabel} htmlFor={`cdd-edit-${block.key}`}>
              ✏️ Edit this block
            </label>
            <textarea
              id={`cdd-edit-${block.key}`}
              className={styles.editTextarea}
              value={draft}
              onChange={(e) => setDrafts((d) => ({ ...d, [block.key]: e.target.value }))}
            />
            {changed && (
              <p className={styles.editHint}>✏️ Edited. Add a reason before saving.</p>
            )}
            <Input
              label="Edit reason / Regen instruction"
              placeholder="e.g. Updated module duration, add a Bloom's verb"
              value={reason}
              onChange={(e) => setReasons((r) => ({ ...r, [block.key]: e.target.value }))}
            />
            <div className={styles.scopeRow} role="radiogroup" aria-label="Edit scope">
              <label className={styles.scopeLabel}>
                <input
                  type="radio"
                  name={`scope-${block.key}`}
                  checked={scope === 'one_time'}
                  onChange={() => setScopes((s) => ({ ...s, [block.key]: 'one_time' }))}
                />
                ⚡ Apply Once
              </label>
              <label className={styles.scopeLabel}>
                <input
                  type="radio"
                  name={`scope-${block.key}`}
                  checked={scope === 'learning'}
                  onChange={() => setScopes((s) => ({ ...s, [block.key]: 'learning' }))}
                />
                🧠 Use as Learning
              </label>
            </div>
            <div className={styles.saveRow}>
              <Button
                variant="primary"
                size="sm"
                loading={saving}
                disabled={!canSave}
                onClick={() => handleSave(block)}
              >
                💾 Save Edit
              </Button>
              {onRegenerateSection && (
                <Button
                  variant="secondary"
                  size="sm"
                  loading={sectionBusy}
                  disabled={saving || sectionBusy}
                  onClick={() => handleRegenSection(block)}
                  title="Regenerate this whole section with AI using the instruction above"
                >
                  🔄 Regenerate Section
                </Button>
              )}
              {!canSave && changed && (
                <span className={styles.editHint} style={{ margin: 0, padding: '4px 8px' }}>
                  Enter a reason to enable saving.
                </span>
              )}
            </div>

            {onRegenerateItem && items.length > 0 && (
              <div className={styles.itemRegen}>
                <p className={styles.itemRegen__head}>
                  🔄 Regenerate a single item — click ⟳ next to any item
                </p>
                <ul className={styles.itemList}>
                  {items.map((item, idx) => {
                    const busy = regenItem === `${block.key}:${idx}`;
                    return (
                      <li key={idx} className={styles.itemList__row}>
                        <span className={styles.itemList__text}>
                          <code className={styles.itemList__num}>{idx + 1}</code>
                          <span
                            className={styles.itemList__md}
                            dangerouslySetInnerHTML={{ __html: renderInlineMarkdown(item.text) }}
                          />
                        </span>
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={busy}
                          disabled={Boolean(regenItem) || sectionBusy}
                          onClick={() => handleRegenItem(block, idx)}
                          title={`Regenerate only item ${idx + 1}. Others stay unchanged.`}
                        >
                          ⟳
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    );
  }

  // DLU CDDs render their worksheets (Overview + Worksheet 1..N) as the same
  // stacked accordion the standard CDD uses — one collapsible section each.
  const blocks = isDlu ? dluBlocks : uiBlocks;

  if (!fullContent?.trim() && blocks.length === 0) {
    return <p className={styles.empty}>No CDD content yet.</p>;
  }

  if (blocks.length === 0) {
    return (
      <div
        className={`${styles.fallback} markdown-content`}
        dangerouslySetInnerHTML={{ __html: renderMarkdownPreview(fullContent) }}
      />
    );
  }

  return (
    <div className={styles.wrap}>
      {blocks.map((block) => {
        const isOpen = expanded === block.key;
        return (
          <div key={block.key} className={styles.section}>
            <button
              type="button"
              className={styles.section__head}
              onClick={() => toggle(block.key)}
              aria-expanded={isOpen}
            >
              <span>{block.label}</span>
              <span aria-hidden="true">{isOpen ? '▾' : '▸'}</span>
            </button>
            {isOpen && renderBody(block)}
          </div>
        );
      })}
    </div>
  );
}
