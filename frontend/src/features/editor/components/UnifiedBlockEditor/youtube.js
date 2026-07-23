/** YouTube URL helpers for the editor embed dialog. */

const ID_RE = /^[A-Za-z0-9_-]{6,15}$/;

/**
 * Normalize a user-supplied YouTube URL (watch, youtu.be, shorts, or embed) to
 * a canonical https embed URL. Returns null if it isn't a recognizable YouTube
 * link — the caller treats that as "unsupported source".
 */
export function toYouTubeEmbedUrl(input) {
  const raw = String(input || '').trim();
  if (!raw) return null;

  let url;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }

  const host = url.hostname.replace(/^www\./, '');
  let id = null;

  if (host === 'youtu.be') {
    id = url.pathname.slice(1);
  } else if (host === 'youtube.com' || host === 'youtube-nocookie.com') {
    if (url.pathname === '/watch') {
      id = url.searchParams.get('v');
    } else if (url.pathname.startsWith('/embed/')) {
      id = url.pathname.split('/embed/')[1];
    } else if (url.pathname.startsWith('/shorts/')) {
      id = url.pathname.split('/shorts/')[1];
    }
  }

  id = (id || '').split('/')[0].split('?')[0];
  if (!ID_RE.test(id)) return null;
  return `https://www.youtube.com/embed/${id}`;
}
