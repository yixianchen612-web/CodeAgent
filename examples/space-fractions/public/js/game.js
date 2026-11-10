// Client-side logic for gameplay, answer submission, and score updates.

const TOTAL_QUESTIONS = 5;
let gameId = null;
let questionNumber = 1;
let currentScore = 0;

const scoreEl = document.getElementById('score');
const questionNumberEl = document.getElementById('question-number');
const promptEl = document.getElementById('prompt');
const optionsEl = document.getElementById('options');
const feedbackEl = document.getElementById('feedback');
const gameOverEl = document.getElementById('game-over');
const finalScoreEl = document.getElementById('final-score');
const playAgainBtn = document.getElementById('play-again');

async function startGame() {
  try {
    const data = await apiFetch('/api/play', { method: 'POST', body: {} });
    gameId = data.gameId;
    currentScore = data.score || 0;
    questionNumber = 1;
    updateScoreBar();
    renderQuestion(data.prompt, data.options);
    feedbackEl.textContent = '';
    feedbackEl.className = 'feedback';
    gameOverEl.classList.add('hidden');
  } catch (err) {
    promptEl.textContent = 'Could not start game.';
    feedbackEl.textContent = err.message;
  }
}

function renderQuestion(prompt, options) {
  promptEl.textContent = prompt || 'No question available.';
  optionsEl.innerHTML = '';
  (options || []).forEach((option) => {
    const button = document.createElement('button');
    button.className = 'option-button';
    button.textContent = option;
    button.addEventListener('click', () => submitAnswer(option));
    optionsEl.appendChild(button);
  });
}

async function submitAnswer(answer) {
  if (!gameId) return;
  disableOptions(true);
  try {
    const data = await apiFetch(`/api/play/${gameId}/answer`, {
      method: 'POST',
      body: { answer },
    });
    currentScore = data.score;
    updateScoreBar();

    // Highlight the chosen option
    const buttons = optionsEl.querySelectorAll('.option-button');
    buttons.forEach((btn) => {
      if (btn.textContent === answer) {
        btn.classList.add(data.correct ? 'correct' : 'incorrect');
      }
    });

    if (data.correct) {
      feedbackEl.textContent = '✅ Correct!';
      feedbackEl.className = 'feedback correct';
    } else {
      feedbackEl.textContent = '❌ Incorrect.';
      feedbackEl.className = 'feedback incorrect';
    }

    if (data.gameOver) {
      endGame();
    } else {
      questionNumber += 1;
      updateScoreBar();
      setTimeout(() => {
        renderQuestion(data.nextPrompt, data.nextOptions);
        feedbackEl.textContent = '';
        feedbackEl.className = 'feedback';
        disableOptions(false);
      }, 900);
    }
  } catch (err) {
    feedbackEl.textContent = err.message;
    disableOptions(false);
  }
}

function endGame() {
  finalScoreEl.textContent = currentScore;
  gameOverEl.classList.remove('hidden');
  optionsEl.innerHTML = '';
  promptEl.textContent = '';
}

function updateScoreBar() {
  scoreEl.textContent = currentScore;
  questionNumberEl.textContent = Math.min(questionNumber, TOTAL_QUESTIONS);
}

function disableOptions(disabled) {
  optionsEl.querySelectorAll('.option-button').forEach((btn) => {
    btn.disabled = disabled;
  });
}

playAgainBtn.addEventListener('click', startGame);

startGame();
