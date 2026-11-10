const gameModel = require('../models/gameModel');
const questionModel = require('../models/questionModel');

const QUESTIONS_PER_GAME = 5;

async function startGame(userId = null) {
  const game = await gameModel.createGame(userId);
  const question = await questionModel.getRandomQuestion();

  const gameState = {
    questionIndex: 0,
    totalQuestions: QUESTIONS_PER_GAME,
    answered: 0,
    correct: 0,
  };

  await gameModel.updateGame(game.id, { gameState, score: 0, status: 'playing' });

  return {
    gameId: game.id,
    score: 0,
    status: 'playing',
    prompt: question ? question.prompt : null,
    options: question ? question.options : [],
  };
}

async function submitAnswer(gameId, answer) {
  const game = await gameModel.getGameById(gameId);
  if (!game) {
    const error = new Error('Game not found');
    error.status = 404;
    throw error;
  }
  if (game.status !== 'playing') {
    const error = new Error('Game is not active');
    error.status = 400;
    throw error;
  }

  const question = await questionModel.getRandomQuestion();
  const correct = question && normalize(answer) === normalize(question.correctAnswer);

  const gameState = game.game_state || {};
  const answered = (gameState.answered || 0) + 1;
  const correctCount = (gameState.correct || 0) + (correct ? 1 : 0);
  const score = correctCount * 10;
  const gameOver = answered >= QUESTIONS_PER_GAME;

  const nextState = {
    ...gameState,
    answered,
    correct: correctCount,
  };

  await gameModel.updateGame(gameId, {
    gameState: nextState,
    score,
    status: gameOver ? 'gameover' : 'playing',
  });

  const nextQuestion = gameOver ? null : await questionModel.getRandomQuestion();

  return {
    correct,
    score,
    gameOver,
    nextPrompt: nextQuestion ? nextQuestion.prompt : null,
    nextOptions: nextQuestion ? nextQuestion.options : [],
  };
}

async function getScore(gameId) {
  const game = await gameModel.getGameById(gameId);
  if (!game) {
    const error = new Error('Game not found');
    error.status = 404;
    throw error;
  }
  return {
    gameId: game.id,
    score: game.score,
    status: game.status,
    answered: (game.game_state || {}).answered || 0,
    correct: (game.game_state || {}).correct || 0,
  };
}

async function listScores(limit = 10) {
  const rows = await gameModel.listRecentScores(limit);
  return rows.map((row) => ({
    gameId: row.id,
    userId: row.user_id,
    score: row.score,
    status: row.status,
    createdAt: row.created_at,
  }));
}

function normalize(value) {
  if (value == null) return '';
  return String(value).trim().toLowerCase().replace(/\s+/g, '');
}

module.exports = { startGame, submitAnswer, getScore, listScores, normalize };
