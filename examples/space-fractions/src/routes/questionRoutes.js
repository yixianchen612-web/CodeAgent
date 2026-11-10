const express = require('express');
const questionController = require('../controllers/questionController');

const router = express.Router();

router.get('/questions', questionController.listQuestions);
router.get('/questions/:id', questionController.getQuestion);
router.post('/questions', questionController.createQuestion);
router.put('/questions/:id', questionController.updateQuestion);
router.delete('/questions/:id', questionController.deleteQuestion);

module.exports = router;
