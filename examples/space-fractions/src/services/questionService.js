const questionModel = require('../models/questionModel');

async function listQuestions() {
  return questionModel.listQuestions();
}

async function getQuestion(id) {
  const question = await questionModel.getQuestionById(id);
  if (!question) {
    const error = new Error('Question not found');
    error.status = 404;
    throw error;
  }
  return question;
}

async function createQuestion(input) {
  if (!input.prompt || !input.correctAnswer) {
    const error = new Error('prompt and correctAnswer are required');
    error.status = 400;
    throw error;
  }
  return questionModel.createQuestion(input);
}

async function updateQuestion(id, input) {
  const existing = await questionModel.getQuestionById(id);
  if (!existing) {
    const error = new Error('Question not found');
    error.status = 404;
    throw error;
  }
  return questionModel.updateQuestion(id, input);
}

async function deleteQuestion(id) {
  const existing = await questionModel.getQuestionById(id);
  if (!existing) {
    const error = new Error('Question not found');
    error.status = 404;
    throw error;
  }
  return questionModel.deleteQuestion(id);
}

module.exports = {
  listQuestions,
  getQuestion,
  createQuestion,
  updateQuestion,
  deleteQuestion,
};
