import { visibilityLabel } from '../../utils/prompt';

export default function VisibilityBadge({ prompt }) {
  const { className, text } = visibilityLabel(prompt);
  return <span className={`badge ${className}`}>{text}</span>;
}
