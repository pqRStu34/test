/**
 * Cloudflare Worker: Telegram Bot for Anime Archive D1 Database Management
 * 
 * Features:
 * - Single-file deployment for Cloudflare Workers.
 * - MyAnimeList (MAL) API v2 Integration with `nsfw=true` (all titles work).
 * - Interactive step-by-step confirmation wizard (reviews one field at a time:
 *   Title, Alt Titles, Genres, Year, Season, Type, Episodes, Status, Description, Identifier).
 * - Edit existing anime directly in Cloudflare D1 with choice buttons or text typing.
 * - Safe deletion flow with confirmation prompts.
 * - Clean 3-option main menu: [➕ Add Anime] [✏️ Edit Anime] [🗑️ Delete Anime].
 * - Web dashboard at root (/) for setup, diagnostics, and 1-click webhook registration.
 * 
 * Environment Bindings (Secrets):
 * - DB: Cloudflare D1 Database binding (REQUIRED)
 * - TELEGRAM_BOT_TOKEN: Telegram Bot token from @BotFather (REQUIRED)
 * - MAL_CLIENT_ID: MyAnimeList Client ID (REQUIRED for MAL metadata)
 * - MAL_CLIENT_SECRET: (Optional) MyAnimeList Client Secret
 * - ADMIN_USER_ID: Telegram User ID(s) allowed to manage the database (comma-separated)
 */

// ==========================================
// CONSTANTS & FIELD DEFINITIONS
// ==========================================
const ANIME_TYPES = ['TV', 'Movie', 'OVA', 'ONA', 'Special'];
const ANIME_STATUSES = ['Completed', 'Airing', 'Upcoming'];
const ANIME_SEASONS = ['Winter', 'Spring', 'Summer', 'Fall'];
const COMMON_EPISODES = [1, 12, 13, 24, 25];
const COMMON_YEARS = [2026, 2025, 2024, 2023, 2022];

const REVIEW_FIELDS = [
  { key: 'title', label: 'Title / Name', type: 'text' },
  { key: 'alt_title', label: 'Alternate Titles', type: 'text' },
  { key: 'genres', label: 'Genres', type: 'text' },
  { key: 'year', label: 'Release Year', type: 'year' },
  { key: 'season', label: 'Season', type: 'season' },
  { key: 'type', label: 'Format Type', type: 'type' },
  { key: 'episodes_count', label: 'Episode Count', type: 'episodes' },
  { key: 'status', label: 'Status', type: 'status' },
  { key: 'description', label: 'Description', type: 'text' },
  { key: 'identifier', label: 'Archive Identifier', type: 'identifier' }
];

// ==========================================
// STRING & HTML UTILITIES
// ==========================================
function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function generateSlug(title) {
  return String(title || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)+/g, '');
}

function formatMalAltTitles(media) {
  const alt = media.alternative_titles || {};
  const titles = [];

  const mainTitle = (media.title || '').trim();
  const enTitle = (alt.en || '').trim();
  const jaTitle = (alt.ja || '').trim();

  if (enTitle && enTitle !== mainTitle) {
    titles.push(enTitle);
  }
  if (jaTitle && jaTitle !== mainTitle && jaTitle !== enTitle) {
    titles.push(jaTitle);
  }
  if (Array.isArray(alt.synonyms)) {
    alt.synonyms.forEach(s => {
      const clean = (s || '').trim();
      if (clean && !titles.includes(clean) && clean !== mainTitle) {
        titles.push(clean);
      }
    });
  }

  if (titles.length === 0) return '';
  return titles.map(t => `[${t}]`).join('');
}

// ==========================================
// TELEGRAM API CLIENT
// ==========================================
async function callTelegram(method, payload, env) {
  const token = env.TELEGRAM_BOT_TOKEN;
  if (!token) throw new Error('TELEGRAM_BOT_TOKEN is not configured in Worker secrets');

  const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  const data = await res.json();
  if (!data.ok) {
    console.warn(`[Telegram API Warning] ${method}:`, data.description);
  }
  return data;
}

async function sendMessage(chatId, text, options = {}, env) {
  return callTelegram('sendMessage', {
    chat_id: chatId,
    text: text,
    parse_mode: 'HTML',
    disable_web_page_preview: true,
    ...options
  }, env);
}

async function editMessage(chatId, messageId, text, options = {}, env) {
  return callTelegram('editMessageText', {
    chat_id: chatId,
    message_id: messageId,
    text: text,
    parse_mode: 'HTML',
    disable_web_page_preview: true,
    ...options
  }, env);
}

async function answerCallback(callbackQueryId, text = '', env) {
  return callTelegram('answerCallbackQuery', {
    callback_query_id: callbackQueryId,
    text: text
  }, env);
}

// ==========================================
// MYANIMELIST API V2 CLIENT (WITH NSFW)
// ==========================================
async function fetchMalAnime(malId, env) {
  const clientId = env.MAL_CLIENT_ID;
  if (!clientId) {
    throw new Error('MAL_CLIENT_ID is not configured in Cloudflare Worker secrets');
  }

  const cleanId = String(malId).trim().replace(/[^\d]/g, '');
  if (!cleanId) {
    throw new Error('Please provide a valid numerical MyAnimeList ID');
  }

  const fields = [
    'id', 'title', 'main_picture', 'alternative_titles',
    'start_date', 'end_date', 'synopsis', 'mean', 'rank',
    'popularity', 'num_list_users', 'num_scoring_users',
    'nsfw', 'created_at', 'updated_at', 'media_type',
    'status', 'genres', 'my_list_status', 'num_episodes',
    'start_season', 'broadcast', 'source', 'rating', 'studios'
  ].join(',');

  // Critical: nsfw=true ensures all IDs (standard, ecchi, 18+ adult) are accessible
  const url = `https://api.myanimelist.net/v2/anime/${cleanId}?fields=${fields}&nsfw=true`;

  const res = await fetch(url, {
    headers: {
      'X-MAL-CLIENT-ID': clientId,
      'User-Agent': 'AnimeArchiveManager/2.0'
    }
  });

  if (!res.ok) {
    if (res.status === 404) {
      throw new Error(`Anime with MAL ID ${cleanId} was not found on MyAnimeList.`);
    }
    const errText = await res.text();
    throw new Error(`MyAnimeList API error (${res.status}): ${errText}`);
  }

  const m = await res.json();

  // Media Type Mapping
  let type = 'TV';
  const rawType = (m.media_type || '').toLowerCase();
  if (rawType === 'movie') type = 'Movie';
  else if (rawType === 'ova') type = 'OVA';
  else if (rawType === 'ona') type = 'ONA';
  else if (rawType === 'special') type = 'Special';

  // Status Mapping
  let status = 'Completed';
  const rawStatus = (m.status || '').toLowerCase();
  if (rawStatus === 'currently_airing') status = 'Airing';
  else if (rawStatus === 'not_yet_aired') status = 'Upcoming';

  // Season & Year Mapping
  let season = 'Spring';
  if (m.start_season && m.start_season.season) {
    const s = m.start_season.season.toLowerCase();
    season = s.charAt(0).toUpperCase() + s.slice(1);
  } else if (m.start_date) {
    const parts = m.start_date.split('-');
    const month = parseInt(parts[1], 10);
    if (month >= 1 && month <= 3) season = 'Winter';
    else if (month >= 4 && month <= 6) season = 'Spring';
    else if (month >= 7 && month <= 9) season = 'Summer';
    else if (month >= 10 && month <= 12) season = 'Fall';
  }

  let year = (m.start_season && m.start_season.year) ||
    (m.start_date ? parseInt(m.start_date.split('-')[0], 10) : null) ||
    new Date().getFullYear();

  const genres = (m.genres || []).map(g => g.name).join(', ');
  const description = (m.synopsis || '')
    .replace(/<[^>]*>?/gm, '')
    .replace(/\[Written by MAL Rewrite\]/gi, '')
    .trim();

  return {
    id: parseInt(cleanId, 10),
    title: m.title || `Anime #${cleanId}`,
    alt_title: formatMalAltTitles(m),
    slug: generateSlug(m.title || `anime-${cleanId}`),
    description: description,
    type: type,
    episodes_count: m.num_episodes || 12,
    genres: genres,
    status: status,
    year: year,
    season: season,
    identifier: 'corrupted_files'
  };
}

