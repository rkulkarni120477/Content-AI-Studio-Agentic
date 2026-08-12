import { formatDateTime } from '@utils/helpers';
import styles from './TraceViewer.module.scss';

function tryParseJson(value) {
  if (typeof value !== 'string') return value;
  try { return JSON.parse(value); } catch { return value; }
}

function asText(value) {
  const parsed = tryParseJson(value);
  return typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2);
}

// Renders Langfuse's raw observation objects (see app/core/langfuse_client.py) —
// one call can have more than one observation (nested spans), most have exactly one.
export default function TraceViewer({ observations }) {
  if (!observations?.length) {
    return <p className={styles.empty}>No observations recorded for this trace.</p>;
  }

  return (
    <div className={styles.trace}>
      {observations.map((obs) => (
        <div key={obs.id} className={styles.observation}>
          <div className={styles.observation__header}>
            <span className={styles.observation__type}>{obs.type || 'OBSERVATION'}</span>
            <span className={styles.observation__name}>{obs.name}</span>
            {obs.model && <span className={styles.observation__model}>{obs.model}</span>}
          </div>
          <div className={styles.observation__meta}>
            {obs.startTime && <span>{formatDateTime(obs.startTime)}</span>}
            {obs.usageDetails && (
              <span>
                {obs.usageDetails.input ?? 0} in / {obs.usageDetails.output ?? 0} out / {obs.usageDetails.total ?? 0} total tokens
              </span>
            )}
            {typeof obs.totalCost === 'number' && obs.totalCost > 0 && (
              <span>${obs.totalCost.toFixed(4)}</span>
            )}
            {obs.level && obs.level !== 'DEFAULT' && (
              <span className={styles[`observation__level--${obs.level.toLowerCase()}`]}>{obs.level}</span>
            )}
          </div>
          {obs.statusMessage && <p className={styles.observation__status}>{obs.statusMessage}</p>}
          {obs.input != null && (
            <div className={styles.observation__block}>
              <div className={styles.observation__blockLabel}>Input</div>
              <pre className={styles.observation__pre}>{asText(obs.input)}</pre>
            </div>
          )}
          {obs.output != null && (
            <div className={styles.observation__block}>
              <div className={styles.observation__blockLabel}>Output</div>
              <pre className={styles.observation__pre}>{asText(obs.output)}</pre>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
