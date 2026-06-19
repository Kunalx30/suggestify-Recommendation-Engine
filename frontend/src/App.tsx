import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import type { ReactNode } from 'react';
import './index.css';
import AuthPage from './AuthPage';
import OnboardingPage from './OnboardingPage';
import HomePage from './HomePage';
import AdminPage from './AdminPage';

function ProtectedRoute({ children }: { children: ReactNode }) {
  const userId = localStorage.getItem('user_id');
  if (!userId) {
    return <Navigate to="/auth" replace />;
  }
  return children;
}

function GuestRoute({ children }: { children: ReactNode }) {
  // Redirect already-logged-in users away from /auth (BUG-05)
  const userId = localStorage.getItem('user_id');
  if (userId) {
    return <Navigate to="/home" replace />;
  }
  return children;
}

function AdminRoute({ children }: { children: ReactNode }) {
  const userId = localStorage.getItem('user_id');
  const username = localStorage.getItem('username');
  if (!userId) {
    return <Navigate to="/auth" replace />;
  }
  if (username?.toLowerCase() !== 'kunalx30') {
    return <Navigate to="/home" replace />;
  }
  return children;
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/auth" replace />} />
        <Route path="/auth" element={<GuestRoute><AuthPage /></GuestRoute>} />
        <Route
          path="/onboarding"
          element={
            <ProtectedRoute>
              <OnboardingPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/home"
          element={
            <ProtectedRoute>
              <HomePage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin"
          element={
            <AdminRoute>
              <AdminPage />
            </AdminRoute>
          }
        />
        <Route path="*" element={<Navigate to="/auth" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
