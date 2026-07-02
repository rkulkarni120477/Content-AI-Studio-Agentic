import { z } from 'zod';

// ─── Reusable Field Schemas ───────────────────────────────────────────────────
export const requiredString = (label = 'This field') =>
  z.string().min(1, `${label} is required`).trim();

export const optionalString = () => z.string().trim().optional();

export const positiveInt = (label = 'Value') =>
  z.number({ invalid_type_error: `${label} must be a number` })
   .int()
   .positive(`${label} must be greater than 0`);

export const positiveFloat = (label = 'Value') =>
  z.number({ invalid_type_error: `${label} must be a number` })
   .positive(`${label} must be greater than 0`);

export const rating = () =>
  z.number().int().min(1, 'Minimum rating is 1').max(5, 'Maximum rating is 5');

// ─── Auth Schemas ─────────────────────────────────────────────────────────────
export const loginSchema = z.object({
  tenant_slug:    z.string().min(1, 'Organisation code is required').trim(),
  username:       requiredString('Username'),
  password:       z.string().min(1, 'Password is required'),
  platform_admin: z.boolean().optional(),
});

export const createUserSchema = z.object({
  username:   requiredString('Username').min(3, 'Username must be at least 3 characters'),
  password:   z.string().min(8, 'Password must be at least 8 characters'),
  role:       z.enum(['admin', 'author', 'reviewer'], { required_error: 'Role is required' }),
  email:      z.string().email('Invalid email address').optional().or(z.literal('')),
});

// ─── Style Schemas ────────────────────────────────────────────────────────────
export const createStyleSchema = z.object({
  name:                requiredString('Style name'),
  description:         optionalString(),
  custom_instructions: optionalString(),
  document_ids:        z.array(z.number()).optional(),
});

// ─── CDD Schemas ─────────────────────────────────────────────────────────────
export const createCddSchema = z.object({
  course_title:    requiredString('Course title'),
  document_title:  optionalString(),
  duration_hours:  positiveInt('Duration').optional(),
  style_id:        z.number().nullable().optional(),
  extra_instructions: optionalString(),
});

export const commitVersionSchema = z.object({
  tag:    optionalString(),
  reason: requiredString('Change reason'),
});

// ─── Blueprint Schemas ────────────────────────────────────────────────────────
export const createBlueprintSchema = z.object({
  cdd_id:        z.number({ required_error: 'CDD is required' }),
  module_number: z.number({ required_error: 'Module is required' }).int().positive(),
  document_title: optionalString(),
  generation_mode: z.enum(['student', 'teacher']).default('student'),
});

// ─── Generate Schemas ─────────────────────────────────────────────────────────
export const generateSchema = z.object({
  component_key:     requiredString('Component'),
  block_count:       positiveInt('Block count').max(20),
  extra_instructions: optionalString(),
  prompt_name:       optionalString(),
});

// ─── Review / Editor Schemas ──────────────────────────────────────────────────
export const reviewSchema = z.object({
  score:    rating(),
  approved: z.boolean(),
  comments: optionalString(),
});

export const editBlockSchema = z.object({
  content:     requiredString('Content'),
  edit_reason: optionalString(),
});

// ─── Prompt Schemas ───────────────────────────────────────────────────────────
export const commitPromptSchema = z.object({
  name:              requiredString('Asset name')
                       .regex(/^[a-z0-9_-]+$/, 'Use lowercase letters, numbers, hyphens, underscores only'),
  description:       optionalString(),
  component_type:    z.enum(['style', 'cdd', 'blueprint', 'generate'], { required_error: 'Type is required' }),
  system_prompt:     requiredString('System prompt'),
  user_prompt_template: requiredString('User template'),
  change_reason:     optionalString(),
  tags:              z.string().optional(),
});

// ─── Workflow Filter Schemas ──────────────────────────────────────────────────
export const workflowFilterSchema = z.object({
  status:       z.string().optional(),
  reviewer:     z.string().optional(),
  project_id:   z.number().nullable().optional(),
  course_id:    z.number().nullable().optional(),
  search:       z.string().optional(),
});

// ─── Document Upload Schemas ──────────────────────────────────────────────────
export const uploadDocumentSchema = z.object({
  doc_tag:  optionalString(),
  files:    z.instanceof(FileList).refine((f) => f.length > 0, 'At least one file is required'),
});

// ─── Central Repository Schemas ───────────────────────────────────────────────
export const createCentralItemSchema = z.object({
  name:        requiredString('Name'),
  item_type:   requiredString('Type'),
  content:     requiredString('Content'),
  description: optionalString(),
  tags:        z.string().optional(),
});

export const importFromRegistrySchema = z.object({
  prompt_name: requiredString('Prompt name'),
  version:     z.number().nullable().optional(),
});
