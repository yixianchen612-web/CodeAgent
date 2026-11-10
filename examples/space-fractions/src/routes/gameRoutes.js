const express = require('express');
const gameController = require('../controllers/gameController');

const router = express.Router();

router.post('/play', gameController.startGame);
router.post('/play/:gameId/answer', gameController.submitAnswer);
router.get('/score/:gameId', gameController.getScore);
router.get('/scores', gameController.listScores);

module.exports = router;
