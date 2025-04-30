document.getElementById("analyze-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = document.getElementById("url-input").value.trim();
  if (!url) return;

  // Show loader and hide results
  document.getElementById("loader").style.display = "block";
  document.getElementById("result").style.display = "none";

  try {
    const res = await fetch("/api/compare-news", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });

    const data = await res.json();

    if (data.error) {
      showAlert(data.error, 'error');
      return;
    }

    // Update UI with analysis results
    document.getElementById("article-title").textContent = data.articleTitle || "Untitled Article";
    document.getElementById("article-date").textContent = formatDate(data.articleDate) || "Unknown date";
    document.getElementById("article-source").textContent = data.source || "Unknown source";
    document.getElementById("bias-score").textContent = data.biasScore || "0";
    
    // Apply bias score color
    updateBiasScoreColor(data.biasScore);
    
    document.getElementById("original-text").innerHTML = 
      data.originalText?.trim()?.replace(/\n/g, '<br>') || 
      "<em>Content not available</em>";
    
    document.getElementById("rewritten-text").innerHTML = 
      data.rewritten?.trim()?.replace(/\n/g, '<br>') || 
      "<em>Could not generate neutral version</em>";
    
    // Highlight biased words if available
    if (data.redlined_text) {
      document.getElementById("original-text").innerHTML = 
        highlightBiasedText(data.redlined_text);
    }

    // Show result section
    document.getElementById("result").style.display = "block";
    
  } catch (err) {
    console.error("Error:", err);
    showAlert("Failed to analyze article. Please try again.", 'error');
  } finally {
    document.getElementById("loader").style.display = "none";
  }
});

function updateBiasScoreColor(score) {
  const biasScoreElement = document.getElementById("bias-score");
  if (!biasScoreElement) return;
  
  score = parseInt(score) || 0;
  
  // Remove all color classes
  biasScoreElement.className = 'bias-score';
  
  // Add appropriate color class
  if (score < 30) {
    biasScoreElement.style.background = 'linear-gradient(135deg, #4cc9f0, #4895ef)';
  } else if (score < 70) {
    biasScoreElement.style.background = 'linear-gradient(135deg, #f8961e, #f3722c)';
  } else {
    biasScoreElement.style.background = 'linear-gradient(135deg, #f72585, #7209b7)';
  }
}

function highlightBiasedText(text) {
  if (!text) return '';
  // Replace [BIAS] tags with highlighting
  return text
    .replace(/\[BIAS\]/g, '<span class="bias-highlight">')
    .replace(/\[\/BIAS\]/g, '</span>')
    .replace(/\n/g, '<br>');
}

function showAlert(message, type = 'info') {
  const alert = document.createElement('div');
  alert.className = `alert alert-${type}`;
  alert.textContent = message;
  alert.style.position = 'fixed';
  alert.style.top = '20px';
  alert.style.right = '20px';
  alert.style.padding = '12px 20px';
  alert.style.background = type === 'error' ? '#f72585' : '#4361ee';
  alert.style.color = 'white';
  alert.style.borderRadius = '8px';
  alert.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
  alert.style.zIndex = '1000';
  alert.style.animation = 'fadeIn 0.3s ease-out';
  
  document.body.appendChild(alert);
  
  setTimeout(() => {
    alert.style.animation = 'fadeOut 0.3s ease-out';
    setTimeout(() => alert.remove(), 300);
  }, 3000);
}

// Stopwords list
const stopwords = ['the', 'of', 'in', 'and', 'to', 'with', 'a', 'an', 'for', 'on', 'at', 'by', 'from', 'as', 'is', 'was', 'be', 'are', 'this', 'that'];

// Extract keywords from title
function extractKeywords(title) {
  return title
    .replace(/[^a-zA-Z0-9\s]/g, '') 
    .split(/\s+/) 
    .filter(word => word.length > 2)
    .filter(word => !stopwords.includes(word.toLowerCase()))
    .slice(0, 8)
    .join(' ');
}

// Format date as yyyy-mm-dd
function formatDate(dateString) {
  if (!dateString) return "";
  try {
    const date = new Date(dateString);
    if (isNaN(date)) return "";
    return date.toLocaleDateString('en-US', { 
      year: 'numeric', 
      month: 'long', 
      day: 'numeric' 
    });
  } catch {
    return dateString;
  }
}

function searchGoogle() {
  const title = document.getElementById('article-title').textContent.trim();
  const publishedDate = document.getElementById('article-date').textContent.trim();

  const keywords = extractKeywords(title);
  const date = formatDate(publishedDate);

  // Expand range: 3 days before and after
  const dateObj = new Date(publishedDate) || new Date();
  const after = new Date(dateObj);
  after.setDate(dateObj.getDate() - 3);
  const before = new Date(dateObj);
  before.setDate(dateObj.getDate() + 3);

  const afterStr = after.toISOString().split('T')[0];
  const beforeStr = before.toISOString().split('T')[0];

  const query = encodeURIComponent(
    `${keywords} site:nytimes.com OR site:cnn.com OR site:foxnews.com OR site:reuters.com OR site:abcnews.go.com OR site:nbcnews.com after:${afterStr} before:${beforeStr}`
  );

  window.open(`https://www.google.com/search?q=${query}`, '_blank');
}





  




