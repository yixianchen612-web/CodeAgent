const questionService = require('../services/questionService');

async function listQuestions(req, res, next) {
  try {
    const questions = await questionService.listQuestions();
    res.json(questions);
  } catch (err) {
    next(err);
  }
}

async function getQuestion(req, res, next) {
  try {
    const id = parseInt(req.params.id, 10);
    if (Number.isNaN(id)) {
      return res.status(400).json({ error: 'Invalid id' });
    }
    const question = await questionService.getQuestion(id);
    res.json(question);
  } catch (err) {
    next(err);
  }
}

async function createQuestion(req, res, next) {
  try {
    const question = await questionService.createQuestion(req.body || {});
    res.status(201).json(question);
  } catch (err) {
    next(err);
  }
}

async function updateQuestion(req, res, next) {
  try {
    const id = parseInt(req.params.id, 10);
    if (Number.isNaN(id)) {
      return res.status(400).json({ error: 'Invalid id' });
    }
    const question = await questionService.updateQuestion(id, req.body || {});
    res.json(question);
  } catch (err) {
    next(err);
  }
}

async function deleteQuestion(req, res, next) {
  try {
    const id = parseInt(req.params.id, 10);
    if (Number.isNaN(id)) {
      return res.status(400).json({ error: 'Invalid id' });
    }
    await questionService.deleteQuestion(id);
    res.status(204).end();
  } catch (err) {
    next(err);
  }
}

module.exports = {
  listQuestions,
  getQuestion,
  createQuestion,
  updateQuestion,
  deleteQuestion,
};
