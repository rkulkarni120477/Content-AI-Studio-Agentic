import styles from './PromptCapabilityNotices.module.scss';

/**
 * How the selected prompt template compares with what the generation pipeline can
 * actually emit — served with the prompt detail as `capability`
 * (GET /api/v1/prompts/{id}), computed by promptops_app.services.prompt_capability.
 *
 * Renders nothing at all in four cases, which are NOT the same thing and must never
 * be collapsed into a reassuring "looks fine" message:
 *   - `capability` is null: the API omits it for component types that never produce a
 *     day table, so nothing was assessed. Absence of notices is not absence of
 *     problems.
 *   - `assessed` is false: there was no prompt text to read.
 *   - the notice list is empty: the prompt and the pipeline agree.
 *   - nothing in the list applies to THIS `scope` (below).
 *
 * `scope` places each notice beside the button it actually describes. One prompt
 * picker feeds two Generate buttons that produce different documents: the block-wide
 * digest pipeline owns the day table, so "matched 32 of 32" and the extra-column
 * lines are true of that path only — rendered above the single-call button they
 * described a document that button does not produce. Pass 'single' or 'block';
 * notices marked 'both' appear in either. The default, 'all', filters nothing.
 *
 * Severity drives the styling because the two ends of the scale mean opposite things:
 * `would_refuse_single_call` means single-call generation would be REFUSED outright,
 * and must not look like an informational column count. That refusal headline is
 * suppressed under scope 'block', where the pipeline does run — it refuses only if it
 * falls back — but the underlying error notice still shows, still styled as an error.
 */
export default function PromptCapabilityNotices({ capability, scope = 'all' }) {
  const all = capability?.notices ?? [];
  const notices = scope === 'all'
    ? all
    : all.filter((n) => !n.scope || n.scope === scope || n.scope === 'both');
  if (!capability?.assessed || notices.length === 0) return null;

  const refused = Boolean(capability.would_refuse_single_call) && scope !== 'block';
  const hasError = notices.some((n) => n.severity === 'error');
  const hasWarning = notices.some((n) => n.severity === 'warning');
  const className = [
    styles.panel,
    refused || hasError ? styles['panel--error'] : '',
    !refused && !hasError && hasWarning ? styles['panel--warning'] : '',
  ].filter(Boolean).join(' ');

  const title = refused
    ? '⛔ This template cannot be used for generation'
    : (scope === 'block'
      ? '🔍 How this template compares with what the block-wide pipeline emits'
      : '🔍 How this template compares with what the pipeline emits');

  return (
    <div className={className} role={refused || hasError ? 'alert' : 'status'}>
      <p className={styles.panel__title}>{title}</p>
      <ul className={styles.panel__list}>
        {notices.map((n) => (
          <li key={n.message} data-severity={n.severity}>{n.message}</li>
        ))}
      </ul>
    </div>
  );
}
