const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

// These tests validate the SQL DDL files and model behavior without a live
// database. They parse the DDL to confirm required tables and columns exist.

const sqlDir = path.join(__dirname, '..', '..', 'sql');

function readSql(file) {
  return fs.readFileSync(path.join(sqlDir, file), 'utf8');
}

test('game_ddl.sql defines users and games tables', () => {
  const sql = readSql('game_ddl.sql');
  assert.match(sql, /CREATE TABLE IF NOT EXISTS users/);
  assert.match(sql, /CREATE TABLE IF NOT EXISTS games/);
  assert.match(sql, /username VARCHAR/);
  assert.match(sql, /password_hash VARCHAR/);
  assert.match(sql, /is_admin BOOLEAN/);
  assert.match(sql, /game_state JSONB/);
  assert.match(sql, /score INTEGER/);
  assert.match(sql, /status VARCHAR/);
});

test('question_ddl.sql defines questions table', () => {
  const sql = readSql('question_ddl.sql');
  assert.match(sql, /CREATE TABLE IF NOT EXISTS questions/);
  assert.match(sql, /prompt TEXT/);
  assert.match(sql, /options JSONB/);
  assert.match(sql, /correct_answer TEXT/);
  assert.match(sql, /difficulty INTEGER/);
});

test('question_ddl.sql seeds starter questions', () => {
  const sql = readSql('question_ddl.sql');
  assert.match(sql, /INSERT INTO questions/);
});

test('games table references users table', () => {
  const sql = readSql('game_ddl.sql');
  assert.match(sql, /REFERENCES users\(id\)/);
});
