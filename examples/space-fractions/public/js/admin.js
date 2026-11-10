// Client-side logic for admin question management.

const questionBody = document.getElementById('question-body');
const formTitle = document.getElementById('form-title');
const questionIdEl = document.getElementById('question-id');
const promptEl = document.getElementById('prompt');
const optionsEl = document.getElementById('options');
const correctAnswerEl = document.getElementById('correct-answer');
const difficultyEl = document.getElementById('difficulty');
const saveBtn = document.getElementById('save-question');
const cancelBtn = document.getElementById('cancel-edit');

async function loadQuestions() {
  try {
    const questions = await apiFetch('/api/questions');
    questionBody.innerHTML = '';
    if (!questions || questions.length === 0) {
      questionBody.innerHTML = '<tr><td colspan="4">No questions yet.</td></tr>';
      return;
    }
    questions.forEach((q) => {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${escapeHtml(q.id)}</td>
        <td>${escapeHtml(q.prompt)}</td>
        <td>${escapeHtml(q.correctAnswer)}</td>
        <td>
          <button class="action-button edit" data-id="${q.id}">Edit</button>
          <button class="action-button delete" data-id="${q.id}">Delete</button>
        </td>
      `;
      row.querySelector('.edit').addEventListener('click', () => editQuestion(q));
      row.querySelector('.delete').addEventListener('click', () => deleteQuestion(q.id));
      questionBody.appendChild(row);
    });
  } catch (err) {
    questionBody.innerHTML = `<tr><td colspan="4">${escapeHtml(err.message)}</td></tr>`;
  }
}

function editQuestion(q) {
  questionIdEl.value = q.id;
  promptEl.value = q.prompt;
  optionsEl.value = (q.options || []).join(', ');
  correctAnswerEl.value = q.correctAnswer;
  difficultyEl.value = q.difficulty;
  formTitle.textContent = 'Edit Question';
  cancelBtn.classList.remove('hidden');
}

function resetForm() {
  questionIdEl.value = '';
  promptEl.value = '';
  optionsEl.value = '';
  correctAnswerEl.value = '';
  difficultyEl.value = '1';
  formTitle.textContent = 'Add Question';
  cancelBtn.classList.add('hidden');
}

async function saveQuestion() {
  const payload = {
    prompt: promptEl.value.trim(),
    options: optionsEl.value.split(',').map((s) => s.trim()).filter(Boolean),
    correctAnswer: correctAnswerEl.value.trim(),
    difficulty: parseInt(difficultyEl.value, 10) || 1,
  };
  if (!payload.prompt || !payload.correctAnswer) {
    alert('Prompt and correct answer are required.');
    return;
  }
  try {
    const id = questionIdEl.value;
    if (id) {
      await apiFetch(`/api/questions/${id}`, { method: 'PUT', body: payload });
    } else {
      await apiFetch('/api/questions', { method: 'POST', body: payload });
    }
    resetForm();
    await loadQuestions();
  } catch (err) {
    alert(err.message);
  }
}

async function deleteQuestion(id) {
  if (!confirm('Delete this question?')) return;
  try {
    await apiFetch(`/api/questions/${id}`, { method: 'DELETE' });
    await loadQuestions();
  } catch (err) {
    alert(err.message);
  }
}

saveBtn.addEventListener('click', saveQuestion);
cancelBtn.addEventListener('click', resetForm);

loadQuestions();