// ==========================================
// D1 DATABASE & SESSION MANAGEMENT
// ==========================================
async function initDb(db) {
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS anime (
      id INTEGER PRIMARY KEY,
      title TEXT NOT NULL,
      alt_title TEXT,
      slug TEXT,
      description TEXT,
      type TEXT DEFAULT 'TV',
      episodes_count INTEGER DEFAULT 12,
      genres TEXT,
      status TEXT DEFAULT 'Completed',
      year INTEGER,
      season TEXT,
      identifier TEXT DEFAULT 'corrupted_files'
    );
  `).run();

  await db.prepare(`
    CREATE TABLE IF NOT EXISTS bot_sessions (
      user_id TEXT PRIMARY KEY,
      state TEXT,
      data TEXT,
      updated_at INTEGER
    );
  `).run();
}

async function getSession(userId, db) {
  try {
    const row = await db.prepare('SELECT state, data FROM bot_sessions WHERE user_id = ?').bind(String(userId)).first();
    if (!row) return null;
    return {
      state: row.state,
      data: row.data ? JSON.parse(row.data) : {}
    };
  } catch (e) {
    return null;
  }
}

async function setSession(userId, state, data, db) {
  try {
    await db.prepare(`
      INSERT INTO bot_sessions (user_id, state, data, updated_at)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(user_id) DO UPDATE SET
        state = excluded.state,
        data = excluded.data,
        updated_at = excluded.updated_at
    `).bind(String(userId), state, JSON.stringify(data || {}), Date.now()).run();
  } catch (e) {
    console.warn('Failed to set session:', e);
  }
}

async function clearSession(userId, db) {
  try {
    await db.prepare('DELETE FROM bot_sessions WHERE user_id = ?').bind(String(userId)).run();
  } catch (e) { }
}

async function getAnimeById(id, db) {
  return db.prepare('SELECT * FROM anime WHERE id = ?').bind(parseInt(id, 10)).first();
}

async function searchAnimeInDb(query, db) {
  const term = `%${query}%`;
  return db.prepare(`
    SELECT id, title, alt_title, year, episodes_count, type, status 
    FROM anime 
    WHERE title LIKE ? OR alt_title LIKE ? OR id = ?
    LIMIT 8
  `).bind(term, term, parseInt(query, 10) || -1).all();
}

async function upsertAnime(anime, db) {
  return db.prepare(`
    INSERT INTO anime (id, title, alt_title, slug, description, type, episodes_count, genres, status, year, season, identifier)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
      title = excluded.title,
      alt_title = excluded.alt_title,
      slug = excluded.slug,
      description = excluded.description,
      type = excluded.type,
      episodes_count = excluded.episodes_count,
      genres = excluded.genres,
      status = excluded.status,
      year = excluded.year,
      season = excluded.season,
      identifier = excluded.identifier
  `).bind(
    parseInt(anime.id, 10),
    anime.title,
    anime.alt_title || '',
    anime.slug || generateSlug(anime.title),
    anime.description || '',
    anime.type || 'TV',
    parseInt(anime.episodes_count, 10) || 12,
    anime.genres || '',
    anime.status || 'Completed',
    parseInt(anime.year, 10) || new Date().getFullYear(),
    anime.season || 'Spring',
    anime.identifier || 'corrupted_files'
  ).run();
}

async function updateSingleField(id, field, value, db) {
  const allowed = ['title', 'alt_title', 'slug', 'description', 'type', 'episodes_count', 'genres', 'status', 'year', 'season', 'identifier'];
  if (!allowed.includes(field)) throw new Error('Disallowed column update');

  let finalVal = value;
  if (field === 'episodes_count' || field === 'year') {
    finalVal = parseInt(value, 10) || 0;
  }

  await db.prepare(`UPDATE anime SET ${field} = ? WHERE id = ?`).bind(finalVal, parseInt(id, 10)).run();

  if (field === 'title') {
    const newSlug = generateSlug(value);
    await db.prepare('UPDATE anime SET slug = ? WHERE id = ?').bind(newSlug, parseInt(id, 10)).run();
  }
}

async function deleteAnimeFromDb(id, db) {
  return db.prepare('DELETE FROM anime WHERE id = ?').bind(parseInt(id, 10)).run();
}

async function getStats(db) {
  const total = await db.prepare('SELECT COUNT(*) as count FROM anime').first();
  return {
    total: total ? total.count : 0
  };
}

// ==========================================
// SECURITY & PERMISSIONS
// ==========================================
function isAuthorized(fromId, env) {
  const adminIds = String(env.ADMIN_USER_ID || env.ALLOWED_USER_IDS || '').trim();
  if (!adminIds) return true;
  const list = adminIds.split(',').map(s => s.trim());
  return list.includes(String(fromId));
}

// ==========================================
// CARD RENDERERS & KEYBOARDS
// ==========================================
function formatAnimeCard(anime) {
  const descSnippet = (anime.description || 'No description').slice(0, 260);
  const ellipsis = anime.description && anime.description.length > 260 ? '...' : '';

  return `🎬 <b>${escapeHtml(anime.title)}</b> (ID: <code>${anime.id}</code>)\n` +
    `• <b>Alt:</b> ${escapeHtml(anime.alt_title || 'None')}\n` +
    `• <b>Type:</b> <code>${escapeHtml(anime.type)}</code> | <b>Episodes:</b> <code>${anime.episodes_count}</code>\n` +
    `• <b>Status:</b> <code>${escapeHtml(anime.status)}</code> | <b>Season:</b> <code>${escapeHtml(anime.season)} ${anime.year}</code>\n` +
    `• <b>Genres:</b> ${escapeHtml(anime.genres || 'None')}\n` +
    `• <b>Identifier:</b> <code>${escapeHtml(anime.identifier || 'corrupted_files')}</code>\n` +
    `• <b>Slug:</b> <code>${escapeHtml(anime.slug)}</code>\n\n` +
    `📖 <i>${escapeHtml(descSnippet)}${ellipsis}</i>`;
}

function getMainMenuKeyboard() {
  return {
    inline_keyboard: [
      [{ text: '➕ Add Anime', callback_data: 'menu:add' }],
      [{ text: '✏️ Edit Anime', callback_data: 'menu:edit' }],
      [{ text: '🗑️ Delete Anime', callback_data: 'menu:delete' }],
      [{ text: '📦 Generate JSON & Supabase Sync', callback_data: 'menu:json' }]
    ]
  };
}

function getEditFieldsKeyboard(animeId) {
  return {
    inline_keyboard: [
      [
        { text: '✏️ Title', callback_data: `ed_f:${animeId}:title` },
        { text: '✏️ Alt Titles', callback_data: `ed_f:${animeId}:alt_title` }
      ],
      [
        { text: '🏷️ Genres', callback_data: `ed_f:${animeId}:genres` },
        { text: '📅 Year', callback_data: `ed_c:${animeId}:year` }
      ],
      [
        { text: '🌸 Season', callback_data: `ed_c:${animeId}:season` },
        { text: '📺 Type', callback_data: `ed_c:${animeId}:type` }
      ],
      [
        { text: '🔢 Episodes', callback_data: `ed_c:${animeId}:episodes_count` },
        { text: '📡 Status', callback_data: `ed_c:${animeId}:status` }
      ],
      [
        { text: '📖 Description', callback_data: `ed_f:${animeId}:description` },
        { text: '📁 Identifier', callback_data: `ed_c:${animeId}:identifier` }
      ],
      [
        { text: '🗑️ Delete This Anime', callback_data: `del_ask:${animeId}` }
      ],
      [
        { text: '🏠 Main Menu', callback_data: 'nav:menu' }
      ]
    ]
  };
}

// ==========================================
// WORKER REQUEST HANDLER
// ==========================================
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Web Dashboard (Browser GET)
    if (request.method === 'GET') {
      if (url.pathname === '/setWebhook') {
        const webhookUrl = url.searchParams.get('url') || `${url.origin}/webhook`;
        const res = await callTelegram('setWebhook', { url: webhookUrl }, env);
        return new Response(JSON.stringify(res, null, 2), {
          headers: { 'Content-Type': 'application/json' }
        });
      }

      if (url.pathname === '/webhookInfo') {
        const res = await callTelegram('getWebhookInfo', {}, env);
        return new Response(JSON.stringify(res, null, 2), {
          headers: { 'Content-Type': 'application/json' }
        });
      }

      let dbHealthy = false;
      let totalAnime = 0;
      if (env.DB) {
        try {
          await initDb(env.DB);
          const stats = await getStats(env.DB);
          totalAnime = stats.total;
          dbHealthy = true;
        } catch (e) { }
      }

      const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Anime Archive Bot Manager</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; max-width: 620px; margin: auto; line-height: 1.6; }
    .card { background: #1e293b; padding: 1.5rem; border-radius: 8px; border: 1px solid #334155; margin-bottom: 1.5rem; }
    .badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85rem; }
    .ok { background: #059669; color: white; }
    .warn { background: #d97706; color: white; }
    .btn { background: #3b82f6; color: white; border: none; padding: 10px 18px; border-radius: 6px; cursor: pointer; text-decoration: none; font-weight: bold; display: inline-block; }
    .btn:hover { background: #2563eb; }
    code { background: #0b0f19; padding: 2px 6px; border-radius: 4px; color: #38bdf8; font-family: monospace; }
  </style>
</head>
<body>
  <h2>🤖 Anime Archive Telegram Bot (MAL v2)</h2>
  <div class="card">
    <p><b>D1 Database:</b> ${dbHealthy ? '<span class="badge ok">Connected</span>' : '<span class="badge warn">Missing / Not Bound</span>'}</p>
    <p><b>Total Anime in D1:</b> <code>${totalAnime}</code></p>
    <p><b>MAL Client ID:</b> ${env.MAL_CLIENT_ID ? '<span class="badge ok">Configured</span>' : '<span class="badge warn">Missing MAL_CLIENT_ID</span>'}</p>
    <p><b>Telegram Token:</b> ${env.TELEGRAM_BOT_TOKEN ? '<span class="badge ok">Configured</span>' : '<span class="badge warn">Missing TELEGRAM_BOT_TOKEN</span>'}</p>
    <p><b>Admin Protection:</b> ${env.ADMIN_USER_ID ? `Restricted to <code>${env.ADMIN_USER_ID}</code>` : '<span class="badge warn">Open (All users)</span>'}</p>
  </div>
  <div class="card">
    <h3>🔗 Telegram Webhook</h3>
    <p>Click below to register this Worker URL with Telegram:</p>
    <a class="btn" href="/setWebhook">Register Webhook Automatically</a>
  </div>
</body>
</html>`;
      return new Response(html, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
    }

    // 2. Telegram Webhook (POST)
    if (request.method !== 'POST') {
      return new Response('Method Not Allowed', { status: 405 });
    }

    let update = null;
    try {
      update = await request.json();
    } catch (e) {
      return new Response('Invalid JSON', { status: 400 });
    }

    if (!env.DB || !env.TELEGRAM_BOT_TOKEN) {
      return new Response('Missing DB or TELEGRAM_BOT_TOKEN', { status: 500 });
    }

    ctx.waitUntil((async () => {
      try {
        await initDb(env.DB);
        if (update.callback_query) {
          await handleCallbackQuery(update.callback_query, env);
        } else if (update.message) {
          await handleMessage(update.message, env);
        }
      } catch (err) {
        console.error('Update processing error:', err);
      }
    })());

    return new Response('OK', { status: 200 });
  }
};

