import { useState, useEffect } from 'react';
import { postEvent, getUserRating } from './api/client';

interface Item {
  id: string;
  title: string;
  content_type: string;
  genres: string[];
  description?: string;
  release_year?: number;
  rating?: number;
  vote_count?: number;
  poster_url?: string;
  backdrop_url?: string;
  imdb_id?: string;
  trailer_url?: string;
  match_score?: number;
}

interface ItemModalProps {
  item: Item;
  onClose: () => void;
  onRefreshRecommendations: () => void;
  isInitiallySaved: boolean;
}

export default function ItemModal({ item, onClose, onRefreshRecommendations, isInitiallySaved }: ItemModalProps) {
  const [isSaved, setIsSaved] = useState(isInitiallySaved);
  const [userRating, setUserRating] = useState<number | null>(null);
  const [showFullDesc, setShowFullDesc] = useState(false);
  const [playError, setPlayError] = useState('');
  const [imgError, setImgError] = useState(false);
  const [showTrailer, setShowTrailer] = useState(false);

  const userId = localStorage.getItem('user_id') || '';

  useEffect(() => {
    if (userId && item.id) {
      getUserRating(userId, item.id)
        .then(res => {
          if (res.data && res.data.rating !== undefined) {
            setUserRating(res.data.rating);
          }
        })
        .catch(err => console.warn('Failed to fetch user rating:', err));
    }
  }, [userId, item.id]);

  const getYouTubeKey = (url?: string) => {
    if (!url) return null;
    const match = url.match(/(?:v=|youtu\.be\/|embed\/)([a-zA-Z0-9_-]{11})/);
    return match ? match[1] : (url.length === 11 ? url : null);
  };

  const trailerKey = getYouTubeKey(item.trailer_url);

  const handlePlay = () => {
    if (trailerKey) {
      setShowTrailer(true);
    } else if (item.imdb_id) {
      window.open(`https://www.playimdb.com/title/${item.imdb_id}/`, '_blank');
    } else {
      setPlayError(isBook ? 'No preview available for this book' : 'No video available for this title');
      setTimeout(() => setPlayError(''), 3000);
    }
  };

  const handleWatchLater = async () => {
    try {
      await postEvent('watch_later', { user_id: userId, item_id: item.id });
      setIsSaved(true);
      onRefreshRecommendations();
    } catch (err) {
      console.error('Failed to save watch later event:', err);
    }
  };

  const handleRate = async (ratingVal: number) => {
    try {
      await postEvent('rate', {
        user_id: userId,
        item_id: item.id,
        rating: ratingVal,
        genres: item.genres || []
      });
      setUserRating(ratingVal);
      onRefreshRecommendations();
    } catch (err) {
      console.error('Failed to save rate event:', err);
    }
  };

  const ratingStars = Math.round((item.rating || 0) / 2);

  // Build a rich fallback description when none is provided
  const hasMeaningfulDesc = item.description && item.description.trim().length > 0;
  const fallbackDesc = `${item.content_type === 'book' ? 'A book' : item.content_type === 'anime' ? 'An anime series' : item.content_type === 'tv' ? 'A TV series' : 'A title'} from ${
    item.release_year || 'unknown year'
  }. Genres: ${(item.genres || []).join(', ') || 'Not specified'}. Rating: ${
    item.rating ? `${item.rating.toFixed(1)}/10` : 'Not rated'
  }.`;
  const rawDesc = hasMeaningfulDesc
    ? (item.description as string)
    : fallbackDesc;
  const displayDesc = showFullDesc || rawDesc.length <= 300
    ? rawDesc
    : `${rawDesc.slice(0, 300)}...`;

  const isBook = item.content_type === 'book';
  const modalBg = isBook 
    ? { background: 'linear-gradient(135deg, #1d140e 0%, #130c08 100%)' } 
    : undefined;
  const modalBorder = isBook ? 'border-[#4a2f1b]' : 'border-neutral-800';

  return (
    <div className="fixed inset-0 bg-black/75 flex items-center justify-center z-50 p-4 backdrop-blur-sm">
      {/* Modal Card */}
      <div 
        className={`w-full max-w-2xl rounded-lg overflow-hidden relative border shadow-2xl animate-fade-in ${modalBorder}`}
        style={modalBg}
      >
        
        {/* Close button */}
        <button 
          onClick={onClose}
          className="absolute top-4 right-4 bg-black/60 text-white rounded-full p-2 hover:bg-black/80 transition-colors z-10 w-10 h-10 flex items-center justify-center font-bold text-xl"
        >
          ✕
        </button>

        {/* Top: Trailer iframe or Backdrop image */}
        <div className="relative h-64 md:h-80 bg-neutral-900 flex items-center justify-center">
          {showTrailer && trailerKey ? (
            <>
              <iframe
                className="w-full h-full"
                src={`https://www.youtube.com/embed/${trailerKey}?autoplay=1&controls=1`}
                allow="autoplay; encrypted-media"
                allowFullScreen
              />
              <button
                onClick={() => setShowTrailer(false)}
                className="absolute top-2 right-2 bg-black/80 text-white px-2.5 py-1 rounded text-xs font-bold hover:bg-black transition-colors z-10"
              >
                ✕ Close Trailer
              </button>
            </>
          ) : (
            <>
              {!imgError && (item.backdrop_url || item.poster_url) ? (
                <img
                  src={item.backdrop_url || item.poster_url}
                  onError={() => setImgError(true)}
                  alt={item.title}
                  className="w-full h-full object-cover"
                />
              ) : isBook ? (
                <div className="w-full h-full flex flex-col justify-between p-8 relative font-serif select-none"
                     style={{
                       background: 'linear-gradient(135deg, #2c1a0c 0%, #1a0f05 50%, #0d0702 100%)',
                       border: '8px double #d4af37',
                       boxShadow: 'inset 0 0 30px rgba(0,0,0,0.7)'
                     }}>
                  <div className="flex flex-col items-center">
                    <div className="text-[#d4af37] text-xs uppercase tracking-widest font-semibold border-b border-[#d4af37]/30 pb-2 w-full text-center">
                      SUGGESTIFY LIBRARY
                    </div>
                    <span className="text-[#d4af37] text-2xl mt-4">📖</span>
                  </div>
                  <div className="text-center my-auto px-4">
                    <h3 className="text-[#f2e6d9] text-2xl font-bold leading-tight tracking-wide font-serif mb-2">
                      {item.title}
                    </h3>
                  </div>
                  <div className="text-center">
                    <p className="text-[#d4af37] text-xs uppercase tracking-widest font-medium">
                      CLASSIC EDITION
                    </p>
                  </div>
                </div>
              ) : (
                <div className="text-center p-6">
                  <h3 className="text-2xl font-bold">{item.title}</h3>
                </div>
              )}
              {/* Spine crease for books */}
              {isBook && (
                <>
                  <div className="absolute inset-y-0 left-0 w-4 bg-gradient-to-r from-black/60 via-black/25 to-transparent pointer-events-none z-10" />
                  <div className="absolute inset-y-0 left-[15px] w-[1.5px] bg-white/10 pointer-events-none z-10" />
                  <div className="absolute inset-y-0 left-[16px] w-[1px] bg-black/30 pointer-events-none z-10" />
                </>
              )}
              {/* Bottom Gradient overlay */}
              <div 
                className="absolute inset-0 bg-gradient-to-t to-transparent" 
                style={{
                  backgroundImage: isBook 
                    ? 'linear-gradient(to top, #1d140e 0%, rgba(29, 20, 14, 0.4) 40%, transparent 100%)' 
                    : 'linear-gradient(to top, #1a1a1a 0%, rgba(26, 26, 26, 0.4) 40%, transparent 100%)'
                }}
              />
              {/* Title overlay */}
              <div className="absolute bottom-4 left-6 right-6">
                <h2 className={`text-2xl md:text-4xl font-extrabold text-white drop-shadow-md ${isBook ? 'font-serif text-[#f2e6d9]' : ''}`}>
                  {item.title}
                </h2>
              </div>
            </>
          )}
        </div>

        {/* Details Section */}
        <div className="p-6 md:p-8 space-y-6">
          <div className="flex flex-wrap items-center gap-3 text-sm md:text-base">
            {/* Match score */}
            {item.match_score && (
              <span className="text-green-500 font-bold">
                {Math.round(item.match_score)}% Match
              </span>
            )}
            
            {/* Release Year */}
            {item.release_year ? (
              <span className="text-neutral-400">{item.release_year}</span>
            ) : null}

            {/* Content Type Badge */}
            <span className="px-2 py-0.5 bg-neutral-800 text-neutral-300 rounded text-xs uppercase font-bold">
              {item.content_type}
            </span>

            {/* Rating Star Indicator */}
            <div className="flex items-center text-yellow-500">
              {Array.from({ length: 5 }).map((_, idx) => (
                <span key={idx} className="text-lg">
                  {idx < ratingStars ? '★' : '☆'}
                </span>
              ))}
              <span className="ml-1 text-xs text-neutral-400">
                ({item.rating ? item.rating.toFixed(1) : '0.0'})
              </span>
            </div>
          </div>

          {/* Genres Badges */}
          {item.genres && item.genres.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {item.genres.map((g) => (
                <span key={g} className="px-3 py-1 bg-neutral-800/80 text-neutral-300 text-xs rounded-full border border-neutral-700">
                  {g}
                </span>
              ))}
            </div>
          )}

          {/* Description */}
          <div className="leading-relaxed text-sm md:text-base">
            <p className={`leading-relaxed ${isBook ? 'font-serif text-[15px] leading-relaxed text-[#e5d4c0] bg-[#1a110a] p-4 rounded border border-amber-950/20 shadow-inner' : 'text-gray-300 text-sm'}`}>
              {displayDesc}
            </p>
            {rawDesc.length > 300 && (
              <button
                onClick={() => setShowFullDesc(!showFullDesc)}
                className={`${isBook ? 'text-[#d4af37] hover:text-[#f2e6d9]' : 'text-netflix'} hover:underline font-bold mt-2 text-xs focus:outline-none block`}
              >
                {showFullDesc ? 'Show Less' : 'Show More'}
              </button>
            )}
          </div>

          {/* Action Buttons Row */}
          <div className="flex flex-wrap items-center gap-4 pt-4 border-t border-neutral-800">
            {/* Play Button */}
            <div className="relative">
              <button
                onClick={handlePlay}
                className={`px-6 py-2.5 rounded font-extrabold transition-colors flex items-center gap-2 ${
                  isBook 
                    ? 'bg-[#d4af37] text-black hover:bg-[#c5a028] active:bg-[#b69119]' 
                    : 'bg-netflix text-white hover:bg-red-700 active:bg-red-800'
                }`}
              >
                {isBook ? '📖 Read' : '▶ Play'}
              </button>
              {playError && (
                <div className="absolute top-12 left-0 bg-red-950 border border-red-900 text-red-200 text-xs px-3 py-1.5 rounded shadow-lg whitespace-nowrap z-20">
                  {playError}
                </div>
              )}
            </div>

            {/* Watch Later Button */}
            <button
              onClick={handleWatchLater}
              disabled={isSaved}
              className={`px-5 py-2.5 rounded font-bold border transition-colors ${
                isSaved 
                  ? 'bg-neutral-800 border-neutral-800 text-green-500 cursor-default'
                  : 'bg-transparent border-neutral-600 hover:border-white text-white'
              }`}
            >
              {isSaved ? '✓ Saved' : '+ Watch Later'}
            </button>

            {/* Interactive Rating Component */}
            <div className="flex items-center gap-2 bg-neutral-900 border border-neutral-800 px-4 py-2 rounded">
              <span className="text-xs text-neutral-400 font-semibold uppercase">
                {userRating ? 'Your Rating:' : 'Rate Now:'}
              </span>
              <div className="flex items-center gap-1">
                {Array.from({ length: 5 }).map((_, idx) => {
                  const val = idx + 1;
                  const active = userRating !== null ? val <= userRating : false;
                  return (
                    <button
                      key={idx}
                      onClick={() => handleRate(val)}
                      className={`text-xl hover:scale-125 transition-transform ${
                        active ? 'text-yellow-500' : 'text-neutral-600 hover:text-yellow-500'
                      }`}
                    >
                      ★
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
