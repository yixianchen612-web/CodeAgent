// Client-side logic to fetch and display scores.

const scoreBody = document.getElementById('score-body');

async function loadScores() {
  try {
    const scores = await apiFetch('/api/scores');
    scoreBody.innerHTML = '';
    if (!scores || scores.length === 0) {
      scoreBody.innerHTML = '<tr><td colspan="4">No scores yet. Play a game!</td></tr>';
      return;
    }
    scores.forEach((score) => {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${escapeHtml(score.gameId)}</td>
        <td>${escapeHtml(score.score)}</td>
        <td>${escapeHtml(score.status)}</td>
        <td>${escapeHtml(formatDate(score.createdAt))}</td>
      `;
      scoreBody.appendChild(row);
    });
  } catch (err) {
    scoreBody.innerHTML = `<tr><td colspan="4">${escapeHtml(err.message)}</td></tr>`;
  }
}

loadScores();