// ==========================================
// MESSAGE ROUTER & CONVERSATION HANDLER
// ==========================================
async function handleMessage(message, env) {
  const chatId = message.chat.id;
  const fromId = message.from.id;
  const text = (message.text || '').trim();

  // Permission Check
  if (!isAuthorized(fromId, env)) {
    return sendMessage(chatId, `⛔ <b>Access Denied</b>\n\nYour Telegram User ID is: <code>${fromId}</code>\nTo authorize yourself, add this ID to your Cloudflare Worker secrets as <code>ADMIN_USER_ID</code>.`, {}, env);
  }

  // Global cancel
  if (text === '/cancel' || text.toLowerCase() === 'cancel') {
    await clearSession(fromId, env.DB);
    return sendMessage(chatId, '❌ Action cancelled.', { reply_markup: getMainMenuKeyboard() }, env);
  }

  // Commands
  if (text === '/start' || text === '/menu') {
    await clearSession(fromId, env.DB);
    const welcome = `👋 <b>Anime Archive D1 Manager</b>\n\nManage your anime catalog with choices and typing where required.\n\nChoose an option:`;
    return sendMessage(chatId, welcome, { reply_markup: getMainMenuKeyboard() }, env);
  }

  if (text === '/add' || text.startsWith('/add ')) {
    const malId = text.replace('/add', '').trim();
    if (malId) {
      return startMalAddFlow(chatId, fromId, malId, env);
    }
    await setSession(fromId, 'AWAIT_MAL_ID', {}, env.DB);
    return sendMessage(chatId, '➕ <b>Add Anime</b>\n\nPlease type the <b>MyAnimeList ID</b> (e.g. <code>52991</code>):', {
      reply_markup: { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'nav:menu' }]] }
    }, env);
  }

  if (text === '/edit' || text.startsWith('/edit ')) {
    const q = text.replace('/edit', '').trim();
    if (q) {
      return showEditCard(chatId, q, env);
    }
    await setSession(fromId, 'AWAIT_EDIT_QUERY', {}, env.DB);
    return sendMessage(chatId, '✏️ <b>Edit Anime</b>\n\nPlease type the <b>Anime ID</b> or <b>Title</b> you want to edit:', {
      reply_markup: { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'nav:menu' }]] }
    }, env);
  }

  if (text === '/delete' || text.startsWith('/delete ')) {
    const q = text.replace('/delete', '').trim();
    if (q) {
      return promptDeleteSearch(chatId, q, env);
    }
    await setSession(fromId, 'AWAIT_DELETE_QUERY', {}, env.DB);
    return sendMessage(chatId, '🗑️ <b>Delete Anime</b>\n\nPlease type the <b>Anime ID</b> or <b>Title</b> of the anime you want to delete:', {
      reply_markup: { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'nav:menu' }]] }
    }, env);
  }

  if (text === '/json' || text.startsWith('/json ') || text.startsWith('/generate ')) {
    const rawParam = text.replace('/generate', '').replace('/json', '').trim();
    if (rawParam) {
      return handleJsonGeneration(chatId, rawParam, env);
    }
    await setSession(fromId, 'AWAIT_JSON_ANIME_ID', {}, env.DB);
    return sendMessage(chatId, '📦 <b>Generate Episode JSON</b>\n\nPlease type the <b>Anime ID</b> (e.g. <code>40969</code> or <code>63140</code>):', {
      reply_markup: { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'nav:menu' }]] }
    }, env);
  }

  // Active Session State Machine
  const session = await getSession(fromId, env.DB);
  if (!session || !session.state) {
    // Fallback: quick search in D1
    return executeSearchAndRespond(chatId, text, env);
  }

  await handleConversationStep(chatId, fromId, text, session, env);
}

