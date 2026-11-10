const express = require('express');
const userController = require('../controllers/userController');

const router = express.Router();

router.post('/users/register', userController.register);
router.post('/users/login', userController.login);

module.exports = router;
