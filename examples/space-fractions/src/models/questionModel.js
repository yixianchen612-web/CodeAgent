const { query } = require('../db');

function mapRow(row) {
  if (!row) return null;
  return {
    id: row.id,
    prompt: row.prompt,
    options: row.options,
    correctAnswer: row.correct_answer,
    difficulty: row.difficulty,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

async function listQuestions() {
  const result = await query(
    `SELECT id, prompt, options, correct_answer, difficulty, created_at, updated_at
     FROM questions ORDER BY id ASC`
  );
  return result.rows.map(mapRow);
}

async function getQuestionById(id) {
  const result = await query(
    `SELECT id, prompt, options, correct_answer, difficulty, created_at, updated_at
     FROM questions WHERE id = $1`,
    [id]
  );
  return mapRow(result.rows[0]);
}

async function getRandomQuestion() {
  const result = await query(
    `SELECT id, prompt, options, correct_answer, difficulty, created_at, updated_at
     FROM questions ORDER BY RANDOM() LIMIT 1`
  );
  return mapRow(result.rows[0]);
}

async function createQuestion({ prompt, options, correctAnswer, difficulty }) {
  const result = await query(
    `INSERT INTO questions (prompt, options, correct_answer, difficulty)
     VALUES ($1, $2, $3, $4)
     RETURNING id, prompt, options, correct_answer, difficulty, created_at, updated_at`,
    [prompt, JSON.stringify(options || []), correctAnswer, difficulty || 1]
  );
  return mapRow(result.rows[0]);
}

async function updateQuestion(id, { prompt, options, correctAnswer, difficulty }) {
  const result = await query(
    `UPDATE questions
     SET prompt = COALESCE($2, prompt),
         options = COALESCE($3, options),
         correct_answer = COALESCE($4, correct_answer),
         difficulty = COALESCE($5, difficulty),
         updated_at = NOW()
     WHERE id = $1
     RETURNING id, prompt, options, correct_answer, difficulty, created_at, updated_at`,
    [
      id,
      prompt,
      options ? JSON.stringify(options) : null,
      correctAnswer,
      difficulty,
    ]
  );
  return mapRow(result.rows[0]);
}

async function deleteQuestion(id) {
  const result = await query(
    `DELETE FROM questions WHERE id = $1 RETURNING id`,
    [id]
  );
  return result.rows[0] || null;
}

module.exports = {
  listQuestions,
  getQuestionById,
  getRandomQuestion,
  createQuestion,
  updateQuestion,
  deleteQuestion,
};
