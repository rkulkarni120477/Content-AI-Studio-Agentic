import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import { platformService } from '@features/platform/services/platformService';
import { extractErrorMessage } from '@utils/helpers';
import {
  buildLabels,
  sanitizeOverrides,
  DEFAULT_LABELS,
  LABEL_FIELDS,
  LABEL_KEYS,
  MAX_LABEL_LENGTH,
} from '@config/tenantLabels';
import styles from './TenantLabelsPanel.module.scss';

/** Blank boxes for every key — a blank box means "use the default wording". */
function formFromTenant(tenant) {
  const saved = sanitizeOverrides(tenant?.ui_labels);
  return LABEL_KEYS.reduce((acc, key) => ({ ...acc, [key]: saved[key] || '' }), {});
}

/**
 * Per-tenant label editor: four boxes on the left, a live preview on the right.
 *
 * The preview is the point — it shows the three places a rename actually lands
 * (workspace sidebar, page heading, in-page section names) so the admin can see
 * the effect before saving rather than clicking into the tenant to check.
 */
export default function TenantLabelsPanel({ tenant, onBack, onSaved }) {
  const [form, setForm] = useState(() => formFromTenant(tenant));
  const [saving, setSaving] = useState(false);

  // Re-seed when the admin picks a different tenant without unmounting.
  useEffect(() => { setForm(formFromTenant(tenant)); }, [tenant?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Preview uses the same builder the real pages use, so what's shown here is
  // exactly what users will get — no second implementation to drift.
  const preview = useMemo(() => buildLabels(form), [form]);

  const savedOverrides = useMemo(() => sanitizeOverrides(tenant?.ui_labels), [tenant?.ui_labels]);
  const pendingOverrides = useMemo(() => sanitizeOverrides(form), [form]);
  const isDirty = JSON.stringify(savedOverrides) !== JSON.stringify(pendingOverrides);

  function set(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function resetAll() {
    setForm(LABEL_KEYS.reduce((acc, key) => ({ ...acc, [key]: '' }), {}));
  }

  async function handleSave() {
    setSaving(true);
    try {
      // Always send the whole object: a cleared box must clear the override,
      // and the server treats a missing key as "no override".
      await platformService.updateTenant(tenant.id, { ui_labels: pendingOverrides });
      toast.success(
        Object.keys(pendingOverrides).length
          ? 'Labels saved — everyone in this organization will see the new wording.'
          : 'Labels reset to the standard wording.',
      );
      onSaved?.();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <button type="button" className={styles.back} onClick={onBack}>
        ← All organizations
      </button>

      <div className={styles.headerRow}>
        <div>
          <div className={styles.eyebrow}>Configuration › {tenant.name}</div>
          <h1 className={styles.title}>Labels</h1>
          <p className={styles.subtitle}>Leave a box empty to keep the standard wording.</p>
        </div>
      </div>

      <div className={styles.split}>
        {/* ── the four boxes ── */}
        <div className={styles.card}>
          <div className={styles.fields}>
            {LABEL_FIELDS.map((field) => (
              <div key={field.key} className={styles.field}>
                <Input
                  label={field.label}
                  id={`label-${field.key}`}
                  value={form[field.key]}
                  placeholder={DEFAULT_LABELS[field.key]}
                  maxLength={MAX_LABEL_LENGTH}
                  hint={field.hint}
                  autoComplete="off"
                  onChange={(e) => set(field.key, e.target.value)}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  className={styles.resetBtn}
                  onClick={() => set(field.key, '')}
                  disabled={!form[field.key]}
                >
                  Reset
                </Button>
              </div>
            ))}
          </div>

          <div className={styles.actions}>
            <Button type="button" variant="ghost" onClick={resetAll} disabled={saving}>
              Reset all to default
            </Button>
            <Button
              type="button"
              variant="primary"
              onClick={handleSave}
              loading={saving}
              disabled={!isDirty}
            >
              {isDirty ? 'Save labels' : 'Saved'}
            </Button>
          </div>
        </div>

        {/* ── live preview ── */}
        <div className={styles.card}>
          <div className={styles.previewHead}>
            <span>What this organization will see</span>
            <span className={styles.live}>Live</span>
          </div>
          <div className={styles.previewBody}>
            <div className={styles.group}>
              <div className={styles.cap}>Workspace sidebar</div>
              <div className={styles.miniNav}>
                <div className={styles.miniItem}>📚 Source Library</div>
                <div className={`${styles.miniItem} ${styles.miniItemOn}`}>🎨 {preview.style}</div>
                <div className={styles.miniItem}>📘 {preview.cdd}</div>
                <div className={styles.miniItem}>🧩 {preview.blueprint}</div>
                <div className={styles.miniItem}>⚙️ Generate</div>
                <div className={styles.miniItem}>✏️ Editor</div>
              </div>
            </div>

            <div className={styles.group}>
              <div className={styles.cap}>Choosing a {preview.titleLower}</div>
              <div className={styles.previewLine}>
                <div className={styles.crumb}>Organization › Category</div>
                <div className={styles.previewH1}>Select {preview.title}</div>
              </div>
            </div>

            <div className={styles.group}>
              <div className={styles.cap}>Inside the {preview.style} page</div>
              <div className={styles.rows}>
                <div className={styles.row}>{preview.style} Management</div>
                <div className={styles.row}>＋ Create New {preview.style}</div>
                <div className={styles.row}>🗂️ Generated {preview.styles}</div>
                <div className={styles.row}>🎯 {preview.style} Prompts</div>
              </div>
            </div>

            <div className={styles.group}>
              <div className={styles.cap}>And inside sentences</div>
              <div className={styles.previewLine}>
                These prompts are inherited from this category and automatically prepended
                to the {preview.style} context for every {preview.titleLower} here.
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
