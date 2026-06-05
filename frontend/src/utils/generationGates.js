/** Component-level gates — mirrors promptops_app/parsers/blueprint_parser.py */

const MODULE_LEVEL = new Set([
  'module_assessment',
  'assessments',
  'assessment_plan',
  'teacher_resources',
  'worksheet',
  'journal_prompts',
]);

const COURSE_LEVEL = new Set([
  'project_work',
  'summative_assessments',
  'learning_activities',
]);

export function isModuleLevelComponent(value) {
  return MODULE_LEVEL.has(value);
}

export function isCourseLevelComponent(value) {
  return COURSE_LEVEL.has(value);
}

export function isModuleAssessmentLabel(label) {
  return /assessment/i.test(label || '');
}

/**
 * Whether Launch is allowed for module-level components given completion status.
 * Mirrors render_completion_gate() in Streamlit.
 */
export function canLaunchWithModuleGate(status, componentLabel, assessmentOverride) {
  if (!status) return true;
  if (status.completed) return true;

  const isAssessment = isModuleAssessmentLabel(componentLabel);
  if (isAssessment && status.generated_lessons === 0 && status.total_lessons === 0) {
    return true;
  }
  if (isAssessment && assessmentOverride) return true;
  if (!isAssessment) return false;
  return false;
}

export function shouldShowAssessmentOverride(status, component) {
  if (!status || !component || status.completed) return false;
  return (
    isModuleLevelComponent(component.value)
    && isModuleAssessmentLabel(component.label)
  );
}
