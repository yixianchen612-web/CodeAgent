const { test } = require('node:test');
const assert = require('node:assert');

const questionService = require('../../src/services/questionService');
const questionModel = require('../../src/models/questionModel');

const original = { ...questionModel };

function stubModel() {
  questionModel.listQuestions = async () => [
    {
      id: 1,
      prompt: 'What is 1/2 + 1/4?',
      options: ['1/4', '3/4'],
      correctAnswer: '3/4',
      difficulty: 1,
    },
  ];
  questionModel.getQuestionById = async (id) =>
    id === 1
      ? {
          id: 1,
          prompt: 'What is 1/2 + 1/4?',
          options: ['1/4', '3/4'],
          correctAnswer: '3/4',
          difficulty: 1,
        }
      : null;
  questionModel.createQuestion = async (input) => ({ id: 2, ...input });
  questionModel.updateQuestion = async (id, input) => ({ id, ...input });
  questionModel.deleteQuestion = async (id) => ({ id });
}

function restoreModel() {
  Object.assign(questionModel, original);
}

test('listQuestions returns all questions', async () => {
  stubModel();
  try {
    const questions = await questionService.listQuestions();
    assert.strictEqual(questions.length, 1);
    assert.strictEqual(questions[0].prompt, 'What is 1/2 + 1/4?');
  } finally {
    restoreModel();
  }
});

test('getQuestion returns a question by id', async () => {
  stubModel();
  try {
    const q = await questionService.getQuestion(1);
    assert.strictEqual(q.id, 1);
  } finally {
    restoreModel();
  }
});

test('getQuestion throws 404 for missing question', async () => {
  stubModel();
  try {
    await assert.rejects(() => questionService.getQuestion(999), (err) => {
      assert.strictEqual(err.status, 404);
      return true;
    });
  } finally {
    restoreModel();
  }
});

test('createQuestion requires prompt and correctAnswer', async () => {
  stubModel();
  try {
    await assert.rejects(
      () => questionService.createQuestion({ prompt: 'x' }),
      (err) => {
        assert.strictEqual(err.status, 400);
        return true;
      }
    );
  } finally {
    restoreModel();
  }
});

test('createQuestion returns the created question', async () => {
  stubModel();
  try {
    const q = await questionService.createQuestion({
      prompt: 'Simplify 4/8',
      options: ['1/2', '2/4'],
      correctAnswer: '1/2',
    });
    assert.strictEqual(q.id, 2);
    assert.strictEqual(q.correctAnswer, '1/2');
  } finally {
    restoreModel();
  }
});

test('deleteQuestion throws 404 for missing question', async () => {
  stubModel();
  try {
    await assert.rejects(() => questionService.deleteQuestion(999), (err) => {
      assert.strictEqual(err.status, 404);
      return true;
    });
  } finally {
    restoreModel();
  }
});
