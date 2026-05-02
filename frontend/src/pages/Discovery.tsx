import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '@/lib/api';
import { PageHeader } from '@/components/PageHeader';
import {
  DangerButton,
  ErrorBanner,
  Field,
  PrimaryButton,
  SecondaryButton,
  inputClasses,
  textareaClasses,
} from '@/components/Form';

type Kind =
  | 'document_class'
  | 'entity_type'
  | 'relationship_definition'
  | 'dossier_class'
  | 'document_class_property';
type Mode = 'greenfield' | 'incremental';

interface DocumentClass {
  id: string;
  slug: string;
  name: string;
}

interface DiscoveryRun {
  id: string;
  kind: string;
  status: string;
  mode: string;
  total_docs: number;
  scanned_docs: number;
  failed_docs: number;
  candidate_count: number;
  proposal_count: number;
  parent_class_id: string | null;
  sample_size: number | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

interface Proposal {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  status: string;
  supporting_candidate_ids: string[];
  discovery_run_id: string | null;
  created_resource_id: string | null;
  merged_into_id: string | null;
  created_at: string;
}

interface Candidate {
  id: string;
  payload: Record<string, unknown>;
  evidence_document_id: string | null;
  evidence_span: Record<string, unknown> | null;
  confidence: number | null;
}

const KIND_LABELS: Record<Kind, string> = {
  document_class: 'Document classes',
  entity_type: 'Entity types',
  relationship_definition: 'Relationships',
  dossier_class: 'Dossier classes',
  document_class_property: 'Properties',
};

const KIND_ORDER: Kind[] = [
  'document_class',
  'entity_type',
  'relationship_definition',
  'dossier_class',
  'document_class_property',
];

export default function DiscoveryPage() {
  const [tab, setTab] = useState<Kind>('document_class');
  const [showNewRun, setShowNewRun] = useState(false);

  return (
    <div>
      <PageHeader
        title="Discovery"
        description="Run agents over the corpus to suggest new document classes, entity types, relationships, dossier classes, or properties. Suggestions are deduplicated and shown here for review."
        actions={
          <PrimaryButton onClick={() => setShowNewRun((v) => !v)}>
            {showNewRun ? 'Cancel' : 'New discovery run'}
          </PrimaryButton>
        }
      />

      {showNewRun && <NewRunForm onClose={() => setShowNewRun(false)} />}

      <RunsList />

      <div className="mt-8">
        <div className="flex gap-1 border-b border-stone-200 mb-4">
          {KIND_ORDER.map((k) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`px-4 py-2 text-sm border-b-2 -mb-px ${
                tab === k
                  ? 'border-forest-600 text-forest-700 font-medium'
                  : 'border-transparent text-stone-600 hover:text-stone-900'
              }`}
            >
              {KIND_LABELS[k]}
            </button>
          ))}
        </div>
        <ProposalsList kind={tab} />
      </div>
    </div>
  );
}