// ==========================================
// CONVERSATIONAL STATE MACHINE
// ==========================================
async function handleConversationStep(chatId, fromId, text, session, env) {
  const db = env.DB;
  const state = session.state;
  const data = session.data || {};

  // 1. User typed MAL ID to add
  if (state === 'AWAIT_MAL_ID') {
    return startMalAddFlow(chatId, fromId, text, env);
  }

  // 1b. User typed Anime ID for JSON generation
  if (state === 'AWAIT_JSON_ANIME_ID') {
    await clearSession(fromId, db);
    return handleJsonGeneration(chatId, text.trim(), env);
  }

  // 2. User typed edited value during Step-by-Step Review (Text Field)
  if (state === 'AWAIT_REVIEW_EDIT_TEXT') {
    const { anime, stepIdx, fieldKey } = data;
    if (!anime || fieldKey === undefined) {
      await clearSession(fromId, db);
      return sendMessage(chatId, '⚠️ Session expired. Please start over.', { reply_markup: getMainMenuKeyboard() }, env);
    }

    anime[fieldKey] = text.trim();
    if (fieldKey === 'title') {
      anime.slug = generateSlug(text.trim());
    }

    // Advance to next field
    return promptReviewField(chatId, fromId, anime, stepIdx + 1, env);
  }

  // 3. User typed custom number during Review (Year or Episodes)
  if (state === 'AWAIT_REVIEW_CUSTOM_NUM') {
    const { anime, stepIdx, fieldKey } = data;
    const num = parseInt(text.trim(), 10);
    if (isNaN(num) || num <= 0) {
      return sendMessage(chatId, `⚠️ Please type a valid positive number for <b>${fieldKey}</b>:`, {}, env);
    }

    anime[fieldKey] = num;
    return promptReviewField(chatId, fromId, anime, stepIdx + 1, env);
  }

  // 4. User typed custom identifier during Review
  if (state === 'AWAIT_REVIEW_CUSTOM_IDENT') {
    const { anime, stepIdx } = data;
    anime.identifier = text.trim() || 'corrupted_files';
    return promptReviewField(chatId, fromId, anime, stepIdx + 1, env);
  }

  // 5. User typed Edit Search query
  if (state === 'AWAIT_EDIT_QUERY') {
    await clearSession(fromId, db);
    return showEditCard(chatId, text, env);
  }

  // 6. User typed Delete Search query
  if (state === 'AWAIT_DELETE_QUERY') {
    await clearSession(fromId, db);
    return promptDeleteSearch(chatId, text, env);
  }

  // 7. User typed replacement value for an existing anime field
  if (state === 'EDIT_TYPING_FIELD') {
    const animeId = data.editingAnimeId;
    const fieldName = data.editingField;
    if (!animeId || !fieldName) {
      await clearSession(fromId, db);
      return sendMessage(chatId, '⚠️ Session expired. Please try again.', { reply_markup: getMainMenuKeyboard() }, env);
    }

    await updateSingleField(animeId, fieldName, text, db);
    await clearSession(fromId, db);
    await sendMessage(chatId, `✅ <b>${fieldName}</b> updated successfully!`, {}, env);
    return showEditCard(chatId, animeId, env);
  }
}

