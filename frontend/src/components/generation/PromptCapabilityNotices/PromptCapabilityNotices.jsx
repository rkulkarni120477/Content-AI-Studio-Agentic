import styles from './PromptCapabilityNotices.module.scss';

/**
 * How the selected prompt template compares with what the generation pipeline can
 * actually emit — served with the prompt detail as `capability`
 * (GET /api/v1/prompts/{id}), computed by promptops_app.services.prompt_capability.
 *
 * Renders nothing at all in three cases, which are NOT the same thing and must never
 * be collapsed into a reassuring "looks fine" message:
 *   - `capability` is null: the API omits it for component types that never produce a
 *     day table, so nothing was assessed. Absence of notices is not absence of
 *     problems.
 *   - `assessed` is false: there was no prompt text to read.
 *   - the notice list is empty: the prompt and the pipeline agree.
 *
 * Severity drives the styling because the two ends of the scale mean opposite things:
 * `would_refuse_single_call` means generation would be REFUSED outright, and must not
 * look like an informational column count.
 */
export default function PromptCapabilityNotices({ capability }) {
  const notices = capability?.notices ?? [];
  if (!capability?.assessed || notices.length === 0) return null;

  const refused = Boolean(capability.would_refuse_single_call);
  const hasWarning = notices.some((n) => n.severity === 'warning');
  const className = [
    styles.panel,
    refused ? styles['panel--error'] : '',
    !refused && hasWarning ? styles['panel--warning'] : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={className} role={refused ? 'alert' : 'status'}>
      <p className={styles.panel__title}>
        {refused
          ? '⛔ This template cannot be used for generation'
          : '🔍 How this template compares with what the pipeline emits'}
      </p>
      <ul className={styles.panel__list}>
        {notices.map((n) => (
          <li key={n.message} data-severity={n.severity}>{n.message}</li>
        ))}
      </ul>
    </div>
  );
}
