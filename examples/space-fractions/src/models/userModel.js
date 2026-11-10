const crypto = require('crypto');
const { query } = require('../db');

function hashPassword(password) {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto
    .pbkdf2Sync(password, salt, 10000, 64, 'sha512')
    .toString('hex');
  return `${salt}:${hash}`;
}

function verifyPassword(password, stored) {
  const [salt, hash] = stored.split(':');
  const candidate = crypto
    .pbkdf2Sync(password, salt, 10000, 64, 'sha512')
    .toString('hex');
  return candidate === hash;
}

function mapRow(row) {
  if (!row) return null;
  return {
    id: row.id,
    username: row.username,
    isAdmin: row.is_admin,
    createdAt: row.created_at,
  };
}

async function createUser(username, password, isAdmin = false) {
  const passwordHash = hashPassword(password);
  const result = await query(
    `INSERT INTO users (username, password_hash, is_admin)
     VALUES ($1, $2, $3)
     RETURNING id, username, is_admin, created_at`,
    [username, passwordHash, isAdmin]
  );
  return mapRow(result.rows[0]);
}

async function getUserByUsername(username) {
  const result = await query(
    `SELECT id, username, password_hash, is_admin, created_at
     FROM users WHERE username = $1`,
    [username]
  );
  return result.rows[0] || null;
}

async function getUserById(id) {
  const result = await query(
    `SELECT id, username, password_hash, is_admin, created_at
     FROM users WHERE id = $1`,
    [id]
  );
  return result.rows[0] || null;
}

module.exports = {
  hashPassword,
  verifyPassword,
  createUser,
  getUserByUsername,
  getUserById,
};
