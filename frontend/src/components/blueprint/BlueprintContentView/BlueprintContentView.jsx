import { useEffect, useMemo, useState } from 'react';
import { buildBlueprintUiSections } from '@utils/blueprintContent';
import styles from './BlueprintContentView.module.scss';

export default function BlueprintContentView({ fullContent, sections }) {
  const uiSections = useMemo(
    () => buildBlueprintUiSections(fullContent, sections),
    [fullContent, sections],
  );

  const [expanded, setExpanded] = useState(() => new Set());

  useEffect(() => {
    if (uiSections.length > 0) {
      setExpanded(new Set([uiSections[0].title]));
    } else {
      setExpanded(new Set());
    }
  }, [uiSections]);

  function toggle(title) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      return next;
    });
  }

  if (!fullContent?.trim() && uiSections.length === 0) {
    return <p className={styles.empty}>No Blueprint content yet.</p>;
  }

  if (uiSections.length === 0) {
    return (
      <pre className={styles.fallback}>{fullContent}</pre>
    );
  }

  return (
    <div className={styles.wrap}>
      {uiSections.map((sec) => {
        const isOpen = expanded.has(sec.title);
        return (
          <div key={sec.title} className={styles.section}>
            <button
              type="button"
              className={styles.section__head}
              onClick={() => toggle(sec.title)}
              aria-expanded={isOpen}
            >
              <span>{sec.title}</span>
              <span aria-hidden="true">{isOpen ? '▾' : '▸'}</span>
            </button>
            {isOpen && (
              <div className={styles.section__body}>
                <pre className={styles.section__text}>{sec.content}</pre>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
