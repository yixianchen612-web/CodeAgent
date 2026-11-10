const { test } = require('node:test');
const assert = require('node:assert');

const gameService = require('../../src/services/gameService');
const gameModel = require('../../src/models/gameModel');
const questionModel = require('../../src/models/questionModel');

// Stub the model layer so tests run without a database.
const originalGameModel = { ...gameModel };
const originalQuestionModel = { ...questionModel };

function stubModels() {
  gameModel.createGame = async (userId) => ({
    id: 1,
    user_id: userId,
    game_state: {},
    score: 0,
    status: 'playing',
  });
  gameModel.getGameById = async (id) => ({
    id,
    user_id: null,
    game_state: { answered: 0, correct: 0 },
    score: 0,
    status: 'playing',
  });
  gameModel.updateGame = async (id, fields) => ({
    id,
    game_state: fields.gameState || {},
    score: fields.score || 0,
    status: fields.status || 'playing',
  });
  gameModel.listRecentScores = async () => [
    { id: 1, user_id: null, score: 20, status: 'gameover', created_at: new Date() },
  ];
  questionModel.getRandomQuestion = async () => ({
    id: 1,
    prompt: 'What is 1/2 + 1/4?',
    options: ['1/4', '3/4', '2/6', '1/8'],
    correctAnswer: '3/4',
    difficulty: 1,
  });
}

function restoreModels() {
  Object.assign(gameModel, originalGameModel);
  Object.assign(questionModel, originalQuestionModel);
}

test('startGame returns a game with a prompt and options', async () => {
  stubModels();
  try {
    const result = await gameService.startGame();
    assert.strictEqual(result.gameId, 1);
    assert.strictEqual(result.score, 0);
    assert.strictEqual(result.status, 'playing');
    assert.ok(result.prompt);
    assert.ok(Array.isArray(result.options));
  } finally {
    restoreModels();
  }
});

test('submitAnswer marks a correct answer and increments score', async () => {
  stubModels();
  try {
    const result = await gameService.submitAnswer(1, '3/4');
    assert.strictEqual(result.correct, true);
    assert.strictEqual(result.score, 10);
    assert.strictEqual(result.gameOver, false);
  } finally {
    restoreModels();
  }
});

test('submitAnswer marks an incorrect answer without incrementing score', async () => {
  stubModels();
  try {
    const result = await gameService.submitAnswer(1, '1/4');
    assert.strictEqual(result.correct, false);
    assert.strictEqual(result.score, 0);
  } finally {
    restoreModels();
  }
});

test('submitAnswer throws 404 for a missing game', async () => {
  stubModels();
  gameModel.getGameById = async () => null;
  try {
    await assert.rejects(() => gameService.submitAnswer(999, '3/4'), (err) => {
      assert.strictEqual(err.status, 404);
      return true;
    });
  } finally {
    restoreModels();
  }
});

test('getScore returns score and status', async () => {
  stubModels();
  try {
    const result = await gameService.getScore(1);
    assert.strictEqual(result.gameId, 1);
    assert.strictEqual(result.score, 0);
    assert.strictEqual(result.status, 'playing');
  } finally {
    restoreModels();
  }
});

test('normalize trims, lowercases, and removes whitespace', () => {
  assert.strictEqual(gameService.normalize(' 3/4 '), '3/4');
  assert.strictEqual(gameService.normalize(' 3 / 4 '), '3/4');
  assert.strictEqual(gameService.normalize('ABC'), 'abc');
});
