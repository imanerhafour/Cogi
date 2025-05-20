document.addEventListener('DOMContentLoaded', function () {
  // ========== Navigation active ==========
  const currentPath = window.location.pathname;
  const navLinks = document.querySelectorAll('.navbar-nav .nav-link');
  navLinks.forEach(link => {
    link.classList.toggle('active', link.getAttribute('href') === currentPath);
  });

  // ========== Logo fallback ==========
  createLogoIfMissing();

  // ========== Micro et reconnaissance vocale ==========
  const micBtn = document.getElementById('micBtn');
  const userInput = document.getElementById('user-input');
  const sendBtn = document.getElementById('send-btn');
  const chatBox = document.getElementById('chat-box');

  const voiceStatus = document.createElement('div');
  voiceStatus.id = 'voice-status';
  voiceStatus.className = 'voice-status';
  voiceStatus.textContent = 'Micro: inactif';
  micBtn.parentNode.insertBefore(voiceStatus, micBtn.nextSibling);

  let recognition;

  if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'fr-FR';

    micBtn.addEventListener('click', function () {
      if (micBtn.classList.contains('recording')) {
        recognition.stop();
      } else {
        try {
          recognition.start();
          voiceStatus.textContent = "Micro: écoute en cours...";
          voiceStatus.classList.add('voice-active');
          micBtn.innerHTML = '<i class="bi bi-mic-fill"></i>';
          micBtn.classList.add('recording');
        } catch (error) {
          voiceStatus.textContent = "Erreur: " + error.message;
        }
      }
    });

    recognition.onresult = function (event) {
      const transcript = event.results[0][0].transcript;
      userInput.value = transcript;

      if (sendBtn) sendBtn.click();
      resetMicUI();
    };

    recognition.onerror = function (event) {
      voiceStatus.textContent = `Erreur: ${event.error}`;
      resetMicUI();
    };

    recognition.onend = function () {
      if (!micBtn.classList.contains('recording')) return;
      resetMicUI();
    };

    function resetMicUI() {
      voiceStatus.textContent = "Micro: inactif";
      voiceStatus.classList.remove('voice-active');
      micBtn.innerHTML = '<i class="bi bi-mic"></i>';
      micBtn.classList.remove('recording');
    }
  } else {
    micBtn.disabled = true;
    voiceStatus.textContent = "Micro: non supporté";
    micBtn.title = "Reconnaissance vocale non supportée par votre navigateur";
  }

  // ========== Envoi de message à Flask ==========
  function displayMessage(text, sender) {
    const msg = document.createElement('div');
    msg.className = `message ${sender}`;
    msg.innerHTML = `<p>${text}</p><span class="timestamp">${formatTimestamp()}</span>`;
    chatBox.appendChild(msg);
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  sendBtn.addEventListener('click', function (e) {
    e.preventDefault();
    const message = userInput.value.trim();
    if (!message) return;

    displayMessage(message, 'user');
    userInput.value = '';

    fetch('/send_message', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ message: message })
    })
      .then(response => response.json())
      .then(data => {
        console.log('Réponse API :', data);
        const botReply = data.reply || "❌ Erreur : réponse vide";
        displayMessage(botReply, 'bot');
      })
      .catch(error => {
        console.error('Erreur fetch :', error);
        displayMessage("⚠️ Erreur serveur", 'bot');
      });
  });
});

// ========== Logo fallback ==========
function createLogoIfMissing() {
  const logoImg = document.querySelector('.navbar-brand img');
  if (logoImg) {
    logoImg.onerror = function () {
      this.style.display = 'none';
      const brand = document.querySelector('.navbar-brand');
      brand.innerHTML = 'Cogi <span class="badge bg-light text-success">AI</span>';
    };
  }
}

// ========== Timestamp pour messages ==========
function formatTimestamp() {
  const now = new Date();
  const hours = now.getHours().toString().padStart(2, '0');
  const minutes = now.getMinutes().toString().padStart(2, '0');
  return `${hours}:${minutes}`;
}

// ========== Fonction scroll  ==========
function scrollFeedbacks(direction) {
  const container = document.getElementById('feedbackContainer');
  const scrollAmount = 320; // Ajuste selon la taille d'une carte
  container.scrollBy({
    left: direction * scrollAmount,
    behavior: 'smooth'
  });
}
