/**
 * AgentForm Component
 * Form for creating and editing agents
 */

import React, { useEffect } from 'react';
import { useForm, Controller } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import type { Agent, AgentCreateRequest, AgentUpdateRequest, AgentConfiguration } from '@types/agent';
import styles from './AgentForm.module.scss';

// Validation schema
const agentFormSchema = z.object({
  name: z.string().min(1, 'Name is required').max(255, 'Name is too long'),
  call_handle: z.string()
    .min(1, 'Handle is required')
    .max(255, 'Handle is too long')
    .regex(/^[a-z0-9_-]+$/, 'Handle must contain only lowercase letters, numbers, hyphens, and underscores'),
  template_id: z.number().int().positive('Template is required'),
  description: z.string().max(1000, 'Description is too long').optional().or(z.literal('')),
  model_id: z.string().optional().or(z.literal('')),
  temperature: z.number().min(0).max(2).optional(),
  max_tokens: z.number().int().min(1).optional(),
});

type AgentFormData = z.infer<typeof agentFormSchema>;

interface AgentFormProps {
  agent?: Agent;
  isLoading?: boolean;
  onSubmit: (data: AgentCreateRequest | AgentUpdateRequest) => Promise<void>;
  templates?: Array<{ id: number; name: string }>;
  models?: Array<{ id: string; name: string }>;
}

export function AgentForm({
  agent,
  isLoading = false,
  onSubmit,
  templates = [],
  models = [
    { id: 'openai:gpt-4o', name: 'GPT-4o' },
    { id: 'openai:gpt-4-turbo', name: 'GPT-4 Turbo' },
    { id: 'openai:gpt-3.5-turbo', name: 'GPT-3.5 Turbo' },
  ],
}: AgentFormProps) {
  const {
    control,
    register,
    handleSubmit,
    formState: { errors },
    reset,
  } = useForm<AgentFormData>({
    resolver: zodResolver(agentFormSchema),
    defaultValues: {
      name: agent?.name || '',
      call_handle: agent?.call_handle || '',
      template_id: agent?.template_id,
      description: agent?.description || '',
      model_id: agent?.configuration?.model_id || '',
      temperature: agent?.configuration?.temperature || 0.7,
      max_tokens: agent?.configuration?.max_tokens || 2000,
    },
  });

  useEffect(() => {
    if (agent) {
      reset({
        name: agent.name,
        call_handle: agent.call_handle,
        template_id: agent.template_id,
        description: agent.description,
        model_id: agent.configuration?.model_id,
        temperature: agent.configuration?.temperature,
        max_tokens: agent.configuration?.max_tokens,
      });
    }
  }, [agent, reset]);

  const handleFormSubmit = async (data: AgentFormData) => {
    const configuration: AgentConfiguration = {
      model_id: data.model_id,
      temperature: data.temperature,
      max_tokens: data.max_tokens,
    };

    const payload = agent
      ? {
          name: data.name,
          description: data.description,
          configuration,
        }
      : {
          name: data.name,
          template_id: data.template_id,
          call_handle: data.call_handle,
          description: data.description,
          configuration,
        };

    await onSubmit(payload);
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit(handleFormSubmit)}>
      <div className={styles.form__section}>
        <h3 className={styles.form__sectionTitle}>Basic Information</h3>

        <div className={styles.form__group}>
          <label className={styles.form__label} htmlFor="name">
            Agent Name *
          </label>
          <Input
            id="name"
            placeholder="e.g., Content Analyzer"
            {...register('name')}
            error={errors.name?.message}
            disabled={isLoading}
          />
          <p className={styles.form__help}>Give your agent a descriptive name</p>
        </div>

        {!agent && (
          <div className={styles.form__group}>
            <label className={styles.form__label} htmlFor="call_handle">
              Call Handle *
            </label>
            <Input
              id="call_handle"
              placeholder="e.g., content-analyzer"
              {...register('call_handle')}
              error={errors.call_handle?.message}
              disabled={isLoading}
            />
            <p className={styles.form__help}>
              Unique identifier for this agent (lowercase, hyphens/underscores only)
            </p>
          </div>
        )}

        {!agent && (
          <div className={styles.form__group}>
            <label className={styles.form__label} htmlFor="template_id">
              Template *
            </label>
            <select
              id="template_id"
              {...register('template_id', { valueAsNumber: true })}
              className={styles.form__select}
              disabled={isLoading}
            >
              <option value="">Select a template</option>
              {templates.map((template) => (
                <option key={template.id} value={template.id}>
                  {template.name}
                </option>
              ))}
            </select>
            {errors.template_id && (
              <p className={styles.form__error}>{errors.template_id.message}</p>
            )}
          </div>
        )}

        <div className={styles.form__group}>
          <label className={styles.form__label} htmlFor="description">
            Description
          </label>
          <textarea
            id="description"
            placeholder="Describe what this agent does..."
            {...register('description')}
            className={styles.form__textarea}
            disabled={isLoading}
            rows={4}
          />
          {errors.description && (
            <p className={styles.form__error}>{errors.description.message}</p>
          )}
        </div>
      </div>

      <div className={styles.form__section}>
        <h3 className={styles.form__sectionTitle}>Model Configuration</h3>

        <div className={styles.form__group}>
          <label className={styles.form__label} htmlFor="model_id">
            Model
          </label>
          <select
            id="model_id"
            {...register('model_id')}
            className={styles.form__select}
            disabled={isLoading}
          >
            <option value="">Auto-select</option>
            {models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.name}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.form__row}>
          <div className={styles.form__group}>
            <label className={styles.form__label} htmlFor="temperature">
              Temperature
            </label>
            <Input
              id="temperature"
              type="number"
              step={0.1}
              min={0}
              max={2}
              {...register('temperature', { valueAsNumber: true })}
              error={errors.temperature?.message}
              disabled={isLoading}
            />
            <p className={styles.form__help}>
              Randomness (0 = deterministic, 2 = creative)
            </p>
          </div>

          <div className={styles.form__group}>
            <label className={styles.form__label} htmlFor="max_tokens">
              Max Tokens
            </label>
            <Input
              id="max_tokens"
              type="number"
              min={1}
              {...register('max_tokens', { valueAsNumber: true })}
              error={errors.max_tokens?.message}
              disabled={isLoading}
            />
            <p className={styles.form__help}>Maximum output length</p>
          </div>
        </div>
      </div>

      <div className={styles.form__actions}>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          loading={isLoading}
        >
          {agent ? 'Update Agent' : 'Create Agent'}
        </Button>
      </div>
    </form>
  );
}

export default AgentForm;
