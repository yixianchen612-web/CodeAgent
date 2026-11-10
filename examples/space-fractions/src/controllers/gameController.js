const gameService = require('../services/gameService');

async function startGame(req, res, next) {
  try {
    const userId = req.body && req.body.userId ? req.body.userId : null;
    const result = await gameService.startGame(userId);
    res.json(result);
  } catch (err) {
    next(err);
  }
}

async function submitAnswer(req, res, next) {
  try {
    const gameId = parseInt(req.params.gameId, 10);
    if (Number.isNaN(gameId)) {
      return res.status(400).json({ error: 'Invalid gameId' });
    }
    const { answer } = req.body || {};
    if (answer == null || answer === '') {
      return res.status(400).json({ error: 'answer is required' });
    }
    const result = await gameService.submitAnswer(gameId, answer);
    res.json(result);
  } catch (err) {
    next(err);
  }
}

async function getScore(req, res, next) {
  try {
    const gameId = parseInt(req.params.gameId, 10);
    if (Number.isNaN(gameId)) {
      return res.status(400).json({ error: 'Invalid gameId' });
    }
    const result = await gameService.getScore(gameId);
    res.json(result);
  } catch (err) {
    next(err);
  }
}

async function listScores(req, res, next) {
  try {
    const limit = parseInt(req.query.limit, 10) || 10;
    const result = await gameService.listScores(limit);
    res.json(result);
  } catch (err) {
    next(err);
  }
}

module.exports = { startGame, submitAnswer, getScore, listScores };
