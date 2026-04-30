import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { useAuth } from './lib/auth';
import LoginPage from './pages/Login';
import DocumentClassesPage from './pages/DocumentClasses';
import EntityTypesPage from './pages/EntityTypes';
import RelationshipsPage from './pages/Relationships';
import DossierClassesPage from './pages/DossierClasses';
import PlaybooksPage from './pages/Playbooks';
import DocumentsPage from './pages/Documents';
import DocumentDetailPage from './pages/DocumentDetail';
import ResultsPage from './pages/Results';
import AnswersPage from './pages/Answers';
import ProposalsPage from './pages/Proposals';
import SinasStatusPage from './pages/SinasStatus';

export default function App() {
  const { status } = useAuth();

  if (status === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center text-stone-500">
        Loading…
      </div>
    );
  }
  if (status === 'unauthenticated') {
    return <LoginPage />;
  }

  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/documents" replace />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="documents/:id" element={<DocumentDetailPage />} />
        <Route path="results" element={<ResultsPage />} />
        <Route path="answers" element={<AnswersPage />} />
        <Route path="config/document-classes" element={<DocumentClassesPage />} />
        <Route path="config/entity-types" element={<EntityTypesPage />} />
        <Route path="config/relationships" element={<RelationshipsPage />} />
        <Route path="config/dossier-classes" element={<DossierClassesPage />} />
        <Route path="config/playbooks" element={<PlaybooksPage />} />
        <Route path="review/proposals" element={<ProposalsPage />} />
        <Route path="sinas-status" element={<SinasStatusPage />} />
      </Route>
    </Routes>
  );
}
