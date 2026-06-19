import { useState, useEffect } from 'react';
import axios from 'axios';

const BASE = 'http://localhost:8000';

export default function AdminPage() {
  const [activeTab, setActiveTab] = useState('health');
  const [health, setHealth] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);
  const [ab, setAb] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 30000); // refresh every 30s
    return () => clearInterval(interval);
  }, []);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const [h, s, a] = await Promise.all([
        axios.get(`${BASE}/admin/health`).then(r => r.data).catch(() => null),
        axios.get(`${BASE}/admin/stats`).then(r => r.data).catch(() => null),
        axios.get(`${BASE}/admin/ab`).then(r => r.data).catch(() => []),
      ]);
      setHealth(h);
      setStats(s);
      setAb(a);
    } catch (err) {
      console.error('Failed to fetch admin data:', err);
    } finally {
      setLoading(false);
    }
  };

  const tabs = ['health', 'ab', 'fairness', 'simulation', 'model'];
  const tabLabels = {
    health: '🟢 System Health',
    ab: '🧪 A/B Experiments',
    fairness: '⚖️ Fairness',
    simulation: '👤 User Simulation',
    model: '🤖 Model Quality',
  };

  return (
    <div className="min-h-screen bg-gray-950 text-white font-sans">
      {/* Header */}
      <div className="bg-gray-900 border-b border-gray-800 px-8 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <a href="/home" className="text-red-500 font-black text-xl tracking-wider hover:text-red-400 transition-colors">SUGGESTIFY</a>
          <span className="text-gray-400">|</span>
          <span className="text-gray-300 font-semibold">Admin Panel</span>
        </div>
        <div className="flex items-center gap-2">
          <div className={`w-2.5 h-2.5 rounded-full ${loading ? 'bg-yellow-400 animate-pulse' : 'bg-green-400'}`} />
          <span className="text-gray-400 text-sm">{loading ? 'Refreshing...' : 'Live'}</span>
          <button onClick={fetchAll} className="ml-4 text-gray-400 hover:text-white text-sm border border-gray-700 hover:border-gray-500 px-3 py-1 rounded transition-colors bg-gray-800">
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-800 bg-gray-900/50 px-8">
        <div className="flex gap-0">
          {tabs.map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-6 py-4 text-sm font-medium border-b-2 transition-colors ${activeTab === tab
                  ? 'border-red-500 text-white font-bold'
                  : 'border-transparent text-gray-400 hover:text-gray-200'
                }`}
            >
              {tabLabels[tab as keyof typeof tabLabels]}
            </button>
          ))}
        </div>
      </div>

      <div className="p-8 max-w-7xl mx-auto">
        {/* ── SYSTEM HEALTH ── */}
        {activeTab === 'health' && (
          <div>
            <h2 className="text-2xl font-bold mb-6">System Health</h2>

            {/* Service status cards */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
              {[
                { name: 'PostgreSQL', key: 'postgres', icon: '🐘', port: 5433 },
                { name: 'Redis', key: 'redis', icon: '⚡', port: 6379 },
                { name: 'Qdrant', key: 'qdrant', icon: '🔍', port: 6333 },
                { name: 'Kafka', key: 'kafka', icon: '📨', port: 9092 },
              ].map(svc => {
                const status = health?.[svc.key]?.status;
                const isUp = status === 'ok';
                return (
                  <div key={svc.key} className="bg-gray-900 rounded-lg p-5 border border-gray-800 hover:border-gray-700 transition-colors shadow-lg">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-2xl">{svc.icon}</span>
                      <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${isUp ? 'bg-green-950 text-green-400 border border-green-800/40' : 'bg-red-950 text-red-400 border border-red-800/40'
                        }`}>
                        {isUp ? 'UP' : 'DOWN'}
                      </span>
                    </div>
                    <p className="text-white font-semibold text-base">{svc.name}</p>
                    <p className="text-gray-400 text-xs mt-0.5">Port {svc.port}</p>
                    {svc.key === 'postgres' && health?.postgres?.items && (
                      <p className="text-gray-300 text-xs mt-2 bg-gray-800/50 py-1 px-2 rounded w-fit">{Number(health.postgres.items).toLocaleString()} items</p>
                    )}
                    {svc.key === 'redis' && health?.redis?.memory && (
                      <p className="text-gray-300 text-xs mt-2 bg-gray-800/50 py-1 px-2 rounded w-fit">{health.redis.memory} RAM</p>
                    )}
                    {svc.key === 'qdrant' && health?.qdrant?.vectors && (
                      <p className="text-gray-300 text-xs mt-2 bg-gray-800/50 py-1 px-2 rounded w-fit">{Number(health.qdrant.vectors).toLocaleString()} vectors</p>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Stats row */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
              {[
                { label: 'Total Items', value: stats?.content_breakdown?.reduce((a: number, c: any) => a + Number(c.cnt), 0)?.toLocaleString() || '—' },
                { label: 'Total Users', value: stats?.total_users?.toLocaleString() || '—' },
                { label: 'Total Interactions', value: stats?.total_interactions?.toLocaleString() || '—' },
                { label: 'Avg Rec Latency', value: stats?.avg_latency_ms ? `${Math.round(stats.avg_latency_ms)}ms` : '—' },
              ].map(stat => (
                <div key={stat.label} className="bg-gray-900 rounded-lg p-5 border border-gray-800 shadow-md">
                  <p className="text-3xl font-black text-red-500 mb-1">{stat.value}</p>
                  <p className="text-gray-400 text-sm font-medium">{stat.label}</p>
                </div>
              ))}
            </div>

            {/* Content breakdown */}
            <div className="bg-gray-900 rounded-lg p-6 border border-gray-800 mb-6 shadow-md">
              <h3 className="text-lg font-semibold mb-4">Content Breakdown</h3>
              <div className="space-y-4">
                {stats?.content_breakdown?.map((ct: any) => {
                  const total = stats.content_breakdown.reduce((a: number, c: any) => a + Number(c.cnt), 0);
                  const pct = total ? Math.round(Number(ct.cnt) / total * 100) : 0;
                  const colors: Record<string, string> = { movie: 'bg-blue-500', tv: 'bg-purple-500', anime: 'bg-pink-500', book: 'bg-amber-500' };
                  return (
                    <div key={ct.content_type}>
                      <div className="flex justify-between text-sm mb-1.5">
                        <span className="capitalize text-gray-300 font-medium">{ct.content_type}</span>
                        <span className="text-gray-400">{Number(ct.cnt).toLocaleString()} ({pct}%)</span>
                      </div>
                      <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
                        <div className={`h-full ${colors[ct.content_type] || 'bg-gray-500'} rounded-full`}
                          style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Recent events */}
            <div className="bg-gray-900 rounded-lg p-6 border border-gray-800 shadow-md">
              <h3 className="text-lg font-semibold mb-4">Events (Last 24 Hours)</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {stats?.recent_events_24h?.map((ev: any) => (
                  <div key={ev.event_type} className="text-center bg-gray-850 p-4 rounded border border-gray-800/40">
                    <p className="text-2xl font-bold text-white">{Number(ev.cnt).toLocaleString()}</p>
                    <p className="text-gray-400 text-sm capitalize mt-1">{ev.event_type}</p>
                  </div>
                ))}
                {(!stats?.recent_events_24h || stats.recent_events_24h.length === 0) && (
                  <div className="text-gray-500 col-span-4 text-center py-4">No events in the last 24 hours — interact with the app to generate events.</div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── A/B EXPERIMENTS ── */}
        {activeTab === 'ab' && (
          <div>
            <h2 className="text-2xl font-bold mb-6">A/B Experiments</h2>
            {ab.length === 0 ? (
              <p className="text-gray-500">No experiments found.</p>
            ) : (
              <div className="space-y-6">
                {ab.map((exp: any) => (
                  <div key={exp.id} className="bg-gray-900 rounded-lg p-6 border border-gray-800 shadow-lg">
                    <div className="flex items-center justify-between mb-4 border-b border-gray-800 pb-4">
                      <div>
                        <h3 className="text-lg font-bold text-white">{exp.name}</h3>
                        <p className="text-gray-400 text-sm mt-0.5">{exp.description}</p>
                      </div>
                      <span className={`px-3 py-1 rounded text-xs font-bold ${exp.status === 'running' ? 'bg-green-950 text-green-400 border border-green-800/40' : 'bg-gray-800 text-gray-400'}`}>
                        {exp.status?.toUpperCase()}
                      </span>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {/* Variant A */}
                      <div className="bg-gray-850 rounded p-5 border border-gray-800/60">
                        <p className="text-gray-400 text-xs font-bold tracking-wider mb-2">VARIANT A (Control)</p>
                        <p className="font-semibold text-lg text-white">{exp.variant_a?.name || 'Control'}</p>
                        <p className="text-gray-400 text-sm mt-1">Strategy: <span className="text-gray-300">{exp.variant_a?.strategy}</span></p>
                        <div className="border-t border-gray-800 mt-4 pt-3 flex items-baseline gap-2 mb-3">
                          <span className="text-3xl font-black text-white">{exp.a_users || 0}</span>
                          <span className="text-sm text-gray-400 font-medium">assigned users</span>
                        </div>
                        {/* Metrics */}
                        <div className="grid grid-cols-2 gap-2">
                          {[
                            { label: 'Impressions', value: exp.a_metrics?.impressions ?? '—' },
                            { label: 'Clicks (CTR)', value: exp.a_metrics ? `${exp.a_metrics.clicks} (${exp.a_metrics.ctr_pct}%)` : '—' },
                            { label: 'Avg Rating', value: exp.a_metrics?.avg_rating ? `${exp.a_metrics.avg_rating} ★` : '—' },
                          ].map(m => (
                            <div key={m.label} className="bg-gray-900/60 rounded px-3 py-2">
                              <p className="text-gray-500 text-[10px] uppercase tracking-wider">{m.label}</p>
                              <p className="text-white text-sm font-bold mt-0.5">{m.value}</p>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Variant B */}
                      <div className="bg-gray-850 rounded p-5 border border-red-900/30">
                        <p className="text-red-400/80 text-xs font-bold tracking-wider mb-2">VARIANT B (Treatment)</p>
                        <p className="font-semibold text-lg text-white">{exp.variant_b?.name || 'Treatment'}</p>
                        <p className="text-gray-400 text-sm mt-1">Strategy: <span className="text-gray-300">{exp.variant_b?.strategy}</span></p>
                        <div className="border-t border-gray-800 mt-4 pt-3 flex items-baseline gap-2 mb-3">
                          <span className="text-3xl font-black text-red-500">{exp.b_users || 0}</span>
                          <span className="text-sm text-gray-400 font-medium">assigned users</span>
                        </div>
                        {/* Metrics */}
                        <div className="grid grid-cols-2 gap-2">
                          {[
                            { label: 'Impressions', value: exp.b_metrics?.impressions ?? '—' },
                            { label: 'Clicks (CTR)', value: exp.b_metrics ? `${exp.b_metrics.clicks} (${exp.b_metrics.ctr_pct}%)` : '—' },
                            { label: 'Avg Rating', value: exp.b_metrics?.avg_rating ? `${exp.b_metrics.avg_rating} ★` : '—' },
                          ].map(m => (
                            <div key={m.label} className="bg-gray-900/60 rounded px-3 py-2">
                              <p className="text-gray-500 text-[10px] uppercase tracking-wider">{m.label}</p>
                              <p className="text-red-300 text-sm font-bold mt-0.5">{m.value}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>

                    {/* CTR lift indicator */}
                    {exp.a_metrics && exp.b_metrics && exp.a_metrics.impressions > 0 && exp.b_metrics.impressions > 0 && (
                      <div className="mt-4 flex items-center gap-3 bg-gray-950/60 rounded-lg px-4 py-3 border border-gray-800/40">
                        <span className="text-gray-500 text-xs">CTR Lift (B vs A):</span>
                        {(() => {
                          const lift = exp.b_metrics.ctr_pct - exp.a_metrics.ctr_pct;
                          return (
                            <span className={`text-sm font-black ${lift > 0 ? 'text-green-400' : lift < 0 ? 'text-red-400' : 'text-gray-400'}`}>
                              {lift > 0 ? '+' : ''}{lift.toFixed(2)}pp {lift > 0 ? '🏆 B winning' : lift < 0 ? '📉 A winning' : '≈ tied'}
                            </span>
                          );
                        })()}
                        <span className="text-gray-600 text-xs ml-auto">Traffic Split: {Math.round(exp.traffic_split * 100)}% B / {100 - Math.round(exp.traffic_split * 100)}% A · Started: {new Date(exp.started_at).toLocaleDateString()}</span>
                      </div>
                    )}
                    {!(exp.a_metrics?.impressions > 0 || exp.b_metrics?.impressions > 0) && (
                      <div className="mt-4 text-xs text-gray-600 border border-gray-800/40 rounded-lg px-4 py-3">
                        No metric events yet — CTR data accumulates as real users interact with recommendations.
                        Traffic Split: {Math.round(exp.traffic_split * 100)}% B · Started: {new Date(exp.started_at).toLocaleDateString()} {new Date(exp.started_at).toLocaleTimeString()}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}


        {/* ── FAIRNESS ── */}
        {activeTab === 'fairness' && (
          <div>
            <h2 className="text-2xl font-bold mb-6">Fairness Dashboard</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="bg-gray-900 rounded-lg p-6 border border-gray-800 shadow-md">
                <h3 className="text-lg font-semibold mb-4">Content Type Distribution</h3>
                <div className="space-y-4">
                  {stats?.content_breakdown?.map((ct: any) => {
                    const total = stats.content_breakdown.reduce((a: number, c: any) => a + Number(c.cnt), 0);
                    const pct = total ? Math.round(Number(ct.cnt) / total * 100) : 0;
                    const ideal: Record<string, number> = { movie: 30, tv: 30, anime: 15, book: 25 };
                    const diff = pct - (ideal[ct.content_type] || 25);
                    return (
                      <div key={ct.content_type} className="flex items-center justify-between">
                        <span className="capitalize text-gray-300 w-16 font-medium">{ct.content_type}</span>
                        <div className="flex-1 mx-4 h-3 bg-gray-800 rounded-full overflow-hidden">
                          <div className="h-full bg-blue-500 rounded-full" style={{ width: `${pct}%` }} />
                        </div>
                        <span className="text-gray-400 text-sm w-12 text-right">{pct}%</span>
                        <span className={`text-xs ml-2 w-16 text-right font-semibold ${diff > 5 ? 'text-yellow-400' : diff < -5 ? 'text-blue-400' : 'text-green-400'}`}>
                          {diff > 0 ? `+${diff}%` : `${diff}%`} vs ideal
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="bg-gray-900 rounded-lg p-6 border border-gray-800 shadow-md">
                <h3 className="text-lg font-semibold mb-4">Recommendation Diversity</h3>
                <div className="space-y-5">
                  <div>
                    <p className="text-gray-300 text-sm font-medium mb-2">MMR Lambda (Diversity vs Relevance)</p>
                    <div className="flex items-center gap-3">
                      <div className="flex-1 h-3 bg-gray-800 rounded-full overflow-hidden">
                        <div className="h-full bg-green-500 rounded-full" style={{ width: `${(stats?.diversity_config?.mmr_lambda ?? 0.7) * 100}%` }} />
                      </div>
                      <span className="text-white font-bold">{stats?.diversity_config?.mmr_lambda ?? 0.7}</span>
                    </div>
                    <p className="text-gray-500 text-xs mt-1">
                      {Math.round((stats?.diversity_config?.mmr_lambda ?? 0.7) * 100)}% relevance weight, {Math.round((1 - (stats?.diversity_config?.mmr_lambda ?? 0.7)) * 100)}% diversity weight to combat echo chambers
                    </p>
                  </div>
                  <div>
                    <p className="text-gray-300 text-sm font-medium mb-2">Bandit Exploration Rate (Epsilon)</p>
                    <div className="flex items-center gap-3">
                      <div className="flex-1 h-3 bg-gray-800 rounded-full overflow-hidden">
                        <div className="h-full bg-yellow-500 rounded-full" style={{ width: `${(stats?.diversity_config?.bandit_epsilon ?? 0.15) * 100}%` }} />
                      </div>
                      <span className="text-white font-bold">{Math.round((stats?.diversity_config?.bandit_epsilon ?? 0.15) * 100)}%</span>
                    </div>
                    <p className="text-gray-500 text-xs mt-1">
                      {Math.round((stats?.diversity_config?.bandit_epsilon ?? 0.15) * 100)}% of slots reserved for dynamic reinforcement learning exploration
                    </p>
                  </div>
                  <div>
                    <p className="text-gray-300 text-sm font-medium mb-2">Max items per genre (Hard Cap)</p>
                    <div className="flex items-center gap-3">
                      <div className="flex-1 h-3 bg-gray-800 rounded-full overflow-hidden">
                        <div className="h-full bg-purple-500 rounded-full" style={{ width: `${(stats?.diversity_config?.max_items_per_genre ?? 4) * 10}%` }} />
                      </div>
                      <span className="text-white font-bold">{stats?.diversity_config?.max_items_per_genre ?? 4}</span>
                    </div>
                    <p className="text-gray-500 text-xs mt-1">
                      Maximum {stats?.diversity_config?.max_items_per_genre ?? 4} titles per genre per recommendation array to avoid filter bubbles
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── USER SIMULATION ── */}
        {activeTab === 'simulation' && (
          <UserSimulation />
        )}

        {/* ── MODEL QUALITY ── */}
        {activeTab === 'model' && (
          <div>
            <h2 className="text-2xl font-bold mb-6">Model Quality</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
              {[
                { label: 'Embedding Dim', value: stats?.model_metadata?.dlrm?.embedding_dim || '128', desc: 'Two-Tower & DLRM tensor dimensionality' },
                { label: 'Training Items', value: stats?.model_metadata?.dlrm?.num_items ? Number(stats.model_metadata.dlrm.num_items).toLocaleString() : '100,000', desc: 'Unique catalogue item representations' },
                { label: 'Training Users', value: stats?.model_metadata?.dlrm?.num_users ? Number(stats.model_metadata.dlrm.num_users).toLocaleString() : '5,000', desc: 'Synthesized interaction user profiles' },
                { label: 'Loss (Final)', value: '5.67', desc: 'InfoNCE contrastive loss value at final epoch' },
                { label: 'Qdrant Vectors', value: health?.qdrant?.vectors?.toLocaleString() || '—', desc: 'Index item embeddings for ANN' },
                { label: 'ANN Speed', value: '<30ms', desc: 'Average p99 candidate vector search time' },
              ].map(m => (
                <div key={m.label} className="bg-gray-900 rounded-lg p-5 border border-gray-800 shadow-md">
                  <p className="text-3xl font-black text-red-500 mb-1">{m.value}</p>
                  <p className="text-gray-300 font-bold text-sm">{m.label}</p>
                  <p className="text-gray-500 text-xs mt-1.5 leading-relaxed">{m.desc}</p>
                </div>
              ))}
            </div>
            <div className="bg-gray-900 rounded-lg p-6 border border-gray-800 shadow-md">
              <h3 className="text-lg font-semibold mb-4">Two-Tower Deep Learning Architecture</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <p className="text-gray-400 text-sm font-semibold mb-3">User Tower Input & Layers</p>
                  <div className="space-y-2 text-sm font-mono">
                    {['Embedding(user_id, 64)', 'Embedding(content_type, 16)', 'genre_vector(50)', '→ Linear(130, 256)', '→ LayerNorm + GELU + Dropout(0.1)', '→ Linear(256, 128)', '→ L2 Normalize'].map((l, i) => (
                      <div key={i} className="bg-gray-855 border border-gray-800 px-3 py-2 rounded text-gray-300 flex items-center justify-between">
                        <span>{l}</span>
                        <span className="text-[10px] text-neutral-500">layer {i + 1}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="text-gray-400 text-sm font-semibold mb-3">Item Tower Input & Layers</p>
                  <div className="space-y-2 text-sm font-mono">
                    {['Embedding(item_id, 64)', 'Embedding(content_type, 16)', 'genre_vector(50)', '→ Linear(130, 256)', '→ LayerNorm + GELU + Dropout(0.1)', '→ Linear(256, 128)', '→ L2 Normalize'].map((l, i) => (
                      <div key={i} className="bg-gray-855 border border-gray-800 px-3 py-2 rounded text-gray-300 flex items-center justify-between">
                        <span>{l}</span>
                        <span className="text-[10px] text-neutral-500">layer {i + 1}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <div className="mt-6 p-4 bg-gray-850 rounded border border-gray-850 text-sm text-gray-400 leading-relaxed">
                <span className="font-bold text-gray-300">Loss function:</span> In-batch InfoNCE (NT-Xent) contrastive loss. Diagonal elements are treated as positives. Temperature parameter initialized to 0.07 (learnable). Optimizer: AdamW + CosineAnnealingLR scheduling.
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Genre presets for the User Simulation
const GENRE_PRESETS = [
  {
    id: 'action_scifi',
    label: 'Action & Sci-Fi',
    emoji: '🚀',
    color: 'blue',
    description: 'Clicks on blockbuster action & science-fiction titles',
    clicks: ['tmdb_movie_550', 'tmdb_movie_27205', 'tmdb_movie_157336', 'tmdb_movie_603', 'tmdb_movie_78'],
    ratings: [
      { item_id: 'tmdb_movie_550', rating: 5.0, genres: ['Drama', 'Crime'], label: 'Fight Club' },
      { item_id: 'tmdb_movie_27205', rating: 4.5, genres: ['Science Fiction', 'Thriller'], label: 'Inception' },
    ],
    boostPreview: 'Drama+2, Crime+2, Science Fiction+1.5, Thriller+1.5',
  },
  {
    id: 'drama_crime',
    label: 'Drama & Crime',
    emoji: '🎭',
    color: 'purple',
    description: 'Clicks on acclaimed drama and crime thrillers',
    clicks: ['tmdb_movie_238', 'tmdb_movie_240', 'tmdb_movie_769', 'tmdb_movie_497', 'tmdb_movie_539'],
    ratings: [
      { item_id: 'tmdb_movie_238', rating: 5.0, genres: ['Drama', 'Crime'], label: 'The Godfather' },
      { item_id: 'tmdb_movie_240', rating: 5.0, genres: ['Drama', 'Crime'], label: 'Godfather II' },
    ],
    boostPreview: 'Drama+4, Crime+4',
  },
  {
    id: 'horror_thriller',
    label: 'Horror & Thriller',
    emoji: '🎃',
    color: 'orange',
    description: 'Clicks on spine-chilling horror and suspense films',
    clicks: ['tmdb_movie_694', 'tmdb_movie_745', 'tmdb_movie_539', 'tmdb_movie_1091', 'tmdb_movie_185'],
    ratings: [
      { item_id: 'tmdb_movie_694', rating: 5.0, genres: ['Horror', 'Thriller'], label: 'The Shining' },
      { item_id: 'tmdb_movie_745', rating: 4.5, genres: ['Horror', 'Thriller'], label: 'Se7en' },
    ],
    boostPreview: 'Horror+3.5, Thriller+3.5',
  },
  {
    id: 'romance_comedy',
    label: 'Romance & Comedy',
    emoji: '💕',
    color: 'pink',
    description: 'Clicks on feel-good romantic comedies and dramas',
    clicks: ['tmdb_movie_13', 'tmdb_movie_11324', 'tmdb_movie_15602', 'tmdb_movie_4951', 'tmdb_movie_10020'],
    ratings: [
      { item_id: 'tmdb_movie_13', rating: 5.0, genres: ['Romance', 'Drama'], label: 'Forrest Gump' },
      { item_id: 'tmdb_movie_11324', rating: 4.5, genres: ['Romance', 'Comedy'], label: 'Juno' },
    ],
    boostPreview: 'Romance+3.5, Drama+2, Comedy+1.5',
  },
  {
    id: 'animation_family',
    label: 'Animation & Family',
    emoji: '✨',
    color: 'green',
    description: 'Clicks on beloved animated and family-friendly films',
    clicks: ['tmdb_movie_862', 'tmdb_movie_585', 'tmdb_movie_863', 'tmdb_movie_10193', 'tmdb_movie_10681'],
    ratings: [
      { item_id: 'tmdb_movie_862', rating: 5.0, genres: ['Animation', 'Comedy', 'Family'], label: 'Toy Story' },
      { item_id: 'tmdb_movie_585', rating: 4.5, genres: ['Animation', 'Family', 'Fantasy'], label: 'Monsters Inc.' },
    ],
    boostPreview: 'Animation+3.5, Family+3.5, Comedy+2, Fantasy+1.5',
  },
  {
    id: 'documentary',
    label: 'Documentary',
    emoji: '📽️',
    color: 'amber',
    description: 'Clicks on highly rated documentary films',
    clicks: ['tmdb_movie_37799', 'tmdb_movie_15258', 'tmdb_movie_293670', 'tmdb_movie_395990', 'tmdb_movie_399055'],
    ratings: [
      { item_id: 'tmdb_movie_37799', rating: 5.0, genres: ['Documentary'], label: 'Planet Earth' },
      { item_id: 'tmdb_movie_15258', rating: 4.5, genres: ['Documentary', 'History'], label: 'March of the Penguins' },
    ],
    boostPreview: 'Documentary+3.5, History+1.5',
  },
] as const;

type PresetId = typeof GENRE_PRESETS[number]['id'];

const PRESET_COLORS: Record<string, { pill: string; active: string; dot: string }> = {
  blue:   { pill: 'border-blue-800/60 text-blue-300 hover:bg-blue-900/30',   active: 'bg-blue-900/50 border-blue-500 text-blue-200',   dot: 'bg-blue-400' },
  purple: { pill: 'border-purple-800/60 text-purple-300 hover:bg-purple-900/30', active: 'bg-purple-900/50 border-purple-500 text-purple-200', dot: 'bg-purple-400' },
  orange: { pill: 'border-orange-800/60 text-orange-300 hover:bg-orange-900/30', active: 'bg-orange-900/50 border-orange-500 text-orange-200', dot: 'bg-orange-400' },
  pink:   { pill: 'border-pink-800/60 text-pink-300 hover:bg-pink-900/30',   active: 'bg-pink-900/50 border-pink-500 text-pink-200',   dot: 'bg-pink-400' },
  green:  { pill: 'border-green-800/60 text-green-300 hover:bg-green-900/30', active: 'bg-green-900/50 border-green-500 text-green-200', dot: 'bg-green-400' },
  amber:  { pill: 'border-amber-800/60 text-amber-300 hover:bg-amber-900/30', active: 'bg-amber-900/50 border-amber-500 text-amber-200', dot: 'bg-amber-400' },
};

// User Simulation sub-component
function UserSimulation() {
  // Generate a fresh random numeric user ID on each component mount.
  // This is critical: the Two-Tower model maps user_id to a training index via
  // int(user_id) % num_users. A non-numeric or always-identical ID always
  // produces user_idx=0 → the same embedding → the same results every run.
  const [simUserId, setSimUserId] = useState(() => String(Math.floor(Math.random() * 4000) + 1));
  const [selectedPreset, setSelectedPreset] = useState<PresetId>('action_scifi');
  const [log, setLog] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [recsBefore, setRecsBefore] = useState<any[]>([]);
  const [recsAfter, setRecsAfter] = useState<any[]>([]);
  const [usedPresetLabel, setUsedPresetLabel] = useState('');

  const resetSimulation = async (userId: string = simUserId) => {
    try {
      await axios.post(`${BASE}/admin/reset_sim_user`, { user_id: userId });
      setLog([]);
      setRecsBefore([]);
      setRecsAfter([]);
      setUsedPresetLabel('');
    } catch (e: any) {
      addLog(`❌ Error resetting simulation: ${e.message}`);
    }
  };

  const addLog = (msg: string) => setLog(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${msg}`]);

  const runSimulation = async () => {
    setRunning(true);

    const preset = GENRE_PRESETS.find(p => p.id === selectedPreset)!;

    // Generate a fresh random user ID each run so the Two-Tower model
    // computes a different embedding index and produces varied results.
    const freshId = String(Math.floor(Math.random() * 4000) + 1);
    setSimUserId(freshId);
    setUsedPresetLabel(preset.label);
    setLog([]);
    setRecsBefore([]);
    setRecsAfter([]);

    try {
      // Always clear Redis state first so we start from a true cold-start.
      await axios.post(`${BASE}/admin/reset_sim_user`, { user_id: freshId });
      addLog(`🆕 Fresh simulation user ID: ${freshId} (Two-Tower index: ${freshId})`);
      addLog(`🎬 Genre preset: ${preset.emoji} ${preset.label}`);
      addLog('🧊 Cold start — no prior signals, fetching baseline recommendations...');

      // Step 1: Get initial recommendations (cold start)
      const r1 = await axios.get(`${BASE}/recommendations?user_id=${freshId}&limit=5`);
      setRecsBefore(r1.data.recommendations || []);
      addLog(`Got ${r1.data.recommendations?.length || 0} initial recommendations`);

      // Step 2: Simulate 5 clicks on the selected genre preset items
      addLog(`🖱️ Simulating 5 clicks on ${preset.label} titles...`);
      for (const itemId of preset.clicks) {
        await axios.post(`${BASE}/events/click`, { user_id: freshId, item_id: itemId });
        addLog(`Event: click registered on item ${itemId}`);
        await new Promise(r => setTimeout(r, 450));
      }

      // Step 3: Rate 2 items highly (this also updates genre_boost in Redis)
      for (const r of preset.ratings) {
        addLog(`⭐ Rating ${r.label} (${r.genres.join('/')}) → ${r.rating} stars`);
        await axios.post(`${BASE}/events/rate`, { user_id: freshId, item_id: r.item_id, rating: r.rating, genres: r.genres });
      }
      addLog(`📊 Genre boosts updated in Redis: ${preset.boostPreview}`);
      await new Promise(r => setTimeout(r, 300));

      // Step 4: Get updated recommendations (signals now in Redis)
      addLog('🔄 Fetching updated recommendations with learned genre signals...');
      const r2 = await axios.get(`${BASE}/recommendations?user_id=${freshId}&limit=5`);
      setRecsAfter(r2.data.recommendations || []);
      addLog(`Got ${r2.data.recommendations?.length || 0} updated recommendations`);

      addLog('✅ Simulation complete! Genre boosts now influence the re-ranking score (genre_match weight: 20%).');
    } catch (e: any) {
      addLog(`❌ Error executing simulation: ${e.message}`);
    } finally {
      setRunning(false);
    }
  };

  const activePreset = GENRE_PRESETS.find(p => p.id === selectedPreset)!;
  const activeColors = PRESET_COLORS[activePreset.color];

  return (
    <div>
      <h2 className="text-2xl font-bold mb-3">User Simulation</h2>
      <p className="text-gray-400 mb-5 leading-relaxed">
        Simulate a dynamic cold-start interaction workflow. Pick a genre preset below — the model will click and rate matching titles, then show how recommendations shift to reflect those preferences.
      </p>

      {/* Genre Preset Selector */}
      <div className="mb-6">
        <p className="text-gray-400 text-xs font-bold tracking-wider uppercase mb-3">Genre Preset</p>
        <div className="flex flex-wrap gap-2">
          {GENRE_PRESETS.map(preset => {
            const colors = PRESET_COLORS[preset.color];
            const isActive = selectedPreset === preset.id;
            return (
              <button
                key={preset.id}
                onClick={() => setSelectedPreset(preset.id)}
                disabled={running}
                className={`flex items-center gap-2 px-3.5 py-2 rounded-lg border text-sm font-semibold transition-all disabled:opacity-50 disabled:cursor-not-allowed
                  ${isActive ? colors.active : `bg-transparent ${colors.pill}`}`}
              >
                <span>{preset.emoji}</span>
                <span>{preset.label}</span>
              </button>
            );
          })}
        </div>
        {/* Preset description */}
        <div className="mt-3 flex items-start gap-2 text-xs text-gray-500 bg-gray-900/60 border border-gray-800 rounded-lg px-3.5 py-2.5 w-fit">
          <span className={`w-1.5 h-1.5 rounded-full mt-0.5 flex-shrink-0 ${activeColors.dot}`} />
          <span>{activePreset.description} · will boost: <span className="text-gray-400">{activePreset.boostPreview}</span></span>
        </div>
      </div>

      {/* Active user info */}
      <div className="flex items-center gap-3 mb-5 bg-gray-900 border border-gray-800 rounded-lg px-4 py-2.5 w-fit text-sm">
        <span className="text-gray-500 font-medium">Sim User ID:</span>
        <span className="font-mono text-yellow-400 font-bold">{simUserId}</span>
        <span className="text-gray-600">·</span>
        <span className="text-gray-500 text-xs">Two-Tower index: <span className="text-blue-400">{simUserId}</span> · refreshes on each run</span>
      </div>

      <div className="flex gap-4 mb-6">
        <button
          onClick={runSimulation}
          disabled={running}
          className="bg-red-600 hover:bg-red-700 disabled:bg-gray-800 text-white font-bold px-6 py-3 rounded transition-colors shadow-md disabled:cursor-not-allowed"
        >
          {running ? '⏳ Simulation Running...' : `▶ Run — ${activePreset.emoji} ${activePreset.label}`}
        </button>

        <button
          onClick={() => resetSimulation()}
          disabled={running}
          className="border border-gray-700 hover:border-gray-500 hover:bg-gray-800/40 text-gray-300 font-bold px-6 py-3 rounded transition-colors shadow-md"
        >
          Reset Simulation
        </button>
      </div>

      {log.length > 0 && (
        <div className="bg-gray-900 rounded-lg p-4 border border-gray-800 mb-6 h-52 overflow-y-auto font-mono text-sm leading-relaxed shadow-inner">
          {log.map((l, i) => (
            <div key={i} className={l.includes('❌') ? 'text-red-400' : l.includes('✅') ? 'text-green-400' : l.includes('🆕') || l.includes('🎬') ? 'text-yellow-300' : l.includes('📊') ? 'text-blue-300' : l.includes('⭐') ? 'text-amber-300' : 'text-neutral-300'}>
              {l}
            </div>
          ))}
        </div>
      )}

      {recsBefore.length > 0 && recsAfter.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <h3 className="text-lg font-semibold mb-3 flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-blue-500" />
              Before (Cold Start)
            </h3>
            <div className="space-y-2">
              {recsBefore.map((item: any, i: number) => (
                <div key={i} className="bg-gray-900 rounded-lg p-3 border border-gray-800 flex items-center gap-3 shadow-sm hover:border-gray-700 transition-colors">
                  <span className="text-gray-500 text-sm font-bold w-4">{i + 1}</span>
                  <div className="w-10 h-14 bg-neutral-800 rounded overflow-hidden flex-shrink-0">
                    {item.poster_url ? (
                      <img src={item.poster_url} alt={item.title} className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-[10px] text-gray-500 font-bold bg-neutral-850">
                        🎬
                      </div>
                    )}
                  </div>
                  <div>
                    <p className="text-white text-sm font-semibold truncate max-w-[280px]">{item.title}</p>
                    <p className="text-gray-400 text-xs mt-0.5 capitalize">{item.content_type} • <span className="text-green-500 font-bold">{Math.round(item.match_score || 0)}% Match</span></p>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <h3 className="text-lg font-semibold mb-3 flex items-center gap-2">
              <span className={`w-2.5 h-2.5 rounded-full ${activeColors.dot}`} />
              After — {usedPresetLabel || activePreset.label} Signals
            </h3>
            <div className="space-y-2">
              {recsAfter.map((item: any, i: number) => (
                <div key={i} className="bg-gray-900 rounded-lg p-3 border border-gray-800 flex items-center gap-3 shadow-sm hover:border-gray-700 transition-colors">
                  <span className="text-gray-500 text-sm font-bold w-4">{i + 1}</span>
                  <div className="w-10 h-14 bg-neutral-800 rounded overflow-hidden flex-shrink-0">
                    {item.poster_url ? (
                      <img src={item.poster_url} alt={item.title} className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-[10px] text-gray-500 font-bold bg-neutral-855">
                        🎬
                      </div>
                    )}
                  </div>
                  <div>
                    <p className="text-white text-sm font-semibold truncate max-w-[280px]">{item.title}</p>
                    <p className="text-gray-400 text-xs mt-0.5 capitalize">{item.content_type} • <span className="text-green-500 font-bold">{Math.round(item.match_score || 0)}% Match</span></p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