// ==========================================
// CALLBACK QUERY (BUTTON CLICKS) HANDLER
// ==========================================
async function handleCallbackQuery(cb, env) {
  const chatId = cb.message.chat.id;
  const messageId = cb.message.message_id;
  const fromId = cb.from.id;
  const dataStr = cb.data || '';
  const db = env.DB;

  if (!isAuthorized(fromId, env)) {
    return answerCallback(cb.id, '⛔ Access Denied', env);
  }

  await answerCallback(cb.id, '', env);

  // Main Menu Navigation
  if (dataStr === 'nav:menu') {
    await clearSession(fromId, db);
    const welcome = `👋 <b>Anime Archive D1 Manager</b>\n\nChoose an option:`;
    return editMessage(chatId, messageId, welcome, { reply_markup: getMainMenuKeyboard() }, env);
  }

  // 3 Primary Menu Options
  if (dataStr === 'menu:add') {
    await setSession(fromId, 'AWAIT_MAL_ID', {}, db);
    return editMessage(chatId, messageId, '➕ <b>Add Anime</b>\n\nPlease type the <b>MyAnimeList ID</b> (e.g. <code>52991</code>):', {
      reply_markup: { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'nav:menu' }]] }
    }, env);
  }

  if (dataStr === 'menu:edit') {
    await setSession(fromId, 'AWAIT_EDIT_QUERY', {}, db);
    return editMessage(chatId, messageId, '✏️ <b>Edit Anime</b>\n\nPlease type the <b>Anime ID</b> or <b>Title</b> you want to edit:', {
      reply_markup: { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'nav:menu' }]] }
    }, env);
  }

  if (dataStr === 'menu:delete') {
    await setSession(fromId, 'AWAIT_DELETE_QUERY', {}, db);
    return editMessage(chatId, messageId, '🗑️ <b>Delete Anime</b>\n\nPlease type the <b>Anime ID</b> or <b>Title</b> of the anime you want to delete:', {
      reply_markup: { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'nav:menu' }]] }
    }, env);
  }

  if (dataStr === 'menu:json') {
    await setSession(fromId, 'AWAIT_JSON_ANIME_ID', {}, db);
    return editMessage(chatId, messageId, '📦 <b>Generate Episode JSON</b>\n\nPlease type the <b>Anime ID</b> (e.g. <code>40969</code> or <code>63140</code>):', {
      reply_markup: { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'nav:menu' }]] }
    }, env);
  }

  // Step-by-Step Review: Keep field value as-is
  if (dataStr.startsWith('rev_keep:')) {
    const stepIdx = parseInt(dataStr.replace('rev_keep:', ''), 10);
    const session = await getSession(fromId, db);
    if (!session || !session.data || !session.data.anime) {
      return sendMessage(chatId, '⚠️ Session expired. Please start over.', { reply_markup: getMainMenuKeyboard() }, env);
    }
    return promptReviewField(chatId, fromId, session.data.anime, stepIdx + 1, env, messageId);
  }

  // Step-by-Step Review: Edit field manually
  if (dataStr.startsWith('rev_edit:')) {
    const stepIdx = parseInt(dataStr.replace('rev_edit:', ''), 10);
    const session = await getSession(fromId, db);
    if (!session || !session.data || !session.data.anime) {
      return sendMessage(chatId, '⚠️ Session expired. Please start over.', { reply_markup: getMainMenuKeyboard() }, env);
    }
    return promptEditForField(chatId, fromId, session.data.anime, stepIdx, env, messageId);
  }

  // Step-by-Step Review: Choice selected (Type, Status, Season, etc.)
  if (dataStr.startsWith('rev_choice:')) {
    const parts = dataStr.split(':');
    const fieldKey = parts[1];
    const choiceVal = parts[2];

    const session = await getSession(fromId, db);
    if (!session || !session.data || !session.data.anime) {
      return sendMessage(chatId, '⚠️ Session expired. Please start over.', { reply_markup: getMainMenuKeyboard() }, env);
    }

    const anime = session.data.anime;
    const stepIdx = session.data.stepIdx !== undefined ? session.data.stepIdx : 0;

    if (fieldKey === 'year' || fieldKey === 'episodes_count') {
      anime[fieldKey] = parseInt(choiceVal, 10);
    } else {
      anime[fieldKey] = choiceVal;
    }

    // Advance to next field
    return promptReviewField(chatId, fromId, anime, stepIdx + 1, env, messageId);
  }

  // Step-by-Step Review: Prompt custom number typing (Year or Episodes)
  if (dataStr.startsWith('rev_custom:')) {
    const parts = dataStr.split(':');
    const fieldKey = parts[1];
    const stepIdx = parseInt(parts[2], 10);

    const session = await getSession(fromId, db);
    if (!session || !session.data || !session.data.anime) {
      return sendMessage(chatId, '⚠️ Session expired. Please start over.', { reply_markup: getMainMenuKeyboard() }, env);
    }

    await setSession(fromId, 'AWAIT_REVIEW_CUSTOM_NUM', { anime: session.data.anime, stepIdx, fieldKey }, db);
    return editMessage(chatId, messageId, `🔢 Please type the custom number for <b>${fieldKey}</b>:`, {
      reply_markup: {
        inline_keyboard: [
          [{ text: '↩️ Keep Original', callback_data: `rev_keep:${stepIdx}` }],
          [{ text: '❌ Cancel', callback_data: 'nav:menu' }]
        ]
      }
    }, env);
  }

  // Step-by-Step Review: Prompt custom identifier typing
  if (dataStr.startsWith('rev_custom_ident:')) {
    const stepIdx = parseInt(dataStr.replace('rev_custom_ident:', ''), 10);
    const session = await getSession(fromId, db);
    if (!session || !session.data || !session.data.anime) {
      return sendMessage(chatId, '⚠️ Session expired. Please start over.', { reply_markup: getMainMenuKeyboard() }, env);
    }

    await setSession(fromId, 'AWAIT_REVIEW_CUSTOM_IDENT', { anime: session.data.anime, stepIdx }, db);
    return editMessage(chatId, messageId, `📁 Please type your custom Internet Archive item identifier:`, {
      reply_markup: {
        inline_keyboard: [
          [{ text: '↩️ Keep (corrupted_files)', callback_data: `rev_keep:${stepIdx}` }],
          [{ text: '❌ Cancel', callback_data: 'nav:menu' }]
        ]
      }
    }, env);
  }

  // Step-by-Step Review: Restart review from beginning
  if (dataStr === 'rev_restart') {
    const session = await getSession(fromId, db);
    if (!session || !session.data || !session.data.anime) {
      return sendMessage(chatId, '⚠️ Session expired. Please start over.', { reply_markup: getMainMenuKeyboard() }, env);
    }
    return promptReviewField(chatId, fromId, session.data.anime, 0, env, messageId);
  }

  // Final Confirmation: Save to D1
  if (dataStr === 'save_add') {
    const session = await getSession(fromId, db);
    if (!session || !session.data || !session.data.anime || !session.data.anime.id) {
      return sendMessage(chatId, '⚠️ Error: No pending anime to save.', { reply_markup: getMainMenuKeyboard() }, env);
    }

    const anime = session.data.anime;
    await upsertAnime(anime, db);
    await clearSession(fromId, db);

    const card = formatAnimeCard(anime);
    return editMessage(chatId, messageId, `🎉 <b>Anime Successfully Saved to D1!</b>\n\n${card}`, {
      reply_markup: {
        inline_keyboard: [
          [{ text: '➕ Add Another Anime', callback_data: 'menu:add' }],
          [{ text: '✏️ Edit This Anime', callback_data: `ed_card:${anime.id}` }],
          [{ text: '🏠 Main Menu', callback_data: 'nav:menu' }]
        ]
      }
    }, env);
  }

  // Open Edit Card
  if (dataStr.startsWith('ed_card:')) {
    const id = dataStr.replace('ed_card:', '');
    return showEditCard(chatId, id, env, messageId);
  }

  // Edit single field typing prompt
  if (dataStr.startsWith('ed_f:')) {
    const parts = dataStr.split(':');
    const animeId = parts[1];
    const fieldName = parts[2];
    await setSession(fromId, 'EDIT_TYPING_FIELD', { editingAnimeId: animeId, editingField: fieldName }, db);
    return editMessage(chatId, messageId, `✏️ <b>Edit ${fieldName}</b> for ID <code>${animeId}</code>\n\nPlease type the new value:`, {
      reply_markup: {
        inline_keyboard: [[{ text: '❌ Cancel', callback_data: `ed_card:${animeId}` }]]
      }
    }, env);
  }

  // Edit single field with choices buttons
  if (dataStr.startsWith('ed_c:')) {
    const parts = dataStr.split(':');
    const animeId = parts[1];
    const fieldName = parts[2];
    return promptEditChoiceOptions(chatId, animeId, fieldName, env, messageId);
  }

  // Apply chosen choice to D1 directly
  if (dataStr.startsWith('set_val:')) {
    const parts = dataStr.split(':');
    const animeId = parts[1];
    const fieldName = parts[2];
    const value = parts[3];

    await updateSingleField(animeId, fieldName, value, db);
    return showEditCard(chatId, animeId, env, messageId);
  }

  // Delete flow confirmations
  if (dataStr.startsWith('del_ask:')) {
    const id = dataStr.replace('del_ask:', '');
    const anime = await getAnimeById(id, db);
    const title = anime ? anime.title : id;

    return editMessage(chatId, messageId, `⚠️ <b>Are you sure you want to permanently delete:</b>\n\n<b>${escapeHtml(title)}</b> (ID: <code>${id}</code>)?\n\nThis cannot be undone.`, {
      reply_markup: {
        inline_keyboard: [
          [{ text: '🚨 Yes, Permanently Delete', callback_data: `del_confirm:${id}` }],
          [{ text: '❌ Cancel', callback_data: `ed_card:${id}` }]
        ]
      }
    }, env);
  }

  if (dataStr.startsWith('del_confirm:')) {
    const id = dataStr.replace('del_confirm:', '');
    await deleteAnimeFromDb(id, db);
    return editMessage(chatId, messageId, `🗑️ Anime with ID <code>${id}</code> has been deleted from D1.`, {
      reply_markup: getMainMenuKeyboard()
    }, env);
  }
}

