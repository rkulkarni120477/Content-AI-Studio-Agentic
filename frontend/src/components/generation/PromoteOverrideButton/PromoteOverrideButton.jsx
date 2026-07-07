import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '@services/apiClient';
import { extractErrorMessage } from '@utils/helpers';
import Button from '@components/common/Button/Button';

function parseParams(raw) {
  if (!raw) return {};
  if (typeof raw === 'object') return raw;
  try { return JSON.parse(raw); } catch { return {}; }
}

// Doc §5.2 loop-closer (Phase 12e): a version generated with an inline
// override carries the full override text in its generation_params (11.9
// provenance). This offers to save that capture as a real registry prompt —
// opt-in (experiments never auto-register), admin-gated server-side, and
// inert for generation until made a default or scope-locked.
export default function PromoteOverrideButton({ sourceType, artifactId, version, generationParams }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const params = parseParams(generationParams);
  if (params.prompt_source !== 'override' || !artifactId || !version) return null;

  async function handleClick() {
    setBusy(true);
    try {
      const created = await api.post('/api/v1/prompts/from-generation', {
        source_type: sourceType,
        artifact_id: artifactId,
        version,
      });
      toast.success(`Saved as prompt "${created.name}" — make it a default or bind it in Courses to put it in use.`);
      navigate(`/prompt-library/prompts/${created.id}`);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      disabled={busy}
      title="This version was generated with a custom prompt override — save that override into the Prompt Library"
      onClick={handleClick}
    >
      {busy ? 'Saving…' : '💾 Save override as prompt'}
    </Button>
  );
}
