import { useEffect, useState } from 'react';
import Modal from '@components/common/Modal/Modal';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { extractErrorMessage } from '@utils/helpers';

export default function EditEntityModal({
  open,
  onClose,
  entityType,
  entity,
  onSaved,
}) {
  const [name, setName]           = useState('');
  const [clientName, setClientName] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving]       = useState(false);
  const [error, setError]         = useState(null);

  useEffect(() => {
    if (!open || !entity) return;
    setError(null);

    async function load() {
      try {
        if (entityType === 'project') {
          const full = await dashboardService.getProject(entity.id);
          setName(full.name ?? '');
          setClientName(full.client_name ?? '');
          setDescription(full.description ?? '');
        } else if (entityType === 'course') {
          const full = await dashboardService.getCourse(entity.id);
          setName(full.name ?? '');
          setDescription(full.description ?? '');
          setClientName('');
        } else {
          setName(entity.name ?? '');
          setDescription(entity.description ?? '');
          setClientName('');
        }
      } catch {
        setName(entity.name ?? '');
        setDescription(entity.description ?? '');
        setClientName(entity.client_name ?? '');
      }
    }

    load();
  }, [open, entity, entityType]);

  async function handleSave(e) {
    e.preventDefault();
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (entityType === 'project') {
        await dashboardService.updateProject(entity.id, {
          name: name.trim(),
          client_name: clientName.trim() || null,
          description: description.trim() || null,
        });
      } else if (entityType === 'cluster') {
        await dashboardService.updateCluster(entity.id, {
          name: name.trim(),
          description: description.trim() || null,
        });
      } else {
        await dashboardService.updateCourse(entity.id, {
          name: name.trim(),
          description: description.trim() || null,
        });
      }
      onSaved?.();
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const titles = { project: 'Edit Project', cluster: 'Edit Cluster', course: 'Edit Course' };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={titles[entityType] ?? 'Edit'}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button variant="primary" onClick={handleSave} loading={saving}>Save</Button>
        </>
      }
    >
      <form onSubmit={handleSave}>
        <Input
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        {entityType === 'project' && (
          <Input
            label="Client Name"
            value={clientName}
            onChange={(e) => setClientName(e.target.value)}
          />
        )}
        <div style={{ marginTop: '1rem' }}>
          <label htmlFor="entity-desc" style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, marginBottom: '0.25rem' }}>
            Description
          </label>
          <textarea
            id="entity-desc"
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            style={{ width: '100%', padding: '0.5rem 0.75rem', borderRadius: '8px', border: '1px solid #d1d5db' }}
          />
        </div>
        {error && <p role="alert" style={{ color: 'var(--color-danger, #dc2626)', fontSize: '0.875rem' }}>{error}</p>}
      </form>
    </Modal>
  );
}