// ─────────────────────── new run ───────────────────────
function NewRunForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState<Kind>('document_class');
  const [mode, setMode] = useState<Mode>('incremental');
  const [parentClassId, setParentClassId] = useState<string>('');
  const [sampleSize, setSampleSize] = useState<string>('50');
  const [classFilter, setClassFilter] = useState<Set<string>>(new Set());
  const [createdSince, setCreatedSince] = useState<string>('');
  const [preview, setPreview] = useState<{ document_count: number; sampled: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const classes = useQuery({
    queryKey: ['document-classes'],
    queryFn: () => api<DocumentClass[]>('/config/document-classes'),
  });

  const buildBody = (dryRun: boolean) => ({
    kind,
    mode,
    filter: {
      document_class_ids: classFilter.size > 0 ? Array.from(classFilter) : null,
      created_since: createdSince ? new Date(createdSince).toISOString() : null,
    },
    sample_size: sampleSize ? Number(sampleSize) : null,
    parent_class_id: kind === 'document_class_property' && parentClassId ? parentClassId : null,
    dry_run: dryRun,
  });

  const previewMutation = useMutation({
    mutationFn: () =>
      api<{ document_count: number; sampled: boolean }>('/discovery/runs', {
        method: 'POST',
        body: JSON.stringify(buildBody(true)),
      }),
    onSuccess: (res) => {
      setPreview(res);
      setError(null);
    },
    onError: (err) => setError(err instanceof Error ? err.message : 'preview failed'),
  });

  const submit = useMutation({
    mutationFn: () =>
      api<{ run_id: string }>('/discovery/runs', {
        method: 'POST',
        body: JSON.stringify(buildBody(false)),
      }),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ['discovery-runs'] });
      onClose();
    },
    onError: (err) => setError(err instanceof Error ? err.message : 'failed'),
  });

  return (
    <div className="mb-6 p-4 border border-forest-500 bg-forest-50 rounded space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Kind">
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as Kind);
              setPreview(null);
            }}
            className={inputClasses}
          >
            {KIND_ORDER.map((k) => (
              <option key={k} value={k}>
                {KIND_LABELS[k]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Mode" hint="incremental skips already-configured items">
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as Mode)}
            className={inputClasses}
          >
            <option value="incremental">incremental</option>
            <option value="greenfield">greenfield</option>
          </select>
        </Field>
      </div>

      {kind === 'document_class_property' && (
        <Field label="Parent document class" hint="required for property discovery">
          <select
            value={parentClassId}
            onChange={(e) => {
              setParentClassId(e.target.value);
              setPreview(null);
            }}
            className={inputClasses}
          >
            <option value="">(pick one)</option>
            {(classes.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.slug})
              </option>
            ))}
          </select>
        </Field>
      )}

      <Field
        label="Filter by document class (optional)"
        hint={kind === 'document_class_property' ? 'ignored — parent class above is used' : 'leave empty to scan all'}
      >
        <div className="flex flex-wrap gap-2">
          {(classes.data ?? []).map((c) => (
            <button
              key={c.id}
              onClick={() => {
                const n = new Set(classFilter);
                if (n.has(c.id)) n.delete(c.id);
                else n.add(c.id);
                setClassFilter(n);
                setPreview(null);
              }}
              className={`px-2 py-1 rounded border text-xs ${
                classFilter.has(c.id)
                  ? 'border-forest-500 bg-forest-50 text-forest-700'
                  : 'border-stone-300 text-stone-700 hover:bg-stone-100'
              }`}
            >
              {c.name}
            </button>
          ))}
        </div>
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Sample size" hint="random sample of N docs (blank = all)">
          <input
            type="number"
            min="1"
            value={sampleSize}
            onChange={(e) => {
              setSampleSize(e.target.value);
              setPreview(null);
            }}
            className={inputClasses}
          />
        </Field>
        <Field label="Created since (optional)">
          <input
            type="datetime-local"
            value={createdSince}
            onChange={(e) => {
              setCreatedSince(e.target.value);
              setPreview(null);
            }}
            className={inputClasses}
          />
        </Field>
      </div>

      {preview && (
        <div className="text-sm text-stone-700 bg-white border border-stone-200 rounded px-3 py-2">
          Would scan <b>{preview.document_count}</b> document(s).{' '}
          {preview.sampled && <span className="text-stone-500">(sampled)</span>}
        </div>
      )}
      <ErrorBanner message={error} />
      <div className="flex gap-2">
        <SecondaryButton onClick={() => previewMutation.mutate()}>
          {previewMutation.isPending ? 'Counting…' : 'Preview count'}
        </SecondaryButton>
        <PrimaryButton onClick={() => submit.mutate()} disabled={submit.isPending}>
          {submit.isPending ? 'Starting…' : 'Start run'}
        </PrimaryButton>
        <SecondaryButton onClick={onClose}>Close</SecondaryButton>
      </div>
    </div>
  );
}

