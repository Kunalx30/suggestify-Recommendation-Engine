import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { login as apiLogin, register as apiRegister, onboard as apiOnboard } from './api/client';

export default function AuthPage() {
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      if (isLogin) {
        const res = await apiLogin(username, password);
        const { access_token, user_id, username: dbUsername } = res.data;
        localStorage.setItem('token', access_token);
        localStorage.setItem('user_id', user_id);
        localStorage.setItem('username', dbUsername ?? username);
        navigate('/home');
      } else {
        if (!email) {
          setError('Email is required');
          setLoading(false);
          return;
        }
        // Register user
        const regRes = await apiRegister(email, username, password);
        const userId = regRes.data.user_id;

        // Auto login after registration
        const logRes = await apiLogin(username, password);
        const { access_token } = logRes.data;

        localStorage.setItem('token', access_token);
        localStorage.setItem('user_id', userId);
        localStorage.setItem('username', username);

        // Call onboarding API with defaults
        try {
          await apiOnboard(userId, [], [], []);
        } catch (onbErr) {
          console.warn('Initial onboarding call failed (non-fatal):', onbErr);
        }

        navigate('/onboarding');
      }
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || 'An error occurred. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div 
      className="min-h-screen flex flex-col items-center justify-center bg-dark px-4"
      style={{
        backgroundImage: 'radial-gradient(circle, rgba(20,20,20,0.8) 0%, rgba(10,10,10,1) 100%)'
      }}
    >
      {/* Header / Logo */}
      <div className="mb-8 select-none">
        <h1 className="text-4xl md:text-5xl font-extrabold text-netflix tracking-wider font-sans">
          SUGGESTIFY
        </h1>
      </div>

      {/* Card Container */}
      <div className="w-full max-w-md bg-black/80 border border-neutral-800 rounded-lg p-8 md:p-10 shadow-2xl backdrop-blur-md">
        {/* Tabs */}
        <div className="flex border-b border-neutral-800 mb-8">
          <button
            type="button"
            className={`flex-1 pb-3 text-lg font-semibold transition-all duration-300 ${
              isLogin ? 'text-netflix border-b-2 border-netflix font-bold' : 'text-neutral-500 hover:text-neutral-300'
            }`}
            onClick={() => {
              setIsLogin(true);
              setError('');
              setEmail(''); setUsername(''); setPassword('');
            }}
          >
            Login
          </button>
          <button
            type="button"
            className={`flex-1 pb-3 text-lg font-semibold transition-all duration-300 ${
              !isLogin ? 'text-netflix border-b-2 border-netflix font-bold' : 'text-neutral-500 hover:text-neutral-300'
            }`}
            onClick={() => {
              setIsLogin(false);
              setError('');
              setEmail(''); setUsername(''); setPassword('');
            }}
          >
            Sign Up
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-6">
          {!isLogin && (
            <div>
              <label className="block text-sm font-medium text-neutral-400 mb-2">Email Address</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@example.com"
                className="w-full bg-neutral-900 border border-neutral-700 text-white rounded px-4 py-3 focus:outline-none focus:border-netflix transition-colors"
                required
              />
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-neutral-400 mb-2">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="username"
              className="w-full bg-neutral-900 border border-neutral-700 text-white rounded px-4 py-3 focus:outline-none focus:border-netflix transition-colors"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-neutral-400 mb-2">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full bg-neutral-900 border border-neutral-700 text-white rounded px-4 py-3 focus:outline-none focus:border-netflix transition-colors"
              required
            />
          </div>

          {error && (
            <div className="text-netflix text-sm font-medium bg-red-950/20 border border-red-900/50 rounded p-3">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-netflix text-white font-bold rounded py-3 hover:bg-red-700 active:bg-red-800 transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed text-lg"
          >
            {loading ? 'Processing...' : isLogin ? 'Sign In' : 'Sign Up'}
          </button>
        </form>

        <p className="mt-8 text-sm text-neutral-500 text-center">
          {isLogin ? "New to Suggestify? " : "Already have an account? "}
          <span 
            className="text-white hover:underline cursor-pointer font-medium"
            onClick={() => {
              setIsLogin(!isLogin);
              setError('');
            }}
          >
            {isLogin ? "Sign up now" : "Sign in here"}
          </span>
        </p>
      </div>
    </div>
  );
}
