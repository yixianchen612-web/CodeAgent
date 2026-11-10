const fs = require('fs');
const path = require('path');

let pool = null;
let Pool = null;

function getPool() {
  if (!pool) {
    // Lazy-load pg so that unit/integration tests can run without the
    // database driver installed.
    if (!Pool) {
      Pool = require('pg').Pool;
    }
    const connectionString =
      process.env.DATABASE_URL ||
      'postgres://postgres:postgres@localhost:5432/spacefractions';
    pool = new Pool({ connectionString });
  }
  return pool;
}

async function query(text, params) {
  return getPool().query(text, params);
}

async function applySchema() {
  const sqlDir = path.join(__dirname, '..', 'sql');
  const files = ['game_ddl.sql', 'question_ddl.sql'];
  for (const file of files) {
    const filePath = path.join(sqlDir, file);
    if (!fs.existsSync(filePath)) {
      continue;
    }
    const sql = fs.readFileSync(filePath, 'utf8');
    await query(sql);
  }
}

async function close() {
  if (pool) {
    await pool.end();
    pool = null;
  }
}

module.exports = { getPool, query, applySchema, close };
