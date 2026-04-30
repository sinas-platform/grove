import { useState } from 'react';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  ErrorBanner,
  Field,
  PrimaryButton,
  SecondaryButton,
  inputClasses,
} from '@/components/Form';

interface LoginResponse {
  message: string;
  session_id: string;
}

interface VerifyResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

export default function LoginPage() {
  const { setSession } = useAuth();
  const [step, setStep] = useState<'email' | 'otp'>('email');
  const [email, setEmail] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [otp, setOtp] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sendCode = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await api<LoginResponse>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email: email.trim() }),
      });
      setSessionId(res.session_id);
      setStep('otp');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'login failed');
    } finally {
      setSubmitting(false);
    }
  };

  const verify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!sessionId) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api<VerifyResponse>('/auth/verify-otp', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId, otp_code: otp.trim() }),
      });
      await setSession(res.access_token, res.refresh_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'invalid code');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-stone-50 px-4">
      <div className="w-full max-w-md p-8 bg-white border border-stone-200 rounded">
        <div className="text-xl font-semibold text-forest-700 mb-1">Sinas Grove</div>
        <div className="text-sm text-stone-500 mb-6">
          {step === 'email'
            ? 'Sign in with your Sinas account.'
            : `We sent a code to ${email}.`}
        </div>

        {step === 'email' && (
          <form onSubmit={sendCode}>
            <Field label="Email">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
                required
                placeholder="you@example.com"
                className={inputClasses}
              />
            </Field>
            <ErrorBanner message={error} />
            <div className="mt-4">
              <PrimaryButton type="submit" disabled={submitting || !email.trim()}>
                {submitting ? 'Sending code…' : 'Send code'}
              </PrimaryButton>
            </div>
          </form>
        )}

        {step === 'otp' && (
          <form onSubmit={verify}>
            <Field label="Verification code">
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                autoFocus
                required
                placeholder="123456"
                className={`${inputClasses} font-mono tracking-widest text-lg`}
              />
            </Field>
            <ErrorBanner message={error} />
            <div className="mt-4 flex gap-2">
              <PrimaryButton type="submit" disabled={submitting || !otp.trim()}>
                {submitting ? 'Verifying…' : 'Verify'}
              </PrimaryButton>
              <SecondaryButton
                onClick={() => {
                  setStep('email');
                  setOtp('');
                  setError(null);
                }}
              >
                Back
              </SecondaryButton>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