// ─────────────────────── runs list ───────────────────────
function RunsList() {
  const runs = useQuery({
    queryKey: ['discovery-runs'],
    queryFn: () => api<DiscoveryRun[]>('/discovery/runs'),
    refetchInterval: 4000,
  });

  if (!runs.data || runs.data.length === 0) return null;

  return (
    <div className="space-y-2">
      {runs.data.slice(0, 5).map((r) => {
        const pct = r.total_docs > 0 ? Math.round((r.scanned_docs / r.total_docs) * 100) : 0;
        const statusColor =
          {
            pending: 'bg-stone-200 text-stone-600',
            scanning: 'bg-blue-100 text-blue-700',
            consolidating: 'bg-purple-100 text-purple-700',
            completed: 'bg-forest-100 text-forest-700',
            failed: 'bg-red-100 text-red-700',
          }[r.status] ?? 'bg-stone-200 text-stone-600';
        return (
          <div key={r.id} className="p-3 border border-stone-200 rounded bg-white">
            <div className="flex items-baseline justify-between">
              <div>
                <span className={`text-xs px-2 py-0.5 rounded ${statusColor}`}>{r.status}</span>
                <span className="ml-2 font-mono text-xs text-stone-500">{r.kind}</span>
                <span className="ml-2 text-xs text-stone-400">{r.mode}</span>
              </div>
              <div className="text-xs text-stone-400 font-mono">{r.id.slice(0, 8)}</div>
            </div>
            <div className="w-full bg-stone-200 rounded h-1.5 mt-2 overflow-hidden">
              <div
                className={`h-full ${r.failed_docs > 0 ? 'bg-amber-500' : 'bg-forest-500'}`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="text-xs text-stone-500 mt-1">
              {r.scanned_docs}/{r.total_docs} docs · {r.candidate_count} raw → {r.proposal_count} proposals
              {r.failed_docs > 0 && (
                <span className="text-amber-700"> · {r.failed_docs} failed</span>
              )}
            </div>
            {r.error && <div className="text-xs text-red-700 mt-1 font-mono">{r.error}</div>}
          </div>
        );
      })}
    </div>
  );
}

// ─────────────────────── proposals list ───────────────────────
function ProposalsList({ kind }: { kind: Kind }) {
  const proposals = useQuery({
    queryKey: ['proposals', kind],
    queryFn: () => api<Proposal[]>(`/discovery/proposals?kind=${kind}&status_filter=pending`),
    refetchInterval: 5000,
  });

  if (!proposals.data) return <div className="text-stone-500">Loading…</div>;
  if (proposals.data.length === 0) {
    return (
      <div className="text-stone-500 text-sm py-12 text-center border border-dashed border-stone-300 rounded">
        No pending {KIND_LABELS[kind].toLowerCase()} proposals.
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {proposals.data.map((p) => (
        <ProposalCard key={p.id} proposal={p} kind={kind} />
      ))}
    </div>
  );
}

function ProposalCard({ proposal, kind }: { proposal: Proposal; kind: Kind }) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draftPayload, setDraftPayload] = useState(JSON.stringify(proposal.payload, null, 2));
  const [showMerge, setShowMerge] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const candidates = useQuery({
    queryKey: ['proposal-candidates', proposal.id],
    queryFn: () => api<Candidate[]>(`/discovery/proposals/${proposal.id}/candidates`),
    enabled: expanded,
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['proposals', kind] });
  };

  const approve = useMutation({
    mutationFn: () => api(`/discovery/proposals/${proposal.id}/approve`, { method: 'POST' }),
    onSuccess: invalidate,
    onError: (err) => setError(err instanceof Error ? err.message : 'failed'),
  });
  const reject = useMutation({
    mutationFn: () => api(`/discovery/proposals/${proposal.id}/reject`, { method: 'POST' }),
    onSuccess: invalidate,
    onError: (err) => setError(err instanceof Error ? err.message : 'failed'),
  });
  const save = useMutation({
    mutationFn: () => {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(draftPayload);
      } catch (e) {
        throw new Error(`payload is not valid JSON: ${(e as Error).message}`);
      }
      return api(`/discovery/proposals/${proposal.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ payload: parsed }),
      });
    },
    onSuccess: () => {
      invalidate();
      setEditing(false);
    },
    onError: (err) => setError(err instanceof Error ? err.message : 'failed'),
  });

  const name = (proposal.payload?.name as string) ?? '(unnamed)';
  const description = proposal.payload?.description as string | undefined;

  return (
    <div className="p-4 border border-stone-200 rounded bg-white">
      <div className="flex items-baseline justify-between mb-1">
        <div className="font-medium text-sm">{name}</div>
        <div className="text-xs text-stone-400">
          {proposal.supporting_candidate_ids.length} evidence
        </div>
      </div>
      {description && <div className="text-sm text-stone-600 mb-2">{description}</div>}

      {editing ? (
        <textarea
          value={draftPayload}
          onChange={(e) => setDraftPayload(e.target.value)}
          rows={Math.min(20, draftPayload.split('\n').length + 2)}
          className={textareaClasses + ' text-xs'}
        />
      ) : (
        <pre className="text-xs bg-stone-50 px-2 py-1 rounded overflow-auto">
          {JSON.stringify(proposal.payload, null, 2)}
        </pre>
      )}

      <ErrorBanner message={error} />

      <div className="flex gap-2 mt-3">
        {editing ? (
          <>
            <PrimaryButton onClick={() => save.mutate()} disabled={save.isPending}>
              {save.isPending ? 'Saving…' : 'Save'}
            </PrimaryButton>
            <SecondaryButton
              onClick={() => {
                setEditing(false);
                setDraftPayload(JSON.stringify(proposal.payload, null, 2));
              }}
            >
              Cancel
            </SecondaryButton>
          </>
        ) : (
          <>
            <PrimaryButton onClick={() => approve.mutate()} disabled={approve.isPending}>
              {approve.isPending ? 'Approving…' : 'Approve'}
            </PrimaryButton>
            <SecondaryButton onClick={() => setShowMerge((v) => !v)}>
              {showMerge ? 'Cancel merge' : 'Merge into…'}
            </SecondaryButton>
            <SecondaryButton onClick={() => setEditing(true)}>Edit</SecondaryButton>
            <DangerButton onClick={() => reject.mutate()}>Reject</DangerButton>
            <SecondaryButton onClick={() => setExpanded((v) => !v)}>
              {expanded ? 'Hide evidence' : 'Show evidence'}
            </SecondaryButton>
          </>
        )}
      </div>

      {showMerge && (
        <MergeBox
          proposalId={proposal.id}
          kind={kind}
          payload={proposal.payload}
          onDone={invalidate}
        />
      )}

      {expanded && (
        <div className="mt-3 pt-3 border-t border-stone-200">
          <div className="text-xs font-semibold uppercase tracking-wider text-stone-500 mb-2">
            Supporting candidates
          </div>
          {candidates.isLoading && <div className="text-stone-500 text-sm">Loading…</div>}
          <div className="space-y-1">
            {(candidates.data ?? []).map((c) => (
              <div key={c.id} className="text-xs p-2 bg-stone-50 rounded">
                <div className="flex items-baseline justify-between">
                  <span className="font-mono text-stone-400">
                    doc {c.evidence_document_id?.slice(0, 8) ?? '—'}
                  </span>
                  {c.confidence != null && (
                    <span className="text-stone-400">conf {c.confidence.toFixed(2)}</span>
                  )}
                </div>
                <pre className="mt-1 overflow-auto">
                  {JSON.stringify(c.payload, null, 2)}
                </pre>
                {c.evidence_span && (
                  <pre className="mt-1 text-stone-500 overflow-auto">
                    {JSON.stringify(c.evidence_span, null, 2)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MergeBox({
  proposalId,
  kind,
  payload,
  onDone,
}: {
  proposalId: string;
  kind: Kind;
  payload: Record<string, unknown>;
  onDone: () => void;
}) {
  // For document_class_property merging is scoped to the parent class's existing properties.
  const parentClassId =
    kind === 'document_class_property' ? (payload.document_class_id as string | undefined) : undefined;

  const targets = useQuery({
    queryKey: ['merge-targets', kind, parentClassId ?? null],
    queryFn: () => {
      if (kind === 'document_class') return api<{ id: string; name: string }[]>('/config/document-classes');
      if (kind === 'entity_type') return api<{ id: string; name: string }[]>('/config/entity-types');
      if (kind === 'relationship_definition')
        return api<{ id: string; name: string }[]>('/config/relationship-definitions');
      if (kind === 'dossier_class')
        return api<{ id: string; name: string }[]>('/config/dossier-classes');
      if (kind === 'document_class_property' && parentClassId)
        return api<{ id: string; name: string }[]>(
          `/config/document-classes/${parentClassId}/properties`,
        );
      return Promise.resolve([] as { id: string; name: string }[]);
    },
  });
  const [target, setTarget] = useState('');
  const [error, setError] = useState<string | null>(null);

  const mergeMutation = useMutation({
    mutationFn: () =>
      api(`/discovery/proposals/${proposalId}/merge`, {
        method: 'POST',
        body: JSON.stringify({ target_id: target }),
      }),
    onSuccess: () => onDone(),
    onError: (err) => setError(err instanceof Error ? err.message : 'failed'),
  });

  if (kind === 'document_class_property' && !parentClassId) {
    return (
      <div className="mt-3 text-xs text-amber-700">
        This property proposal is missing a `document_class_id` in its payload — edit it
        first to specify the parent class, then merge.
      </div>
    );
  }

  return (
    <div className="mt-3 p-3 bg-stone-50 border border-stone-200 rounded space-y-2">
      <div className="text-xs text-stone-600">
        Merge this proposal into an existing{' '}
        {KIND_LABELS[kind].toLowerCase().replace(/s$/, '')}
        {kind === 'document_class_property' ? ' on the same class' : ''}:
      </div>
      <select
        value={target}
        onChange={(e) => setTarget(e.target.value)}
        className={inputClasses}
      >
        <option value="">(pick one)</option>
        {(targets.data ?? []).map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
      <ErrorBanner message={error} />
      <PrimaryButton onClick={() => mergeMutation.mutate()} disabled={!target || mergeMutation.isPending}>
        {mergeMutation.isPending ? 'Merging…' : 'Merge'}
      </PrimaryButton>
    </div>
  );
}
