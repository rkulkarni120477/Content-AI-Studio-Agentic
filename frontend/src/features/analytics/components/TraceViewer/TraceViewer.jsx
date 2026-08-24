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

// Phoenix's span `attributes` can come back as flat dotted keys
// ("llm.model_name") or nested ("llm.model_name" -> {llm: {model_name}}) —
// check both rather than assume one shape.
function getAttr(attributes, dottedPath) {
  if (!attributes) return undefined;
  if (dottedPath in attributes) return attributes[dottedPath];
  return dottedPath.split('.').reduce((acc, key) => (acc == null ? acc : acc[key]), attributes);
}

// Renders Phoenix's raw span objects (see app/core/phoenix_client.py) — one
// call can have more than one span (nested calls), most have exactly one.
export default function TraceViewer({ observations }) {
  if (!observations?.length) {
    return <p className={styles.empty}>No observations recorded for this trace.</p>;
  }

  return (
    <div className={styles.trace}>
      {observations.map((span) => {
        const attrs = span.attributes;
        const model = getAttr(attrs, 'llm.model_name');
        const promptTokens = getAttr(attrs, 'llm.token_count.prompt');
        const completionTokens = getAttr(attrs, 'llm.token_count.completion');
        const totalTokens = getAttr(attrs, 'llm.token_count.total')
          ?? (promptTokens != null && completionTokens != null ? promptTokens + completionTokens : undefined);
        const input = getAttr(attrs, 'input.value');
        const output = getAttr(attrs, 'output.value');
        const isError = span.status_code === 'ERROR';

        return (
          <div key={span.id ?? span.context?.span_id} className={styles.observation}>
            <div className={styles.observation__header}>
              <span className={styles.observation__type}>{span.span_kind || 'SPAN'}</span>
              <span className={styles.observation__name}>{span.name}</span>
              {model && <span className={styles.observation__model}>{model}</span>}
            </div>
            <div className={styles.observation__meta}>
              {span.start_time && <span>{formatDateTime(span.start_time)}</span>}
              {(promptTokens != null || completionTokens != null) && (
                <span>
                  {promptTokens ?? 0} in / {completionTokens ?? 0} out / {totalTokens ?? 0} total tokens
                </span>
              )}
              {isError && (
                <span className={styles['observation__level--error']}>ERROR</span>
              )}
            </div>
            {isError && span.status_message && (
              <p className={styles.observation__status}>{span.status_message}</p>
            )}
            {input != null && (
              <div className={styles.observation__block}>
                <div className={styles.observation__blockLabel}>Input</div>
                <pre className={styles.observation__pre}>{asText(input)}</pre>
              </div>
            )}
            {output != null && (
              <div className={styles.observation__block}>
                <div className={styles.observation__blockLabel}>Output</div>
                <pre className={styles.observation__pre}>{asText(output)}</pre>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
