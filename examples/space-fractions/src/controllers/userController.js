const userModel = require('../models/userModel');

async function register(req, res, next) {
  try {
    const { username, password } = req.body || {};
    if (!username || !password) {
      return res.status(400).json({ error: 'username and password are required' });
    }
    const existing = await userModel.getUserByUsername(username);
    if (existing) {
      return res.status(409).json({ error: 'Username already taken' });
    }
    const user = await userModel.createUser(username, password, false);
    res.status(201).json(user);
  } catch (err) {
    next(err);
  }
}

async function login(req, res, next) {
  try {
    const { username, password } = req.body || {};
    if (!username || !password) {
      return res.status(400).json({ error: 'username and password are required' });
    }
    const user = await userModel.getUserByUsername(username);
    if (!user || !userModel.verifyPassword(password, user.password_hash)) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    res.json({
      id: user.id,
      username: user.username,
      isAdmin: user.is_admin,
    });
  } catch (err) {
    next(err);
  }
}

module.exports = { register, login };
