/** Human labels and workspace hrefs for GenerationJob.job_type values. */

export const JOB_TYPE_META = {
  generation: {
    label: 'Content generation',
    complete: 'Content generation complete',
    failed: 'Content generation failed',
    segment: 'generate',
  },
  cdd: {
    label: 'CDD generation',
    complete: 'CDD generation complete',
    failed: 'CDD generation failed',
    segment: 'cdd',
  },
  cdd_block: {
    label: 'CDD block generation',
    complete: 'CDD generation complete',
    failed: 'CDD generation failed',
    segment: 'cdd',
  },
  blueprint: {
    label: 'Blueprint generation',
    complete: 'Blueprint generation complete',
    failed: 'Blueprint generation failed',
    segment: 'blueprint',
  },
  blueprint_block: {
    label: 'Blueprint block generation',
    complete: 'Blueprint generation complete',
    failed: 'Blueprint generation failed',
    segment: 'blueprint',
  },
  outline_import: {
    label: 'Outline import',
    complete: 'Outline import complete',
    failed: 'Outline import failed',
    segment: 'blueprint',
  },
  style_understand: {
    label: 'Style understanding',
    complete: 'Style understanding complete',
    failed: 'Style understanding failed',
    segment: 'style',
  },
  regenerate_item: {
    label: 'Item regeneration',
    complete: 'Item regeneration complete',
    failed: 'Item regeneration failed',
    segment: 'editor',
  },
  regenerate_block: {
    label: 'Block regeneration',
    complete: 'Block regeneration complete',
    failed: 'Block regeneration failed',
    segment: 'editor',
  },
  cdd_regen_item: {
    label: 'CDD item regeneration',
    complete: 'CDD item regeneration complete',
    failed: 'CDD item regeneration failed',
    segment: 'cdd',
  },
  cdd_regen_section: {
    label: 'CDD section regeneration',
    complete: 'CDD section regeneration complete',
    failed: 'CDD section regeneration failed',
    segment: 'cdd',
  },
  blueprint_regen_item: {
    label: 'Blueprint item regeneration',
    complete: 'Blueprint item regeneration complete',
    failed: 'Blueprint item regeneration failed',
    segment: 'blueprint',
  },
  blueprint_regen_section: {
    label: 'Blueprint section regeneration',
    complete: 'Blueprint section regeneration complete',
    failed: 'Blueprint section regeneration failed',
    segment: 'blueprint',
  },
  apply_feedback: {
    label: 'Apply feedback',
    complete: 'Feedback applied',
    failed: 'Applying feedback failed',
    segment: 'feedback',
  },
  import: {
    label: 'Import',
    complete: 'Import complete',
    failed: 'Import failed',
    segment: 'sources',
  },
};

export function jobTypeMeta(jobType) {
  return JOB_TYPE_META[jobType] || {
    label: jobType || 'Background job',
    complete: 'Job complete',
    failed: 'Job failed',
    segment: 'generate',
  };
}

export function jobHref(job) {
  const courseId = job?.courseId ?? job?.course_id;
  const meta = jobTypeMeta(job?.jobType ?? job?.job_type);
  if (!courseId) return null;
  return `/workspace/${courseId}/${meta.segment}`;
}
