import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { PageHeader } from '@/components/PageHeader';

interface Proposal {
  id: string;
  relationship_definition_id: string;
  source_id: string;
  target_id: string;
  proposing_agent: string | null;
  reasoning: string | null;
  confidence: number | null;
  status: string;
}

export default function ProposalsPage() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ['proposals', 'pending'],
    queryFn: () => api<Proposal[]>('/relationships/proposals?status_filter=pending'),
  });

  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      api(`/relationships/proposals/${id}/decision`, {
        method: 'POST',
        body: JSON.stringify({ approve }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proposals'] }),
  });

  return (
    <div>
      <PageHeader
        title="Relationship proposals"
        description="Low-confidence relationships proposed by extraction or discovery — approve to promote to facts."
      />
      <div className="space-y-2">
        {(data ?? []).map((p) => (
          <div key={p.id} className="p-4 border border-stone-200 rounded bg-white">
            <div className="flex items-baseline justify-between mb-2">
              <div className="text-sm">
                <span className="font-mono text-stone-500">{p.source_id.slice(0, 8)}</span>
                <span className="mx-2">→</span>
                <span className="font-mono text-stone-500">{p.target_id.slice(0, 8)}</span>
              </div>
              <div className="text-xs text-stone-400">
                {p.proposing_agent}
                {p.confidence != null && ` · ${p.confidence.toFixed(2)}`}
              </div>
            </div>
            {p.reasoning && <div className="text-sm text-stone-600 mb-3">{p.reasoning}</div>}
            <div className="flex gap-2">
              <button
                onClick={() => decide.mutate({ id: p.id, approve: true })}
                className="px-3 py-1 rounded text-sm bg-forest-600 text-white hover:bg-forest-700"
              >
                Approve
              </button>
              <button
                onClick={() => decide.mutate({ id: p.id, approve: false })}
                className="px-3 py-1 rounded text-sm border border-stone-300 hover:bg-stone-100"
              >
                Reject
              </button>
            </div>
          </div>
        ))}
        {data && data.length === 0 && (
          <div className="text-stone-500 text-sm py-12 text-center border border-dashed border-stone-300 rounded">
            No pending proposals.
          </div>
        )}
      </div>
    </div>
  );
}
