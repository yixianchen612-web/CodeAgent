require('dotenv').config();

const { createApp } = require('./app');
const { applySchema, close } = require('./db');

const PORT = process.env.PORT || 3000;

async function start() {
  try {
    await applySchema();
  } catch (err) {
    console.warn('Could not apply schema (continuing):', err.message);
  }

  const app = createApp();
  const server = app.listen(PORT, () => {
    console.log(`Space Fractions server listening on port ${PORT}`);
  });

  const shutdown = async () => {
    server.close(async () => {
      await close();
      process.exit(0);
    });
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

if (require.main === module) {
  start();
}

module.exports = { start };