// ==========================================
// ADD FLOW: MAL FETCH & STEP-BY-STEP REVIEW
// ==========================================
async function startMalAddFlow(chatId, fromId, malId, env) {
  const cleanId = String(malId).trim();
  const loadingMsg = await sendMessage(chatId, `🔍 Fetching anime metadata from MyAnimeList for ID <code>${escapeHtml(cleanId)}</code> (NSFW enabled)...\n<i>Please wait a moment...</i>`, {}, env);

  try {
    const anime = await fetchMalAnime(cleanId, env);

    // Prompt user with Field 1 of the review sequence
    return promptReviewField(chatId, fromId, anime, 0, env, loadingMsg?.result?.message_id);
  } catch (err) {
    const errorText = `❌ <b>Failed to fetch from MyAnimeList:</b>\n\n${escapeHtml(err.message || 'Unknown error')}\n\nPlease check the MAL ID and try again:`;
    const keyboard = {
      inline_keyboard: [
        [{ text: '🔄 Try Another MAL ID', callback_data: 'menu:add' }],
        [{ text: '🏠 Main Menu', callback_data: 'nav:menu' }]
      ]
    };

    if (loadingMsg?.result?.message_id) {
      return editMessage(chatId, loadingMsg.result.message_id, errorText, { reply_markup: keyboard }, env);
    }
    return sendMessage(chatId, errorText, { reply_markup: keyboard }, env);
  }
}

async function promptReviewField(chatId, fromId, anime, stepIdx, env, messageId = null) {
  if (stepIdx >= REVIEW_FIELDS.length) {
    // All fields reviewed! Show final summary confirmation
    return showAddConfirmation(chatId, fromId, anime, env, messageId);
  }

  const field = REVIEW_FIELDS[stepIdx];
  const val = anime[field.key];
  const displayVal = (val === null || val === undefined || val === '')
    ? '<i>(Empty / None)</i>'
    : `<code>${escapeHtml(String(val))}</code>`;

  const text = `📋 <b>Field ${stepIdx + 1}/${REVIEW_FIELDS.length}: ${field.label}</b>\n\n` +
    `<b>Fetched Value:</b>\n👉 ${displayVal}\n\n` +
    `Do you want to keep this or edit it manually?`;

  const keyboard = {
    inline_keyboard: [
      [
        { text: '✅ Keep', callback_data: `rev_keep:${stepIdx}` },
        { text: '✏️ Edit', callback_data: `rev_edit:${stepIdx}` }
      ],
      [
        { text: '❌ Cancel', callback_data: 'nav:menu' }
      ]
    ]
  };

  await setSession(fromId, 'ADD_REVIEW_FIELD', { anime, stepIdx }, env.DB);

  if (messageId) {
    return editMessage(chatId, messageId, text, { reply_markup: keyboard }, env);
  }
  return sendMessage(chatId, text, { reply_markup: keyboard }, env);
}

async function promptEditForField(chatId, fromId, anime, stepIdx, env, messageId = null) {
  const field = REVIEW_FIELDS[stepIdx];
  const val = anime[field.key];

  if (field.type === 'text') {
    await setSession(fromId, 'AWAIT_REVIEW_EDIT_TEXT', { anime, stepIdx, fieldKey: field.key }, env.DB);
    const text = `✏️ <b>Edit ${field.label}</b> (${stepIdx + 1}/${REVIEW_FIELDS.length})\n\n` +
      `Current value: <code>${escapeHtml(String(val || 'None'))}</code>\n\n` +
      `Please type the new value below:`;
    const keyboard = {
      inline_keyboard: [
        [{ text: '↩️ Keep Original', callback_data: `rev_keep:${stepIdx}` }],
        [{ text: '❌ Cancel', callback_data: 'nav:menu' }]
      ]
    };
    return messageId ? editMessage(chatId, messageId, text, { reply_markup: keyboard }, env) : sendMessage(chatId, text, { reply_markup: keyboard }, env);
  }

  if (field.type === 'type') {
    await setSession(fromId, 'ADD_REVIEW_FIELD', { anime, stepIdx }, env.DB);
    const buttons = ANIME_TYPES.map(t => ({ text: t === val ? `✅ ${t}` : t, callback_data: `rev_choice:type:${t}` }));
    const keyboard = {
      inline_keyboard: [
        buttons,
        [{ text: '↩️ Keep Original', callback_data: `rev_keep:${stepIdx}` }]
      ]
    };
    const text = `📺 <b>Choose Format Type:</b>\n\nCurrent: <code>${escapeHtml(val)}</code>`;
    return messageId ? editMessage(chatId, messageId, text, { reply_markup: keyboard }, env) : sendMessage(chatId, text, { reply_markup: keyboard }, env);
  }

  if (field.type === 'status') {
    await setSession(fromId, 'ADD_REVIEW_FIELD', { anime, stepIdx }, env.DB);
    const buttons = ANIME_STATUSES.map(s => ({ text: s === val ? `✅ ${s}` : s, callback_data: `rev_choice:status:${s}` }));
    const keyboard = {
      inline_keyboard: [
        buttons,
        [{ text: '↩️ Keep Original', callback_data: `rev_keep:${stepIdx}` }]
      ]
    };
    const text = `📡 <b>Choose Status:</b>\n\nCurrent: <code>${escapeHtml(val)}</code>`;
    return messageId ? editMessage(chatId, messageId, text, { reply_markup: keyboard }, env) : sendMessage(chatId, text, { reply_markup: keyboard }, env);
  }

  if (field.type === 'season') {
    await setSession(fromId, 'ADD_REVIEW_FIELD', { anime, stepIdx }, env.DB);
    const buttons = ANIME_SEASONS.map(s => ({ text: s === val ? `✅ ${s}` : s, callback_data: `rev_choice:season:${s}` }));
    const keyboard = {
      inline_keyboard: [
        buttons,
        [{ text: '↩️ Keep Original', callback_data: `rev_keep:${stepIdx}` }]
      ]
    };
    const text = `🌸 <b>Choose Season:</b>\n\nCurrent: <code>${escapeHtml(val)}</code>`;
    return messageId ? editMessage(chatId, messageId, text, { reply_markup: keyboard }, env) : sendMessage(chatId, text, { reply_markup: keyboard }, env);
  }

  if (field.type === 'episodes') {
    await setSession(fromId, 'ADD_REVIEW_FIELD', { anime, stepIdx }, env.DB);
    const buttons = COMMON_EPISODES.map(ep => ({ text: `${ep} eps`, callback_data: `rev_choice:episodes_count:${ep}` }));
    const keyboard = {
      inline_keyboard: [
        buttons,
        [{ text: '✍️ Type Custom Count', callback_data: `rev_custom:episodes_count:${stepIdx}` }],
        [{ text: '↩️ Keep Original', callback_data: `rev_keep:${stepIdx}` }]
      ]
    };
    const text = `🔢 <b>Choose Episode Count:</b>\n\nCurrent: <code>${escapeHtml(val)}</code>`;
    return messageId ? editMessage(chatId, messageId, text, { reply_markup: keyboard }, env) : sendMessage(chatId, text, { reply_markup: keyboard }, env);
  }

  if (field.type === 'year') {
    await setSession(fromId, 'ADD_REVIEW_FIELD', { anime, stepIdx }, env.DB);
    const buttons = COMMON_YEARS.map(y => ({ text: String(y), callback_data: `rev_choice:year:${y}` }));
    const keyboard = {
      inline_keyboard: [
        buttons,
        [{ text: '✍️ Type Custom Year', callback_data: `rev_custom:year:${stepIdx}` }],
        [{ text: '↩️ Keep Original', callback_data: `rev_keep:${stepIdx}` }]
      ]
    };
    const text = `📅 <b>Choose Release Year:</b>\n\nCurrent: <code>${escapeHtml(val)}</code>`;
    return messageId ? editMessage(chatId, messageId, text, { reply_markup: keyboard }, env) : sendMessage(chatId, text, { reply_markup: keyboard }, env);
  }

  if (field.type === 'identifier') {
    await setSession(fromId, 'ADD_REVIEW_FIELD', { anime, stepIdx }, env.DB);
    const keyboard = {
      inline_keyboard: [
        [{ text: '📦 Keep corrupted_files (Default)', callback_data: 'rev_choice:identifier:corrupted_files' }],
        [{ text: '✍️ Type Custom Identifier', callback_data: `rev_custom_ident:${stepIdx}` }],
        [{ text: '↩️ Keep Original', callback_data: `rev_keep:${stepIdx}` }]
      ]
    };
    const text = `📁 <b>Archive Identifier:</b>\n\nCurrent: <code>${escapeHtml(val)}</code>`;
    return messageId ? editMessage(chatId, messageId, text, { reply_markup: keyboard }, env) : sendMessage(chatId, text, { reply_markup: keyboard }, env);
  }
}

