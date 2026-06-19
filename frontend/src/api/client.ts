import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000';

const client = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000, // 15s timeout — fail fast instead of hanging
  headers: { Connection: 'keep-alive' },
});

export const getRecommendations = (user_id: string, content_type?: string) => {
  const params: Record<string, any> = { user_id };
  if (content_type) params.content_type = content_type;
  return client.get('/recommendations', { params });
};

export const getRows = (user_id: string, content_type?: string, cache_bust = false) => {
  const params: Record<string, any> = { user_id };
  if (content_type) params.content_type = content_type;
  if (cache_bust) params.cache_bust = true;  // BUG-08 fix: bypass Redis cache after user interaction
  return client.get('/recommendations/rows', { params });
};

export const searchItems = (q: string, content_type?: string) => {
  const params: Record<string, any> = { q };
  if (content_type) params.content_type = content_type;
  return client.get('/search', { params });
};

export const getTrending = (content_type?: string) => {
  const params: Record<string, any> = {};
  if (content_type) params.content_type = content_type;
  return client.get('/trending', { params });
};

export const getBooksByGenre = (genre: string, limit = 20) => {
  return client.get('/search', {
    params: { q: genre, content_type: 'book', limit },
  });
};

const TMDB_API_KEY = 'be06ccdec5d44b100a46f6cd22df2ea3';

export const fetchTMDBTrailer = async (imdbId: string): Promise<string | null> => {
  if (!imdbId) return null;
  try {
    // Resolve IMDb ID → TMDB item
    const findRes = await axios.get(
      `https://api.themoviedb.org/3/find/${imdbId}`,
      { params: { external_source: 'imdb_id', api_key: TMDB_API_KEY } }
    );
    const movieResults: any[] = findRes.data.movie_results || [];
    const tvResults: any[] = findRes.data.tv_results || [];
    const tmdbItem = movieResults[0] || tvResults[0];
    if (!tmdbItem) return null;

    const type = movieResults.length > 0 ? 'movie' : 'tv';
    const videosRes = await axios.get(
      `https://api.themoviedb.org/3/${type}/${tmdbItem.id}/videos`,
      { params: { api_key: TMDB_API_KEY } }
    );
    const trailer = (videosRes.data.results || []).find(
      (v: any) => v.type === 'Trailer' && v.site === 'YouTube'
    );
    return trailer?.key || null;
  } catch {
    return null;
  }
};

export const getUserRating = (userId: string, itemId: string) => {
  return client.get('/events/rating', { params: { user_id: userId, item_id: itemId } });
};

export const postEvent = (type: string, body: Record<string, any>) => {
  return client.post(`/events/${type}`, body);
};

export const register = (email: string, username: string, password: string) => {
  return client.post('/auth/register', { email, username, password });
};

export const login = (username: string, password: string) => {
  return client.post('/auth/login', { username, password });
};

export const onboard = (
  user_id: string,
  preferred_genres: string[],
  preferred_content_types: string[],
  seed_ratings: Array<{ item_id: string; rating: number }>
) => {
  return client.post('/auth/onboarding', {
    user_id,
    preferred_genres,
    preferred_content_types,
    seed_ratings,
  });
};
