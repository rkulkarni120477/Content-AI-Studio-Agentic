import { useState } from 'react';
import { editorService, buildPromptMarkdown } from '@features/editor/services/editorService';
import { downloadBlob } from '@utils/helpers';
import Button from '@components/common/Button/Button';

export default function PromptDownloadButton({
  promptName,
  promptVersion,
  projectName,
  clusterName,
  courseName,
  component = 'generate',
  label = '⬇️ Download Prompt Used (.md)',
}) {
  const [loading, setLoading] = useState(false);

  async function onDownload() {
    if (!promptName) return;
    setLoading(true);
    try {
      const prompt = await editorService.getPromptByName(promptName);
      let systemPrompt = '';
      let userPrompt = '';
      if (prompt?.id) {
        const ver = await editorService.getPromptVersion(prompt.id, promptVersion || prompt.active_version);
        systemPrompt = ver?.system_prompt || '';
        userPrompt = ver?.user_prompt_template || '';
      }
      const md = buildPromptMarkdown({
        projectName,
        clusterName,
        courseName,
        component,
        promptName,
        promptVersion: promptVersion || prompt?.active_version || 'v1',
        systemPrompt,
        userPrompt,
      });
      const safeVer = (promptVersion || 'v1').replace(/[/\s]/g, '_');
      downloadBlob(
        new Blob([md], { type: 'text/markdown' }),
        `prompt_${component}_${safeVer}.md`,
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      loading={loading}
      disabled={!promptName}
      onClick={onDownload}
    >
      {label}
    </Button>
  );
}
