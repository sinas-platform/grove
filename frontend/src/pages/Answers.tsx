import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { PageHeader } from '@/components/PageHeader';

interface Answer {
  id: string;
  question: string;
  status: string;
  source_result_id: string | null;
  source_dossier_id: string | null;
  published_at: string | null;
}

export default function AnswersPage() {
  const { data } = useQuery({
    queryKey: ['answers'],
    queryFn: () => api<Answer[]>('/answers'),
  });

  return (
    <div>
      <PageHeader
        title="Answers"
        description="Synthesized answers with claim-bound evidence."
      />
      <div className="space-y-2">
        {(data ?? []).map((a) => (
          <div key={a.id} className="p-4 border border-stone-200 rounded bg-white">
            <div className="flex items-baseline justify-between">
              <div className="font-medium">{a.question}</div>
              <div
                className={`text-xs px-2 py-0.5 rounded ${
                  a.status === 'published'
                    ? 'bg-forest-100 text-forest-700'
                    : 'bg-stone-200 text-stone-600'
                }`}
              >
                {a.status}
              </div>
            </div>
            <div className="text-xs text-stone-500 mt-1">
              {a.source_dossier_id ? 'dossier-driven' : 'result-driven'}
            </div>
          </div>
        ))}
        {data && data.length === 0 && (
          <div className="text-stone-500 text-sm py-12 text-center border border-dashed border-stone-300 rounded">
            No answers yet.
          </div>
        )}
      </div>
    </div>
  );
}
