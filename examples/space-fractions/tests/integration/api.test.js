const { test, after } = require('node:test');
const assert = require('node:assert');

const gameController = require('../../src/controllers/gameController');
const questionController = require('../../src/controllers/questionController');
const userController = require('../../src/controllers/userController');
const gameService = require('../../src/services/gameService');
const questionService = require('../../src/services/questionService');
const userModel = require('../../src/models/userModel');

const originalGameService = { ...gameService };
const originalQuestionService = { ...questionService };
const originalUserModel = { ...userModel };

after(() => {
  Object.assign(gameService, originalGameService);
  Object.assign(questionService, originalQuestionService);
  Object.assign(userModel, originalUserModel);
});

// Minimal mock of an Express response object.
function mockRes() {
  const res = {
    statusCode: 200,
    body: null,
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(data) {
      this.body = data;
      return this;
    },
    end() {
      this.ended = true;
      return this;
    },
  };
  return res;
}

function mockReq(params = {}, body = {}, query = {}) {
  return { params, body, query };
}

test('gameController.startGame returns a game', async () => {
  gameService.startGame = async () => ({
    gameId: 1,
    score: 0,
    status: 'playing',
    prompt: 'What is 1/2 + 1/4?',
    options: ['1/4', '3/4'],
  });
  const res = mockRes();
  await gameController.startGame(mockReq(), res, () => {});
  assert.strictEqual(res.statusCode, 200);
  assert.strictEqual(res.body.gameId, 1);
});

test('gameController.submitAnswer validates answer', async () => {
  const res = mockRes();
  await gameController.submitAnswer(mockReq({ gameId: '1' }, {}), res, () => {});
  assert.strictEqual(res.statusCode, 400);
  assert.ok(res.body.error);
});

test('gameController.submitAnswer submits an answer', async () => {
  gameService.submitAnswer = async () => ({
    correct: true,
    score: 10,
    gameOver: false,
    nextPrompt: 'Simplify 4/8',
    nextOptions: ['1/2', '2/4'],
  });
  const res = mockRes();
  await gameController.submitAnswer(
    mockReq({ gameId: '1' }, { answer: '3/4' }),
    res,
    () => {}
  );
  assert.strictEqual(res.statusCode, 200);
  assert.strictEqual(res.body.correct, true);
  assert.strictEqual(res.body.score, 10);
});

test('gameController.getScore returns score', async () => {
  gameService.getScore = async () => ({
    gameId: 1,
    score: 10,
    status: 'playing',
    answered: 1,
    correct: 1,
  });
  const res = mockRes();
  await gameController.getScore(mockReq({ gameId: '1' }), res, () => {});
  assert.strictEqual(res.statusCode, 200);
  assert.strictEqual(res.body.score, 10);
});

test('gameController.listScores lists scores', async () => {
  gameService.listScores = async () => [
    { gameId: 1, userId: null, score: 10, status: 'gameover', createdAt: new Date() },
  ];
  const res = mockRes();
  await gameController.listScores(mockReq({}, {}, {}), res, () => {});
  assert.strictEqual(res.statusCode, 200);
  assert.strictEqual(res.body.length, 1);
});

test('questionController.listQuestions lists questions', async () => {
  questionService.listQuestions = async () => [
    {
      id: 1,
      prompt: 'What is 1/2 + 1/4?',
      options: ['1/4', '3/4'],
      correctAnswer: '3/4',
      difficulty: 1,
    },
  ];
  const res = mockRes();
  await questionController.listQuestions(mockReq(), res, () => {});
  assert.strictEqual(res.statusCode, 200);
  assert.strictEqual(res.body.length, 1);
});

test('questionController.createQuestion creates a question', async () => {
  questionService.createQuestion = async (input) => ({ id: 2, ...input });
  const res = mockRes();
  await questionController.createQuestion(
    mockReq({}, { prompt: 'Simplify 4/8', options: ['1/2'], correctAnswer: '1/2' }),
    res,
    () => {}
  );
  assert.strictEqual(res.statusCode, 201);
  assert.strictEqual(res.body.id, 2);
});

test('questionController.updateQuestion updates a question', async () => {
  questionService.updateQuestion = async (id, input) => ({ id, ...input });
  const res = mockRes();
  await questionController.updateQuestion(
    mockReq({ id: '1' }, { prompt: 'Updated', options: ['a'], correctAnswer: 'a' }),
    res,
    () => {}
  );
  assert.strictEqual(res.statusCode, 200);
  assert.strictEqual(res.body.prompt, 'Updated');
});

test('questionController.deleteQuestion deletes a question', async () => {
  questionService.deleteQuestion = async () => ({ id: 1 });
  const res = mockRes();
  await questionController.deleteQuestion(mockReq({ id: '1' }), res, () => {});
  assert.strictEqual(res.statusCode, 204);
  assert.strictEqual(res.ended, true);
});

test('userController.register registers a user', async () => {
  userModel.getUserByUsername = async () => null;
  userModel.createUser = async (username) => ({ id: 1, username, isAdmin: false });
  const res = mockRes();
  await userController.register(
    mockReq({}, { username: 'player1', password: 'secret' }),
    res,
    () => {}
  );
  assert.strictEqual(res.statusCode, 201);
  assert.strictEqual(res.body.username, 'player1');
});

test('userController.login rejects bad credentials', async () => {
  userModel.getUserByUsername = async () => null;
  const res = mockRes();
  await userController.login(
    mockReq({}, { username: 'nobody', password: 'wrong' }),
    res,
    () => {}
  );
  assert.strictEqual(res.statusCode, 401);
});
