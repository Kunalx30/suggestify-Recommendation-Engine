import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { getRows, searchItems, postEvent, getBooksByGenre, fetchTMDBTrailer } from './api/client';
import ItemModal from './ItemModal';

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

// Map nav tab label → content_type query param (null = Home / all)
const CONTENT_TYPE_MAP: Record<string, string | null> = {
  Home: null,
  Movies: 'movie',
  'TV Shows': 'tv',
  Anime: 'anime',
  Books: 'book',
};

const TAB_LABELS = ['Home', 'Movies', 'TV Shows', 'Anime', 'Books'];

export default function HomePage() {
  const [activeTab, setActiveTab] = useState('Home');
  const [rows, setRows] = useState<Record<string, Item[]>>({});
  const [loading, setLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Item[]>([]);
  const [selectedItem, setSelectedItem] = useState<Item | null>(null);
  const [heroPlayError, setHeroPlayError] = useState('');
  const [bookRows, setBookRows] = useState<Record<string, Item[]>>({});
  const [bookRowsLoaded, setBookRowsLoaded] = useState(false);
  const [watchLaterOpen, setWatchLaterOpen] = useState(false);

  // Cache: keyed by `${tab}_${userId}`
  const rowsCache = useRef<Record<string, Record<string, Item[]>>>({});

  const navigate = useNavigate();
  const userId = localStorage.getItem('user_id') || '';
  const username = localStorage.getItem('username') || 'Guest';

  // Auth Guard
  useEffect(() => {
    if (!userId) navigate('/auth');
  }, [userId, navigate]);

  // ── Core data loader with caching ───────────────────────────────────────────
  const loadContent = async (tab: string, silent = false) => {
    const cacheKey = `${tab}_${userId}`;
    if (rowsCache.current[cacheKey]) {
      setRows(rowsCache.current[cacheKey]);
      return;
    }
    if (!silent) setLoading(true);
    const contentType = CONTENT_TYPE_MAP[tab];
    try {
      const res = await getRows(userId, contentType || undefined);
      const data: Record<string, Item[]> = res.data;
      rowsCache.current[cacheKey] = data;
      if (tab === activeTab) setRows(data);
    } catch (err) {
      console.error(`Failed to fetch rows for tab ${tab}:`, err);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  // Load initial tab
  useEffect(() => {
    if (!userId) return;
    loadContent(activeTab);
  }, [activeTab, userId]);

  // Prefetch all other tabs silently after 2 s
  useEffect(() => {
    if (!userId) return;
    const timer = setTimeout(() => {
      TAB_LABELS.filter(t => t !== activeTab).forEach(tab => {
        const key = `${tab}_${userId}`;
        if (!rowsCache.current[key]) loadContent(tab, true);
      });
    }, 2000);
    return () => clearTimeout(timer);
  }, [userId]); // run once on mount

  // Books genre rows — load when switching to Books tab
  useEffect(() => {
    if (activeTab !== 'Books' || bookRowsLoaded) return;
    Promise.all([
      getBooksByGenre('Fiction', 20),
      getBooksByGenre('Fantasy', 20),
      getBooksByGenre('Mystery', 20),
      getBooksByGenre('Romance', 20),
      getBooksByGenre('Science Fiction', 20),
    ]).then(([fiction, fantasy, mystery, romance, scifi]) => {
      setBookRows({
        'Fiction': (fiction as any)?.data ?? fiction,
        'Fantasy': (fantasy as any)?.data ?? fantasy,
        'Mystery & Thriller': (mystery as any)?.data ?? mystery,
        'Romance': (romance as any)?.data ?? romance,
        'Science Fiction': (scifi as any)?.data ?? scifi,
      });
      setBookRowsLoaded(true);
    }).catch(err => console.warn('Book genre rows failed:', err));
  }, [activeTab]);

  // Silently refresh recommendations (after modal interaction)
  const refreshRecommendations = async () => {
    if (!userId) return;
    try {
      const key = `${activeTab}_${userId}`;
      delete rowsCache.current[key]; // invalidate JS-side cache
      const contentType = CONTENT_TYPE_MAP[activeTab];
      // BUG-08 fix: pass cache_bust=true to skip the backend Redis cache
      const res = await getRows(userId, contentType || undefined, true);
      const data: Record<string, Item[]> = res.data;
      rowsCache.current[key] = data;
      setRows(data);
    } catch (err) {
      console.warn('Background refresh failed:', err);
    }
  };

  // Search debounce
  useEffect(() => {
    if (!searchQuery.trim()) { setSearchResults([]); return; }
    const t = setTimeout(async () => {
      try {
        const ct = CONTENT_TYPE_MAP[activeTab] || undefined;
        const res = await searchItems(searchQuery, ct);
        setSearchResults(res.data || []);
      } catch { /* silent */ }
    }, 300);
    return () => clearTimeout(t);
  }, [searchQuery, activeTab]);

  // ── Event handlers ───────────────────────────────────────────────────────────
  const handleCardClick = async (item: Item) => {
    try { await postEvent('click', { user_id: userId, item_id: item.id }); } catch { /* silent */ }
    setSelectedItem(item);
    // Background refresh: updates user signals and fetches fresh rows
    refreshRecommendations();
  };

  const handleSearchResultClick = async (item: Item) => {
    setSearchOpen(false); setSearchQuery(''); setSearchResults([]);
    await handleCardClick(item);
  };

  const handleHeroPlay = (item: Item) => {
    if (item.imdb_id) {
      window.open(`https://www.playimdb.com/title/${item.imdb_id}/`, '_blank');
    } else {
      setHeroPlayError(item.content_type === 'book' ? 'No preview available for this book' : 'Not available for this title');
      setTimeout(() => setHeroPlayError(''), 3000);
    }
  };

  // ── Hero item selection — content-type aware, image required ────────────────
  const getHeroItem = (): Item | null => {
    const hasImage = (item: Item) =>
      (item?.backdrop_url && item.backdrop_url.trim() !== '') ||
      (item?.poster_url && item.poster_url.trim() !== '');

    const personalized = rows['Personalized For You'] || [];
    const trending = rows['Trending Now'] || [];

    if (activeTab === 'Books') {
      return personalized.find(i => i.content_type === 'book' && hasImage(i))
        || trending.find(i => hasImage(i))
        || null;
    }
    if (activeTab === 'Movies') {
      return personalized.find(i => i.content_type === 'movie' && hasImage(i))
        || trending.find(i => i.content_type === 'movie' && hasImage(i))
        || trending.find(i => hasImage(i))
        || null;
    }
    if (activeTab === 'TV Shows') {
      return personalized.find(i => i.content_type === 'tv' && hasImage(i))
        || trending.find(i => hasImage(i))
        || null;
    }
    if (activeTab === 'Anime') {
      return personalized.find(i => i.content_type === 'anime' && hasImage(i))
        || trending.find(i => hasImage(i))
        || null;
    }
    // Home — prefer backdrop specifically for cinematic feel
    return personalized.find(i => i.backdrop_url && i.backdrop_url.trim() !== '')
      || trending.find(i => hasImage(i))
      || null;
  };
  const heroItem = getHeroItem();

  // ── Carousel row data ────────────────────────────────────────────────────────
  // Core rows shown on every tab (items are already filtered by content_type by backend)
  const CORE_ROW_TITLES = ['Trending Now', 'Personalized For You', 'Because You Liked X', 'Top Rated'];

  // Extra genre/category rows per tab (returned by backend only for that tab)
  const TAB_EXTRA_ROWS: Record<string, string[]> = {
    Movies: ['New Releases', 'Action & Adventure', 'Drama & Romance', 'Comedy & Family'],
    'TV Shows': ['New Series', 'Drama & Crime Series', 'Sci-Fi & Fantasy', 'Comedy & Animation'],
    Anime: ['Action & Shounen', 'Romance & Slice of Life', 'Fantasy & Isekai', 'Psychological & Mystery'],
    Books: [], // Books uses separate bookRows genre system
    Home: [],
  };

  // Build ordered list of row keys to render for the active tab
  const activeCarouselTitles = [
    ...CORE_ROW_TITLES,
    ...(TAB_EXTRA_ROWS[activeTab] || []),
  ];

  const isBooks = activeTab === 'Books';

  // Tab-specific background tints so each tab feels visually distinct
  const TAB_BG: Record<string, string> = {
    Home:      '#141414',
    Movies:    'linear-gradient(180deg, #0d1117 0%, #141414 500px)',
    'TV Shows':'linear-gradient(180deg, #0d1a14 0%, #141414 500px)',
    Anime:     'linear-gradient(180deg, #14091a 0%, #141414 500px)',
    Books:     'linear-gradient(180deg, #22150a 0%, #140d07 400px, #0e0905 100%)',
  };
  const bgStyle = TAB_BG[activeTab] ?? '#141414';

  return (
    <div className="min-h-screen text-white font-sans pb-16 selection:bg-red-600 selection:text-white"
         style={{ background: bgStyle }}>

      {/* ── Navbar ─────────────────────────────────────────────────────────── */}
      <nav className="fixed top-0 left-0 right-0 h-16 flex items-center justify-between px-6 md:px-12 z-40 transition-all duration-300"
           style={{ background: 'linear-gradient(to bottom, rgba(0,0,0,0.9) 0%, transparent 100%)' }}>
        <div className="flex items-center gap-8">
          <h1 onClick={() => setActiveTab('Home')}
              className="text-2xl md:text-3xl font-extrabold text-red-600 cursor-pointer tracking-wider select-none">
            SUGGESTIFY
          </h1>

          <div className="hidden md:flex items-center gap-6 text-sm font-semibold text-neutral-300">
            {TAB_LABELS.map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`hover:text-white transition-colors py-1 ${
                  activeTab === tab ? 'text-white font-bold border-b-2 border-red-600' : ''
                }`}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        {/* Search + Profile */}
        <div className="flex items-center gap-6 relative">
          <div className="flex items-center relative">
            <button onClick={() => { setSearchOpen(!searchOpen); setWatchLaterOpen(false); }}
                    className="text-xl text-neutral-300 hover:text-white focus:outline-none z-10">
              🔍
            </button>
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Titles, genres..."
              className={`bg-neutral-900 border border-neutral-700 text-white rounded px-3 py-1.5 ml-2 text-sm focus:outline-none transition-all duration-300 ${
                searchOpen ? 'w-48 md:w-64 opacity-100' : 'w-0 opacity-0 pointer-events-none'
              }`}
            />
            {searchOpen && searchQuery && (
              <div className="absolute top-12 right-0 bg-neutral-950/95 border border-neutral-800 rounded-md w-72 md:w-80 max-h-96 overflow-y-auto shadow-2xl backdrop-blur-md z-50">
                {searchResults.length > 0 ? searchResults.map(item => (
                  <div key={item.id} onClick={() => handleSearchResultClick(item)}
                       className="flex items-center gap-3 p-3 hover:bg-neutral-800/80 cursor-pointer border-b border-neutral-900 transition-colors">
                    <div className="w-10 h-14 bg-neutral-800 rounded overflow-hidden flex-shrink-0 relative">
                      <img src={item.poster_url || ''} alt={item.title}
                           className="w-full h-full object-cover"
                           onError={e => { e.currentTarget.style.display = 'none'; const fb = e.currentTarget.nextSibling as HTMLDivElement; if (fb) fb.style.display = 'flex'; }}
                           style={{ display: item.poster_url ? 'block' : 'none' }} />
                      <div className="absolute inset-0 flex items-center justify-center text-[8px] text-center font-bold px-1 bg-neutral-800"
                           style={{ display: item.poster_url ? 'none' : 'flex' }}>
                        {item.title.substring(0, 15)}
                      </div>
                    </div>
                    <div className="flex-1 min-w-0">
                      <h4 className="text-sm font-semibold truncate text-white">{item.title}</h4>
                      <div className="flex items-center gap-2 text-xs text-neutral-400 mt-1">
                        <span className="uppercase">{item.content_type}</span>
                        {item.release_year && <span>• {item.release_year}</span>}
                        {item.match_score && <span className="text-green-500 font-bold ml-1">{item.match_score}% Match</span>}
                      </div>
                    </div>
                  </div>
                )) : (
                  <div className="p-4 text-center text-sm text-neutral-500">No results found</div>
                )}
              </div>
            )}
          </div>

          {/* Watch Later nav button & dropdown */}
          <div className="relative">
            <button onClick={() => { setWatchLaterOpen(!watchLaterOpen); setSearchOpen(false); }}
                    className="text-neutral-300 hover:text-white focus:outline-none flex items-center gap-1.5 font-semibold text-sm py-1.5 px-3 rounded hover:bg-neutral-800/50 transition-all duration-200 border border-transparent hover:border-neutral-700/50"
                    title="Watch Later list">
              🕒 <span className="hidden md:inline">Watch Later</span>
            </button>
            {watchLaterOpen && (
              <div className="absolute top-12 right-0 bg-neutral-950/98 border border-neutral-800 rounded-md w-72 md:w-80 max-h-[450px] overflow-y-auto shadow-2xl backdrop-blur-md z-50 animate-fade-in divide-y divide-neutral-900">
                <div className="p-3 bg-neutral-950/60 sticky top-0 backdrop-blur flex justify-between items-center z-10 border-b border-neutral-900">
                  <span className="font-bold text-sm text-neutral-200 flex items-center gap-2">🕒 Watch Later</span>
                  <button onClick={() => setWatchLaterOpen(false)} className="text-xs text-neutral-500 hover:text-neutral-300">Close</button>
                </div>
                {rows['Watch Later'] && rows['Watch Later'].length > 0 ? (
                  rows['Watch Later'].map(item => (
                    <div key={item.id} onClick={() => { setWatchLaterOpen(false); handleCardClick(item); }}
                         className="flex items-center gap-3 p-3 hover:bg-neutral-800/60 cursor-pointer transition-colors">
                      <div className="w-10 h-14 bg-neutral-900 rounded overflow-hidden flex-shrink-0 relative border border-neutral-800 shadow">
                        <img src={item.poster_url || ''} alt={item.title}
                             className="w-full h-full object-cover"
                             onError={e => { e.currentTarget.style.display = 'none'; const fb = e.currentTarget.nextSibling as HTMLDivElement; if (fb) fb.style.display = 'flex'; }}
                             style={{ display: item.poster_url ? 'block' : 'none' }} />
                        <div className="absolute inset-0 flex items-center justify-center text-[8px] text-center font-bold px-1 bg-neutral-900 text-neutral-400"
                             style={{ display: item.poster_url ? 'none' : 'flex' }}>
                          {item.title.substring(0, 15)}
                        </div>
                      </div>
                      <div className="flex-1 min-w-0">
                        <h4 className="text-sm font-semibold truncate text-white hover:text-red-500 transition-colors">{item.title}</h4>
                        <div className="flex items-center gap-2 text-xs text-neutral-400 mt-1">
                          <span className="uppercase font-bold text-[9px] px-1 bg-neutral-800 rounded text-neutral-300">{item.content_type}</span>
                          {item.release_year && <span>• {item.release_year}</span>}
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="p-6 text-center text-sm text-neutral-500 flex flex-col items-center justify-center gap-2">
                    <span className="text-2xl">📋</span>
                    <span>No items in Watch Later list</span>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="flex items-center gap-3 select-none">
            {username?.toLowerCase() === 'kunalx30' && (
              <a href="/admin" className="text-gray-400 hover:text-white text-sm mr-2 flex items-center gap-1">
                ⚙️ Admin
              </a>
            )}
            <div className="w-8 h-8 rounded bg-red-600 flex items-center justify-center font-bold text-sm shadow">
              {username[0].toUpperCase()}
            </div>
            <span className="hidden sm:inline text-sm font-semibold text-neutral-300 truncate max-w-[100px]">{username}</span>
            <button onClick={() => { localStorage.clear(); navigate('/auth'); }}
                    className="text-xs text-neutral-500 hover:text-white transition-colors">
              Sign Out
            </button>
          </div>
        </div>
      </nav>

      {/* ── Hero Banner ─────────────────────────────────────────────────────── */}
      {loading && !heroItem ? (
        <div className="h-[550px] w-full bg-neutral-900 flex items-center justify-center">
          <div className="text-center">
            <div className="w-12 h-12 border-4 border-red-600 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <p className="text-neutral-400 text-sm">Loading...</p>
          </div>
        </div>
      ) : heroItem ? (
        <HeroBanner
          item={heroItem}
          onPlay={handleHeroPlay}
          onMoreInfo={setSelectedItem}
          heroPlayError={heroPlayError}
        />
      ) : (
        <div className="h-[250px] w-full bg-neutral-900 flex items-center justify-center">
          <h2 className="text-xl font-bold text-neutral-500 animate-pulse">Loading Hero Banner...</h2>
        </div>
      )}

      {/* ── Carousels ───────────────────────────────────────────────────────── */}
      <div className="px-6 md:px-12 space-y-12 -mt-12 relative z-20">
        {/* Standard + tab-specific rows */}
        {activeCarouselTitles.map(title => {
          const items = rows[title] || [];
          if (items.length === 0) return null;
          return (
            <CarouselRow key={title} title={title} items={items} onCardClick={handleCardClick} isBookRow={isBooks} />
          );
        })}

        {/* Books-only genre rows */}
        {isBooks && Object.entries(bookRows).map(([genre, items]) =>
          items && items.length > 0 ? (
            <CarouselRow key={genre} title={`📚 ${genre}`} items={items} onCardClick={handleCardClick} isBookRow={isBooks} />
          ) : null
        )}
      </div>

      {/* ── Item Modal ──────────────────────────────────────────────────────── */}
      {selectedItem && (
        <ItemModal
          item={selectedItem}
          onClose={() => setSelectedItem(null)}
          onRefreshRecommendations={refreshRecommendations}
          isInitiallySaved={rows['Watch Later']?.some(w => w.id === selectedItem.id) ?? false}
        />
      )}
    </div>
  );
}

// ── HeroBanner ─────────────────────────────────────────────────────────────────
interface HeroBannerProps {
  item: Item;
  onPlay: (item: Item) => void;
  onMoreInfo: (item: Item) => void;
  heroPlayError: string;
}

function HeroBanner({ item, onPlay, onMoreInfo, heroPlayError }: HeroBannerProps) {
  const [showTrailer, setShowTrailer] = useState(false);
  const [isMuted, setIsMuted] = useState(true);
  const [trailerKey, setTrailerKey] = useState<string | null>(null);

  const getYouTubeKey = (url?: string): string | null => {
    if (!url) return null;
    const match = url.match(/(?:v=|youtu\.be\/|embed\/)([a-zA-Z0-9_-]{11})/);
    if (match) return match[1];
    if (/^[a-zA-Z0-9_-]{11}$/.test(url)) return url;
    return null;
  };

  // Resolve trailer: stored URL first, then TMDB lookup via imdb_id
  useEffect(() => {
    setTrailerKey(null);
    setShowTrailer(false);
    if (!item) return;

    // 1. Check stored trailer_url
    const storedKey = getYouTubeKey(item.trailer_url);
    if (storedKey) {
      setTrailerKey(storedKey);
      const t = setTimeout(() => setShowTrailer(true), 3000);
      return () => clearTimeout(t);
    }

    // 2. Dynamically fetch from TMDB if item has an imdb_id
    if (item.imdb_id) {
      let cancelled = false;
      fetchTMDBTrailer(item.imdb_id).then(key => {
        if (!cancelled && key) {
          setTrailerKey(key);
          setTimeout(() => { if (!cancelled) setShowTrailer(true); }, 3000);
        }
      });
      return () => { cancelled = true; };
    }
  }, [item.id]);

  // Re-load iframe when mute state changes
  const iframeSrc = trailerKey
    ? `https://www.youtube.com/embed/${trailerKey}?autoplay=1&mute=${isMuted ? 1 : 0}&controls=0&loop=1&playlist=${trailerKey}&showinfo=0&rel=0&modestbranding=1&iv_load_policy=3`
    : '';

  return (
    <div className="relative w-full h-[550px] overflow-hidden">
      {/* Background */}
      {showTrailer && trailerKey ? (
        <iframe
          key={`${trailerKey}-${isMuted}`}
          src={iframeSrc}
          className="absolute w-[177.78vh] h-[100vh] min-w-full min-h-full top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2"
          allow="autoplay; encrypted-media"
          frameBorder="0"
          style={{ pointerEvents: 'none' }}
        />
      ) : (
        <img
          src={item.backdrop_url || item.poster_url || ''}
          alt={item.title}
          className="w-full h-full object-cover object-top"
          fetchPriority="high"
          decoding="async"
          onError={e => { (e.target as HTMLImageElement).style.display = 'none'; }}
        />
      )}

      {/* Gradient overlays */}
      <div className="absolute inset-0" style={{ background: 'linear-gradient(to right, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.5) 40%, transparent 70%)' }} />
      <div className="absolute inset-0" style={{ background: 'linear-gradient(to top, #141414 0%, transparent 50%)' }} />

      {/* Mute / unmute button — Netflix style, bottom right */}
      {showTrailer && trailerKey && (
        <button
          onClick={() => setIsMuted(m => !m)}
          className="absolute bottom-24 right-8 border-2 border-gray-400 text-white rounded-full w-10 h-10 flex items-center justify-center hover:border-white z-10 transition-colors bg-black/30"
          title={isMuted ? 'Unmute' : 'Mute'}
        >
          {isMuted ? '🔇' : '🔊'}
        </button>
      )}

      {/* Hero content — bottom left */}
      <div className="absolute bottom-16 left-12 max-w-lg z-10">
        <h1 className="text-5xl font-black text-white mb-3 drop-shadow-lg leading-tight">
          {item.title}
        </h1>

        <div className="flex items-center gap-2 mb-3 flex-wrap">
          {item.match_score && (
            <span className="bg-green-500 text-white text-sm font-bold px-2 py-1 rounded">
              {Math.round(item.match_score)}% Match
            </span>
          )}
          <span className="border border-gray-400 text-white text-xs px-2 py-1 uppercase">
            {item.content_type}
          </span>
          {item.release_year && (
            <span className="text-gray-300 text-sm">{item.release_year}</span>
          )}
        </div>

        {item.genres && item.genres.length > 0 && (
          <div className="flex gap-2 mb-4 flex-wrap">
            {item.genres.slice(0, 3).map(g => (
              <span key={g} className="border border-gray-500 text-gray-300 text-xs px-2 py-1 rounded">{g}</span>
            ))}
          </div>
        )}

        {item.description && (
          <p className="text-neutral-300 text-sm line-clamp-2 leading-relaxed mb-4 drop-shadow-md">
            {item.description}
          </p>
        )}

        <div className="flex gap-3">
          <button
            onClick={() => onPlay(item)}
            className={`font-bold px-8 py-3 rounded flex items-center gap-2 transition-colors ${
              item.content_type === 'book'
                ? 'bg-[#d4af37] text-black hover:bg-[#c5a028]'
                : 'bg-white text-black hover:bg-gray-200'
            }`}
          >
            {item.content_type === 'book' ? '📖 Read' : '▶ Play'}
          </button>
          <button
            onClick={() => onMoreInfo(item)}
            className="bg-gray-600 bg-opacity-70 text-white font-bold px-6 py-3 rounded flex items-center gap-2 hover:bg-opacity-90 transition-colors"
          >
            ⓘ More Info
          </button>
        </div>

        {heroPlayError && (
          <div className="mt-3 bg-red-950 border border-red-900 text-red-200 text-xs px-3 py-1.5 rounded shadow-lg inline-block">
            {heroPlayError}
          </div>
        )}
      </div>
    </div>
  );
}

// ── CardItem ───────────────────────────────────────────────────────────────────
interface CardItemProps {
  item: Item;
  onCardClick: (item: Item) => void;
}

function CardItem({ item, onCardClick }: CardItemProps) {
  const [imgError, setImgError] = useState(false);
  const hasImage = item.poster_url && !imgError;

  const contentEmoji = ({ movie: '🎬', tv: '📺', anime: '⛩️', book: '📚' } as Record<string, string>)[item.content_type] ?? '🎬';
  const isBook = item.content_type === 'book';
  const placeholderGradient = isBook
    ? 'linear-gradient(135deg, #1a0f05 0%, #2d1a08 50%, #3d2410 100%)'
    : 'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)';
  const bookBorder = isBook ? 'border-amber-900/40 hover:border-amber-600/60' : 'border-neutral-800';
  const cardRounding = isBook ? 'rounded-[3px]' : 'rounded-md';
  const cardShadow = isBook 
    ? 'shadow-[3px_5px_15px_rgba(0,0,0,0.5),-1px_2px_5px_rgba(0,0,0,0.3)]' 
    : 'shadow-lg';

  return (
    <div
      onClick={() => onCardClick(item)}
      className={`w-[160px] h-[240px] ${cardRounding} overflow-hidden relative flex-shrink-0 cursor-pointer ${cardShadow} transition-all duration-300 transform hover:scale-105 hover:z-20 border ${bookBorder} group/card`}
    >
      {hasImage ? (
        <img src={item.poster_url} onError={() => setImgError(true)} alt={item.title} className="w-full h-full object-cover" loading="lazy" decoding="async" />
      ) : isBook ? (
        <div className="w-full h-full flex flex-col justify-between p-4 relative font-serif select-none"
             style={{
               background: 'linear-gradient(135deg, #2c1a0c 0%, #1a0f05 50%, #0d0702 100%)',
               border: '4px double #d4af37',
               boxShadow: 'inset 0 0 20px rgba(0,0,0,0.6)'
             }}>
          {/* Top library tag */}
          <div className="flex flex-col items-center">
            <div className="text-[#d4af37] text-[8px] uppercase tracking-widest font-semibold border-b border-[#d4af37]/30 pb-0.5 w-full text-center leading-none">
              SUGGESTIFY
            </div>
            <span className="text-[#d4af37] text-xs mt-1 opacity-80 leading-none">📖</span>
          </div>
          
          {/* Title */}
          <div className="text-center my-auto px-1 flex flex-col justify-center h-full">
            <p className="text-[#f2e6d9] text-[11px] font-bold leading-snug line-clamp-4 tracking-wide font-serif">
              {item.title}
            </p>
          </div>
          
          {/* Bottom tag */}
          <div className="text-center">
            <p className="text-[#d4af37] text-[8px] uppercase tracking-widest opacity-90 font-medium">
              CLASSIC
            </p>
          </div>
        </div>
      ) : (
        <div className="w-full h-full flex flex-col items-center justify-center p-3 select-none" style={{ background: placeholderGradient }}>
          <div className="text-4xl mb-2">{contentEmoji}</div>
          <p className="text-white text-xs text-center font-medium leading-tight line-clamp-4">{item.title}</p>
          <p className="text-gray-400 text-[9px] mt-1 uppercase tracking-wider">{item.content_type}</p>
        </div>
      )}

      {/* Book spine crease shadow overlay */}
      {isBook && (
        <>
          <div className="absolute inset-y-0 left-0 w-3 bg-gradient-to-r from-black/55 via-black/25 to-transparent pointer-events-none z-10" />
          <div className="absolute inset-y-0 left-[10px] w-[1.5px] bg-white/10 pointer-events-none z-10" />
          <div className="absolute inset-y-0 left-[11px] w-[1px] bg-black/30 pointer-events-none z-10" />
        </>
      )}

      {/* Match Score Badge */}
      {item.match_score && (
        <div className="absolute top-2 left-2 bg-green-500 text-white text-xs font-bold px-1.5 py-0.5 rounded z-10 shadow-md">
          {Math.round(item.match_score)}%
        </div>
      )}

      {/* Content type badge on hover */}
      <div className="absolute top-2 right-2 opacity-0 group-hover/card:opacity-100 transition-opacity duration-300">
        <span className="bg-black/60 text-white text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider">
          {item.content_type}
        </span>
      </div>

      {/* Title overlay on hover */}
      <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black via-black/80 to-transparent p-3 pt-8 transform translate-y-full group-hover/card:translate-y-0 transition-transform duration-300">
        <h4 className="text-xs font-bold text-white truncate">{item.title}</h4>
        <p className="text-[10px] text-neutral-400 mt-1">{item.release_year || ''}</p>
      </div>
    </div>
  );
}

// ── CarouselRow ────────────────────────────────────────────────────────────────
interface CarouselRowProps {
  title: string;
  items: Item[];
  onCardClick: (item: Item) => void;
  isBookRow?: boolean;
}

function CarouselRow({ title, items, onCardClick, isBookRow }: CarouselRowProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const showArrows = items.length > 5;

  const handleScroll = (direction: 'left' | 'right') => {
    if (containerRef.current) {
      containerRef.current.scrollBy({ left: direction === 'left' ? -400 : 400, behavior: 'smooth' });
    }
  };

  return (
    <div className="space-y-3 relative group">
      <h3 className={`text-lg md:text-xl font-bold tracking-wide text-neutral-100 px-1 ${isBookRow ? 'font-serif text-[#f3e3d3] drop-shadow-md' : ''}`}>
        {title}
      </h3>
      <div className="relative">
        {showArrows && (
          <button onClick={() => handleScroll('left')}
                  className="absolute left-0 top-0 bottom-0 w-12 bg-black/50 hover:bg-black/80 text-white font-extrabold flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity z-10 text-2xl border-r border-neutral-900 rounded-l">
            ‹
          </button>
        )}
        <div ref={containerRef} className="flex overflow-x-auto gap-4 py-4 px-1 scroll-smooth scrollbar-none">
          {items.map(item => (
            <CardItem key={item.id} item={item} onCardClick={onCardClick} />
          ))}
        </div>
        {showArrows && (
          <button onClick={() => handleScroll('right')}
                  className="absolute right-0 top-0 bottom-0 w-12 bg-black/50 hover:bg-black/80 text-white font-extrabold flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity z-10 text-2xl border-l border-neutral-900 rounded-r">
            ›
          </button>
        )}
        {isBookRow && (
          <div className="h-3.5 bg-gradient-to-r from-[#2c1d11] via-[#4d321d] to-[#2c1d11] rounded-b shadow-[0_8px_16px_rgba(0,0,0,0.6)] border-t border-[#66462c] relative z-10 w-[calc(100%-8px)] mx-1 mt-[-16px] pointer-events-none" />
        )}
      </div>
    </div>
  );
}