async function showAddConfirmation(chatId, fromId, anime, env, messageId = null) {
  await setSession(fromId, 'CONFIRM_ADD', { anime }, env.DB);
  const card = formatAnimeCard(anime);
  const text = `🎉 <b>Review Complete!</b>\n\n${card}\n\nDo you want to save this anime to Cloudflare D1?`;

  const keyboard = {
    inline_keyboard: [
      [{ text: '💾 Confirm & Save to D1', callback_data: 'save_add' }],
      [{ text: '🔄 Review Again', callback_data: 'rev_restart' }],
      [{ text: '❌ Cancel', callback_data: 'nav:menu' }]
    ]
  };

  if (messageId) {
    return editMessage(chatId, messageId, text, { reply_markup: keyboard }, env);
  }
  return sendMessage(chatId, text, { reply_markup: keyboard }, env);
}

// ==========================================
// EDIT & DELETE FLOWS
// ==========================================
function promptEditChoiceOptions(chatId, animeId, field, env, messageId = null) {
  let buttons = [];
  let title = '';

  if (field === 'type') {
    title = 'Choose new Type:';
    buttons = [ANIME_TYPES.map(t => ({ text: t, callback_data: `set_val:${animeId}:type:${t}` }))];
  } else if (field === 'status') {
    title = 'Choose new Status:';
    buttons = [ANIME_STATUSES.map(s => ({ text: s, callback_data: `set_val:${animeId}:status:${s}` }))];
  } else if (field === 'season') {
    title = 'Choose new Season:';
    buttons = [ANIME_SEASONS.map(s => ({ text: s, callback_data: `set_val:${animeId}:season:${s}` }))];
  } else if (field === 'episodes_count') {
    title = 'Choose Episode Count:';
    buttons = [
      COMMON_EPISODES.map(ep => ({ text: String(ep), callback_data: `set_val:${animeId}:episodes_count:${ep}` })),
      [{ text: '✍️ Type Custom Number', callback_data: `ed_f:${animeId}:episodes_count` }]
    ];
  } else if (field === 'year') {
    title = 'Choose Release Year:';
    buttons = [
      COMMON_YEARS.map(y => ({ text: String(y), callback_data: `set_val:${animeId}:year:${y}` })),
      [{ text: '✍️ Type Custom Year', callback_data: `ed_f:${animeId}:year` }]
    ];
  } else if (field === 'identifier') {
    title = 'Choose Archive Identifier:';
    buttons = [
      [{ text: '📦 corrupted_files (Default)', callback_data: `set_val:${animeId}:identifier:corrupted_files` }],
      [{ text: '✍️ Type Custom Identifier', callback_data: `ed_f:${animeId}:identifier` }]
    ];
  }

  buttons.push([{ text: '🔙 Back to Edit Card', callback_data: `ed_card:${animeId}` }]);
  const text = `✏️ <b>Edit ${field}</b>\n\n${title}`;

  if (messageId) {
    return editMessage(chatId, messageId, text, { reply_markup: { inline_keyboard: buttons } }, env);
  }
  return sendMessage(chatId, text, { reply_markup: { inline_keyboard: buttons } }, env);
}

async function showEditCard(chatId, idOrQuery, env, messageId = null) {
  let anime = null;
  if (/^\d+$/.test(String(idOrQuery).trim())) {
    anime = await getAnimeById(idOrQuery, env.DB);
  }
  if (!anime) {
    const results = await searchAnimeInDb(idOrQuery, env.DB);
    if (results && results.results && results.results.length === 1) {
      anime = await getAnimeById(results.results[0].id, env.DB);
    } else if (results && results.results && results.results.length > 1) {
      const rows = results.results.map(r => [
        { text: `✏️ ${r.title.slice(0, 32)} (${r.year || 'N/A'})`, callback_data: `ed_card:${r.id}` }
      ]);
      rows.push([{ text: '❌ Cancel', callback_data: 'nav:menu' }]);
      const msg = `Found ${results.results.length} matches in D1. Choose one to edit:`;
      return messageId
        ? editMessage(chatId, messageId, msg, { reply_markup: { inline_keyboard: rows } }, env)
        : sendMessage(chatId, msg, { reply_markup: { inline_keyboard: rows } }, env);
    }
  }

  if (!anime) {
    const notFound = `❌ Anime <b>${escapeHtml(idOrQuery)}</b> not found in database.`;
    return messageId
      ? editMessage(chatId, messageId, notFound, { reply_markup: getMainMenuKeyboard() }, env)
      : sendMessage(chatId, notFound, { reply_markup: getMainMenuKeyboard() }, env);
  }

  const card = formatAnimeCard(anime);
  const text = `${card}\n\n<i>Tap any field below to update it:</i>`;
  const keyboard = getEditFieldsKeyboard(anime.id);

  if (messageId) {
    return editMessage(chatId, messageId, text, { reply_markup: keyboard }, env);
  }
  return sendMessage(chatId, text, { reply_markup: keyboard }, env);
}

