/** Lightweight markdown preview matching Streamlit st.markdown rendering for editor preview. */

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function renderMarkdownPreview(text) {
  if (!text) return '';
  const lines = escapeHtml(text).split('\n');
  const html = lines.map((line) => {
    if (/^#### (.+)$/.test(line)) return `<h4>${line.slice(5)}</h4>`;
    if (/^### (.+)$/.test(line)) return `<h3>${line.slice(4)}</h3>`;
    if (/^## (.+)$/.test(line)) return `<h2>${line.slice(3)}</h2>`;
    if (/^# (.+)$/.test(line)) return `<h1>${line.slice(2)}</h1>`;
    if (/^[-*] (.+)$/.test(line)) return `<li>${line.replace(/^[-*] /, '')}</li>`;
    if (line.trim() === '') return '<br/>';
    return line
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  }).join('<br/>');
  return html.replace(/(<li>.*?<\/li>(<br\/>)?)+/g, (m) => `<ul>${m}</ul>`);
}
