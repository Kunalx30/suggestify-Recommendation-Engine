import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { onboard as apiOnboard } from './api/client';

const GENRES = [
  'Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 
  'Documentary', 'Drama', 'Fantasy', 'Horror', 'Music', 
  'Mystery', 'Romance', 'Science Fiction', 'Thriller', 'War', 
  'Western', 'Family', 'History', 'Biography', 'Sport'
];

const CONTENT_TYPES = [
  { label: 'Movies', value: 'movie' },
  { label: 'TV Shows', value: 'tv' },
  { label: 'Anime', value: 'anime' },
  { label: 'Books', value: 'book' }
];

export default function OnboardingPage() {
  const [selectedGenres, setSelectedGenres] = useState<string[]>([]);
  const [selectedContentType, setSelectedContentType] = useState<string>('movie');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  // Auth Guard
  useEffect(() => {
    const userId = localStorage.getItem('user_id');
    if (!userId) {
      navigate('/auth');
    }
  }, [navigate]);

  const handleGenreClick = (genre: string) => {
    setSelectedGenres((prev) =>
      prev.includes(genre)
        ? prev.filter((g) => g !== genre)
        : [...prev, genre]
    );
  };

  const handleContinue = async () => {
    const userId = localStorage.getItem('user_id');
    if (!userId) {
      navigate('/auth');
      return;
    }

    if (selectedGenres.length < 3) {
      setError('Please select at least 3 genres');
      return;
    }

    setLoading(true);
    setError('');

    try {
      // onboard(user_id, genres, content_types, seed_ratings)
      await apiOnboard(userId, selectedGenres, [selectedContentType], []);
      navigate('/home');
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || 'Failed to save preferences. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-dark text-white flex flex-col items-center py-12 px-6">
      {/* Brand Logo */}
      <div className="absolute top-6 left-8 select-none">
        <h2 className="text-2xl font-bold text-netflix tracking-wider">
          SUGGESTIFY
        </h2>
      </div>

      <div className="max-w-4xl w-full flex flex-col items-center mt-12">
        <h1 className="text-3xl md:text-5xl font-bold mb-3 text-center">
          What do you like to watch?
        </h1>
        <p className="text-neutral-400 text-lg mb-8 text-center">
          Select at least 3 genres to personalize your recommendations.
        </p>

        {/* Selected Count Indicator */}
        <div className="mb-6 bg-neutral-900 border border-neutral-800 rounded-full px-6 py-2 text-sm font-semibold text-netflix shadow-inner">
          {selectedGenres.length} {selectedGenres.length === 1 ? 'genre' : 'genres'} selected
        </div>

        {/* Genres Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4 w-full mb-12">
          {GENRES.map((genre) => {
            const isSelected = selectedGenres.includes(genre);
            return (
              <button
                key={genre}
                type="button"
                onClick={() => handleGenreClick(genre)}
                className={`h-20 rounded-md border flex items-center justify-center font-bold px-3 text-center transition-all duration-300 transform hover:scale-105 active:scale-95 ${
                  isSelected
                    ? 'bg-netflix border-netflix text-white shadow-lg shadow-netflix/30'
                    : 'bg-card border-neutral-800 hover:border-neutral-600 text-neutral-300'
                }`}
              >
                {genre}
              </button>
            );
          })}
        </div>

        {/* Content Type Preference Row */}
        <div className="w-full flex flex-col items-center mb-10">
          <h3 className="text-xl font-semibold mb-4">Preferred Content Type</h3>
          <div className="flex flex-wrap gap-3 justify-center">
            {CONTENT_TYPES.map((type) => {
              const isSelected = selectedContentType === type.value;
              return (
                <button
                  key={type.value}
                  type="button"
                  onClick={() => setSelectedContentType(type.value)}
                  className={`px-6 py-3 rounded-full font-bold transition-all duration-300 ${
                    isSelected
                      ? 'bg-netflix text-white shadow-md'
                      : 'bg-neutral-800 hover:bg-neutral-700 text-neutral-400 hover:text-white'
                  }`}
                >
                  {type.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Error message */}
        {error && (
          <div className="text-netflix text-sm font-medium bg-red-950/20 border border-red-900/50 rounded px-6 py-3 mb-6 w-full max-w-md text-center">
            {error}
          </div>
        )}

        {/* Continue Button */}
        <button
          type="button"
          onClick={handleContinue}
          disabled={selectedGenres.length < 3 || loading}
          className="bg-netflix text-white font-extrabold text-xl px-12 py-4 rounded-md hover:bg-red-700 active:bg-red-800 transition-all duration-300 disabled:bg-neutral-800 disabled:text-neutral-600 disabled:cursor-not-allowed shadow-xl shadow-netflix/20 w-full max-w-sm"
        >
          {loading ? 'Saving...' : 'Continue'}
        </button>
      </div>
    </div>
  );
}
