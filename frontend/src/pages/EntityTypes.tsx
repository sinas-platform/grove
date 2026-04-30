import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '@/lib/api';
import { PageHeader } from '@/components/PageHeader';
import {
  DangerButton,
  ErrorBanner,
  Field,
  PrimaryButton,
  inputClasses,
  textareaClasses,
} from '@/components/Form';

interface EntityType {
  id: string;
  name: string;
  description: string | null;
  guidance: string | null;
}

export default function EntityTypesPage() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const list = useQuery({
    queryKey: ['entity-types'],
    queryFn: () => api<EntityType[]>('/config/entity-types'),
  });

  const selected = list.data?.find((e) => e.id === selectedId) ?? null;

  return (
    <div>
      <PageHeader
        title="Entity types"
        description="Canonical kinds of entities the entity_extractor_agent looks for inside documents."
        actions={
          <PrimaryButton
            onClick={() => {
              setSelectedId(null);
              setCreating(true);
            }}
          >
            New entity type
          </PrimaryButton>
        }
      />
      <div className="grid grid-cols-12 gap-6">
        <div className="col-span-4 space-y-2">
          {(list.data ?? []).map((et) => (
            <button
              key={et.id}
              onClick={() => {
                setSelectedId(et.id);
                setCreating(false);
              }}
              className={`block w-full text-left p-3 border rounded ${
                selectedId === et.id
                  ? 'border-forest-500 bg-forest-50'
                  : 'border-stone-200 bg-white hover:border-stone-300'
              }`}
            >
              <div className="font-medium text-sm">{et.name}</div>
              {et.description && (
                <div className="text-xs text-stone-500 mt-1 line-clamp-2">{et.description}</div>
              )}
            </button>
          ))}
          {list.data && list.data.length === 0 && (
            <div className="text-stone-500 text-sm py-8 text-center border border-dashed border-stone-300 rounded">
              No entity types yet.
            </div>
          )}
        </div>
        <div className="col-span-8">
          {creating && (
            <Editor
              key="new"
              onSaved={(saved) => {
                qc.invalidateQueries({ queryKey: ['entity-types'] });
                setCreating(false);
                setSelectedId(saved.id);
              }}
            />
          )}
          {!creating && selected && (
            <Editor
              key={selected.id}
              initial={selected}
              onSaved={() => qc.invalidateQueries({ queryKey: ['entity-types'] })}
              onDeleted={() => {
                qc.invalidateQueries({ queryKey: ['entity-types'] });
                setSelectedId(null);
              }}
            />
          )}
          {!creating && !selected && (
            <div className="text-stone-500 text-sm py-12 text-center border border-dashed border-stone-300 rounded">
              Select an entity type to edit, or create a new one.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Editor({
  initial,
  onSaved,
  onDeleted,
}: {
  initial?: EntityType;
  onSaved: (saved: EntityType) => void;
  onDeleted?: () => void;
}) {
  const isNew = !initial;
  const [draft, setDraft] = useState({
    name: initial?.name ?? '',
    description: initial?.description ?? '',
    guidance: initial?.guidance ?? '',
  });
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () =>
      api<EntityType>(
        isNew ? '/config/entity-types' : `/config/entity-types/${initial!.id}`,
        { method: isNew ? 'POST' : 'PUT', body: JSON.stringify(draft) },
      ),
    onSuccess: (saved) => {
      setError(null);
      onSaved(saved);
    },
    onError: (err) => setError(err instanceof Error ? err.message : 'save failed'),
  });

  const del = useMutation({
    mutationFn: () =>
      api(`/config/entity-types/${initial!.id}`, { method: 'DELETE' }),
    onSuccess: () => onDeleted?.(),
    onError: (err) => setError(err instanceof Error ? err.message : 'delete failed'),
  });

  return (
    <div className="space-y-3 p-4 border border-stone-200 rounded bg-white">
      <div className="flex items-center justify-between">
        <div className="text-xs text-stone-400">
          {isNew ? 'New entity type' : `id: ${initial!.id.slice(0, 8)}`}
        </div>
        {!isNew && (
          <DangerButton
            onClick={() => {
              if (confirm(`Delete entity type "${initial!.name}"?`)) del.mutate();
            }}
          >
            Delete
          </DangerButton>
        )}
      </div>
      <Field label="Name">
        <input
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          className={inputClasses}
        />
      </Field>
      <Field label="Description">
        <textarea
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          rows={2}
          className={inputClasses}
        />
      </Field>
      <Field label="Guidance" hint="read by entity_extractor_agent">
        <textarea
          value={draft.guidance}
          onChange={(e) => setDraft({ ...draft, guidance: e.target.value })}
          rows={4}
          className={textareaClasses}
        />
      </Field>
      <ErrorBanner message={error} />
      <div>
        <PrimaryButton
          onClick={() => save.mutate()}
          disabled={save.isPending || !draft.name.trim()}
        >
          {save.isPending ? 'Saving…' : isNew ? 'Create' : 'Save'}
        </PrimaryButton>
      </div>
    </div>
  );
}
