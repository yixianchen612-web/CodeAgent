-- Space Fractions: questions schema
-- Run with: psql -U postgres -d spacefractions -f sql/question_ddl.sql

CREATE TABLE IF NOT EXISTS questions (
  id SERIAL PRIMARY KEY,
  prompt TEXT NOT NULL,
  options JSONB NOT NULL DEFAULT '[]'::jsonb,
  correct_answer TEXT NOT NULL,
  difficulty INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty);

-- Seed a few starter questions so the game is playable out of the box.
INSERT INTO questions (prompt, options, correct_answer, difficulty)
SELECT 'What is 1/2 + 1/4?', '["1/4", "3/4", "2/6", "1/8"]'::jsonb, '3/4', 1
WHERE NOT EXISTS (SELECT 1 FROM questions WHERE prompt = 'What is 1/2 + 1/4?');

INSERT INTO questions (prompt, options, correct_answer, difficulty)
SELECT 'What is 2/3 of 9?', '["3", "6", "4", "9"]'::jsonb, '6', 2
WHERE NOT EXISTS (SELECT 1 FROM questions WHERE prompt = 'What is 2/3 of 9?');

INSERT INTO questions (prompt, options, correct_answer, difficulty)
SELECT 'Simplify 4/8', '["1/2", "2/4", "4/8", "1/4"]'::jsonb, '1/2', 1
WHERE NOT EXISTS (SELECT 1 FROM questions WHERE prompt = 'Simplify 4/8');
