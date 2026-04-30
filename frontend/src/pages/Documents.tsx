import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, tokens } from '@/lib/api';
import { PageHeader } from '@/components/PageHeader';

interface Document {
  id: string;
  filename: string;
  summary: string | null;
  document_class_id: string | null;
  classification_confidence: number | null;
}

export default function DocumentsPage() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUploaded, setLastUploaded] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ['documents'],
    queryFn: () => api<Document[]>('/documents'),
    refetchInterval: lastUploaded ? 3000 : false,
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append('file', file);
      // No Content-Type header — let the browser set the multipart boundary.
      const access = tokens.access;
      const res = await fetch('/api/v1/uploads', {
        method: 'POST',
        body: fd,
        headers: access ? { Authorization: `Bearer ${access}` } : {},
      });
      if (!res.ok) {
        throw new Error(`${res.status}: ${await res.text()}`);
      }
      return res.json();
    },
    onSuccess: (_, file) => {
      setLastUploaded(file.name);
      setError(null);
      void qc.invalidateQueries({ queryKey: ['documents'] });
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : 'upload failed');
      setLastUploaded(null);
    },
  });

  const onFiles = async (files: FileList | File[]) => {
    setBusy(true);
    setError(null);
    try {
      for (const f of Array.from(files)) {
        await upload.mutateAsync(f);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Documents"
        description="Drop files to ingest. Uploads land in the Sinas grove/documents collection and trigger ingestion automatically."
        actions={
          <button
            onClick={() => fileRef.current?.click()}
            className="px-3 py-1.5 rounded bg-forest-600 text-white text-sm hover:bg-forest-700"
          >
            Upload
          </button>
        }
      />
      <input
        ref={fileRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => e.target.files && onFiles(e.target.files)}
      />

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files.length) void onFiles(e.dataTransfer.files);
        }}
        className={`mb-6 px-6 py-10 text-center border-2 border-dashed rounded transition-colors ${
          dragOver
            ? 'border-forest-500 bg-forest-50'
            : 'border-stone-300 bg-white text-stone-500'
        }`}
      >
        {busy ? 'Uploading…' : 'Drop files here, or click Upload'}
      </div>

      {lastUploaded && (
        <div className="mb-4 text-sm text-forest-700 bg-forest-50 border border-forest-100 rounded px-3 py-2">
          Uploaded <span className="font-mono">{lastUploaded}</span>. Ingestion is running in the
          background — refresh in a few seconds to see it.
        </div>
      )}
      {error && (
        <div className="mb-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
          {error}
        </div>
      )}

      <div className="space-y-2">
        {(data ?? []).map((d) => (
          <Link
            key={d.id}
            to={`/documents/${d.id}`}
            className="block p-4 border border-stone-200 rounded bg-white hover:border-forest-500"
          >
            <div className="flex items-baseline justify-between">
              <div className="font-medium">{d.filename}</div>
              <div className="text-xs text-stone-400">
                {d.document_class_id ? (
                  d.classification_confidence != null ? (
                    <>conf {d.classification_confidence.toFixed(2)}</>
                  ) : (
                    'classified'
                  )
                ) : (
                  <span className="text-amber-600">awaiting classification</span>
                )}
              </div>
            </div>
            {d.summary && (
              <div className="text-sm text-stone-600 mt-1 line-clamp-2">{d.summary}</div>
            )}
          </Link>
        ))}
        {data && data.length === 0 && (
          <div className="text-stone-500 text-sm py-12 text-center border border-dashed border-stone-300 rounded">
            No documents yet. Upload some to get started.
          </div>
        )}
      </div>
    </div>
  );
}