async function promptDeleteSearch(chatId, idOrQuery, env, messageId = null) {
  let anime = null;
  if (/^\d+$/.test(String(idOrQuery).trim())) {
    anime = await getAnimeById(idOrQuery, env.DB);
  }
  if (!anime) {
    const results = await searchAnimeInDb(idOrQuery, env.DB);
    if (results && results.results && results.results.length === 1) {
      anime = await getAnimeById(results.results[0].id, env.DB);
    } else if (results && results.results && results.results.length > 1) {
      const rows = results.results.map(r => [
        { text: `🗑️ ${r.title.slice(0, 32)} (${r.year || 'N/A'})`, callback_data: `del_ask:${r.id}` }
      ]);
      rows.push([{ text: '❌ Cancel', callback_data: 'nav:menu' }]);
      const msg = `Found ${results.results.length} matches in D1. Choose one to delete:`;
      return messageId
        ? editMessage(chatId, messageId, msg, { reply_markup: { inline_keyboard: rows } }, env)
        : sendMessage(chatId, msg, { reply_markup: { inline_keyboard: rows } }, env);
    }
  }

  if (!anime) {
    const notFound = `❌ Anime <b>${escapeHtml(idOrQuery)}</b> not found in database.`;
    return messageId
      ? editMessage(chatId, messageId, notFound, { reply_markup: getMainMenuKeyboard() }, env)
      : sendMessage(chatId, notFound, { reply_markup: getMainMenuKeyboard() }, env);
  }

  const confirmText = `⚠️ <b>Are you sure you want to permanently delete:</b>\n\n<b>${escapeHtml(anime.title)}</b> (ID: <code>${anime.id}</code>)?\n\nThis cannot be undone.`;
  const confirmKeyboard = {
    reply_markup: {
      inline_keyboard: [
        [{ text: '🚨 Yes, Permanently Delete', callback_data: `del_confirm:${anime.id}` }],
        [{ text: '❌ Cancel', callback_data: 'nav:menu' }]
      ]
    }
  };

  if (messageId) {
    try {
      return await editMessage(chatId, messageId, confirmText, confirmKeyboard, env);
    } catch (e) { }
  }
  return sendMessage(chatId, confirmText, confirmKeyboard, env);
}

async function executeSearchAndRespond(chatId, query, env) {
  if (!query) {
    return sendMessage(chatId, 'Type an anime title or ID to search.', { reply_markup: getMainMenuKeyboard() }, env);
  }
  const results = await searchAnimeInDb(query, env.DB);
  const list = results?.results || [];

  if (list.length === 0) {
    return sendMessage(chatId, `🔍 No anime found for: <b>${escapeHtml(query)}</b>`, {
      reply_markup: {
        inline_keyboard: [
          [{ text: '➕ Add Anime', callback_data: 'menu:add' }],
          [{ text: '🏠 Main Menu', callback_data: 'nav:menu' }]
        ]
      }
    }, env);
  }

  let text = `🔍 <b>Search Results for "${escapeHtml(query)}":</b>\n\n`;
  const buttons = [];

  list.forEach((item, idx) => {
    text += `${idx + 1}. <b>${escapeHtml(item.title)}</b> (ID: <code>${item.id}</code>)\n   ${item.type} | ${item.episodes_count} eps | ${item.year} | ${item.status}\n\n`;
    buttons.push([{ text: `✏️ Edit: ${item.title.slice(0, 26)}`, callback_data: `ed_card:${item.id}` }]);
  });

  buttons.push([{ text: '🏠 Main Menu', callback_data: 'nav:menu' }]);

  return sendMessage(chatId, text, { reply_markup: { inline_keyboard: buttons } }, env);
}

// ==========================================
// POCKETBASE & SUPABASE JSON GENERATION
// ==========================================
async function fetchEpisodesFromPocketBase(animeId, env) {
  const pbUrl = (env.POCKETBASE_URL || 'http://127.0.0.1:8090').replace(/\/$/, '');
  const url = `${pbUrl}/api/collections/completed_syncs/records?filter=(anime_id='${encodeURIComponent(animeId)}')&sort=+episode_number&perPage=500`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`PocketBase returned HTTP ${res.status}`);
  const data = await res.json();
  return data.items || [];
}

function buildAnimeJson(records) {
  const epMap = {};
  for (const r of records) {
    let epNum = parseInt(r.episode_number) || 1;
    if (!epMap[epNum]) {
      epMap[epNum] = {
        episodeNumber: epNum,
        thumbnail: r.thumbnail || '',
        audio: {}
      };
    }
    const lang = (r.language || 'JPN').toUpperCase();
    const qual = (r.quality || '1080p').toLowerCase();
    const streamLink = r.stream_link || '';

    if (!epMap[epNum].audio[lang]) {
      epMap[epNum].audio[lang] = {};
    }
    epMap[epNum].audio[lang][qual] = streamLink;
  }
  const episodes = Object.keys(epMap)
    .sort((a, b) => parseInt(a) - parseInt(b))
    .map(k => epMap[k]);
  return { episodes };
}

async function uploadToSupabase(animeId, jsonPayload, env) {
  if (!env.SUPABASE_URL || !env.SUPABASE_KEY) {
    return { ok: false, msg: 'Supabase credentials not configured in Worker' };
  }
  const bucket = env.SUPABASE_BUCKET || 'episodes';
  const url = `${env.SUPABASE_URL.replace(/\/$/, '')}/storage/v1/object/${bucket}/${animeId}.json`;
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'apikey': env.SUPABASE_KEY,
      'Authorization': `Bearer ${env.SUPABASE_KEY}`,
      'Content-Type': 'application/json',
      'x-upsert': 'true'
    },
    body: JSON.stringify(jsonPayload, null, 2)
  });
  if (res.ok) {
    return { ok: true, msg: `Uploaded to bucket '${bucket}'` };
  } else {
    const errText = await res.text();
    return { ok: false, msg: `HTTP ${res.status}: ${errText}` };
  }
}

async function sendDocument(chatId, filename, contentText, caption, env) {
  const token = env.TELEGRAM_BOT_TOKEN;
  const formData = new FormData();
  formData.append('chat_id', chatId);
  const blob = new Blob([contentText], { type: 'application/json' });
  formData.append('document', blob, filename);
  if (caption) {
    formData.append('caption', caption);
    formData.append('parse_mode', 'HTML');
  }
  return fetch(`https://api.telegram.org/bot${token}/sendDocument`, {
    method: 'POST',
    body: formData
  });
}

async function handleJsonGeneration(chatId, animeId, env) {
  const statusMsg = await sendMessage(chatId, `🔍 Querying episodes for <code>${escapeHtml(animeId)}</code>...`, {}, env);
  try {
    const records = await fetchEpisodesFromPocketBase(animeId, env);
    if (!records || records.length === 0) {
      return editMessage(chatId, statusMsg.result.message_id, `❌ No episodes found in PocketBase for Anime ID: <code>${escapeHtml(animeId)}</code>.`, {
        reply_markup: { inline_keyboard: [[{ text: '🏠 Main Menu', callback_data: 'nav:menu' }]] }
      }, env);
    }

    const payload = buildAnimeJson(records);
    const jsonStr = JSON.stringify(payload, null, 2);

    let sbStatus = 'Not configured';
    if (env.SUPABASE_URL && env.SUPABASE_KEY) {
      const sbRes = await uploadToSupabase(animeId, payload, env);
      sbStatus = sbRes.ok ? '✅ Uploaded & Replaced' : `⚠️ ${sbRes.msg}`;
    }

    const caption = `📦 <b>Anime ID</b>: <code>${escapeHtml(animeId)}</code>\n🎬 <b>Episodes</b>: ${payload.episodes.length}\n☁️ <b>Supabase</b>: ${sbStatus}`;
    await sendDocument(chatId, `${animeId}.json`, jsonStr, caption, env);
    await callTelegram('deleteMessage', { chat_id: chatId, message_id: statusMsg.result.message_id }, env);
  } catch (err) {
    return editMessage(chatId, statusMsg.result.message_id, `❌ Error generating JSON: ${escapeHtml(err.message)}`, {
      reply_markup: { inline_keyboard: [[{ text: '🏠 Main Menu', callback_data: 'nav:menu' }]] }
    }, env);
  }
}