const { query } = require('../db');

async function createGame(userId = null) {
  const result = await query(
    `INSERT INTO games (user_id, game_state, score, status)
     VALUES ($1, $2, $3, $4)
     RETURNING id, user_id, game_state, score, status, created_at, updated_at`,
    [userId, JSON.stringify({}), 0, 'playing']
  );
  return result.rows[0];
}

async function getGameById(id) {
  const result = await query(
    `SELECT id, user_id, game_state, score, status, created_at, updated_at
     FROM games WHERE id = $1`,
    [id]
  );
  return result.rows[0] || null;
}

async function updateGame(id, { gameState, score, status }) {
  const result = await query(
    `UPDATE games
     SET game_state = COALESCE($2, game_state),
         score = COALESCE($3, score),
         status = COALESCE($4, status),
         updated_at = NOW()
     WHERE id = $1
     RETURNING id, user_id, game_state, score, status, created_at, updated_at`,
    [id, gameState ? JSON.stringify(gameState) : null, score, status]
  );
  return result.rows[0] || null;
}

async function listRecentScores(limit = 10) {
  const result = await query(
    `SELECT id, user_id, score, status, created_at
     FROM games
     ORDER BY created_at DESC
     LIMIT $1`,
    [limit]
  );
  return result.rows;
}

module.exports = { createGame, getGameById, updateGame, listRecentScores };
