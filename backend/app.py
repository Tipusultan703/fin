import os
import re
import logging
import asyncio
from urllib.parse import urlparse, quote_plus
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import openai
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import tldextract
import requests
from bs4 import BeautifulSoup
from newspaper import Article
from playwright.async_api import async_playwright
import nltk
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize

# Download NLTK data
nltk.download('punkt')
nltk.download('stopwords')

# Logger setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("❌ Missing OPENAI_API_KEY in .env file.")

# Flask App
app = Flask(__name__)
CORS(app)

# OpenAI client
openai.api_key = api_key

# Constants
TOP_NEWS_DOMAINS = [
    'nytimes.com', 'washingtonpost.com', 'wsj.com', 
    'latimes.com', 'chicagotribune.com', 'usatoday.com',
    'nbcnews.com', 'cnn.com', 'foxnews.com', 'reuters.com',
    'apnews.com', 'bbc.com', 'theguardian.com', 'bloomberg.com'
]

# Helper functions
def extract_domain(url):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        domain = domain.replace("www.", "").replace("mobile.", "")
        return domain
    except:
        return None

def clean_text(text):
    """Clean and normalize text for comparison"""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)  # Remove punctuation
    text = re.sub(r'\s+', ' ', text).strip()  # Normalize whitespace
    return text

def extract_keywords(text, n=10):
    """Extract top keywords from text using TF-IDF"""
    if not text.strip():
        return []
    
    # Tokenize and remove stopwords
    words = word_tokenize(text.lower())
    stop_words = set(stopwords.words('english'))
    words = [word for word in words if word.isalpha() and word not in stop_words]
    
    if not words:
        return []
    
    # Use simple frequency count for keywords
    freq_dist = nltk.FreqDist(words)
    return [word for word, _ in freq_dist.most_common(n)]

def is_similar_article(main_article, comparison_article, threshold=0.4):
    """Check if two articles are about the same topic using cosine similarity"""
    main_text = clean_text(main_article.get('title', '') + " " + main_article.get('text', ''))
    comp_text = clean_text(comparison_article.get('title', '') + " " + comparison_article.get('summary', ''))
    
    if not main_text or not comp_text:
        return False
    
    # Calculate Jaccard similarity between keyword sets as a simple approach
    main_keywords = set(extract_keywords(main_text))
    comp_keywords = set(extract_keywords(comp_text))
    
    if not main_keywords or not comp_keywords:
        return False
    
    intersection = main_keywords.intersection(comp_keywords)
    union = main_keywords.union(comp_keywords)
    
    similarity = len(intersection) / len(union) if union else 0
    return similarity >= threshold

async def extract_with_playwright(url):
    """Extract article content using Playwright (with Firefox and stealth headers)."""
    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True)
            page = await browser.new_page()

            # Set stealth headers
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/118.0.5993.90 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            })
            await page.evaluate("""() => {
                Object.defineProperty(navigator, 'webdriver', {get: () => false});
            }""")

            try:
                await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                await page.wait_for_load_state('networkidle')
            except Exception as e1:
                logger.warning(f"Initial page load failed: {e1}")
                try:
                    await page.goto(url, timeout=60000, wait_until='load')
                    await page.wait_for_timeout(5000)
                except Exception as e2:
                    logger.error(f"Retry also failed: {e2}")
                    await browser.close()
                    return fallback_newspaper_extraction(url)

            # Scroll to trigger lazy-loading
            for _ in range(3):
                await page.mouse.wheel(0, 1500)
                await page.wait_for_timeout(1000)

            # Try common article selectors
            selectors = [
                'article', 'main', '[itemprop="articleBody"]',
                '.article-body', '.post-content', '.entry-content'
            ]

            article_content = ""
            for selector in selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        article_content = await element.inner_text()
                        if article_content.strip():
                            break
                except:
                    continue

            # Fallback: collect all meaningful <p> tags
            if not article_content:
                paragraphs = await page.query_selector_all("p")
                blocks = []
                for p in paragraphs:
                    try:
                        text = await p.inner_text()
                        if len(text.strip()) > 50:
                            blocks.append(text.strip())
                    except:
                        continue
                article_content = "\n\n".join(blocks)

            # Title and date
            title = await page.title() or ""

            pub_date = await page.evaluate('''() => {
                const metaTags = [
                    'article:published_time', 'pubdate', 'publish-date',
                    'date', 'DC.date.issued', 'sailthru.date'
                ];
                for (const name of metaTags) {
                    const tag = document.querySelector(`meta[property="${name}"], meta[name="${name}"]`);
                    if (tag && tag.content) return tag.content;
                }
                const timeTag = document.querySelector('time[datetime]');
                if (timeTag && timeTag.getAttribute('datetime')) {
                    return timeTag.getAttribute('datetime');
                }
                return "";
            }''')

            source = urlparse(url).netloc.replace("www.", "").split(":")[0]

            await browser.close()

            # Truncate overly long content
            if len(article_content) > 30000:
                article_content = article_content[:30000]

            return article_content.strip(), title.strip(), pub_date.strip(), source.strip()

    except Exception as e:
        logger.error(f"Playwright extraction failed: {e}")
        return fallback_newspaper_extraction(url)


def fallback_newspaper_extraction(url):
    """Fallback using Newspaper3k if Playwright fails."""
    try:
        article = Article(url)
        article.download()
        article.parse()
        source = urlparse(url).netloc.replace("www.", "").split(":")[0]
        pub_date = str(article.publish_date) if article.publish_date else ""
        return article.text.strip(), article.title.strip(), pub_date, source
    except Exception as e:
        logger.error(f"Newspaper3k fallback failed: {e}")
        return "", "", "", ""

def analyze_article(url):
    """Analyze an article for bias and generate neutral version"""
    text, title, date, source = asyncio.run(extract_with_playwright(url))

    if not text.strip():
        return {"error": "Failed to extract article content."}

    # Get bias score (0-100)
    bias_score_prompt = """Analyze the following news article and provide a bias score from 0 (completely neutral) 
    to 100 (extremely biased). Consider political leaning, emotional language, framing, and source reliability. 
    Respond ONLY with a number between 0 and 100."""
    bias_score = analyze_with_gpt(bias_score_prompt, text) or "0"

    # Get highlighted biased language
    redlined_prompt = """Identify and highlight biased words or phrases in the following article by 
    surrounding them with [BIAS][/BIAS] tags. Only tag clearly biased language that affects neutrality. 
    Keep the original text intact and only add the tags."""
    redlined_text = analyze_with_gpt(redlined_prompt, text) or ""

    # Generate neutral rewrite
    neutral_prompt = """Rewrite the following article to remove bias while preserving factual accuracy. 
    Maintain a neutral tone, avoid emotional language, and present all sides fairly. 
    Keep the structure similar to the original."""
    rewritten_text = analyze_with_gpt(neutral_prompt, text) or ""

    # Extract biased words from redlined text
    biased_words = []
    if redlined_text:
        biased_matches = re.findall(r"\[BIAS\](.*?)\[/BIAS\]", redlined_text)
        biased_words = [match.strip() for match in biased_matches if match.strip()]

    return {
        "url": url,
        "title": title,
        "published_date": date,
        "source": source,
        "bias_score": int(re.sub(r'\D', '', bias_score)) if bias_score else 0,
        "original_text": text,
        "redlined_text": redlined_text,
        "rewritten_text": rewritten_text,
        "biased_words": list(set(biased_words))[:20],  # Limit to top 20 unique biased words
    }


def analyze_with_gpt(prompt, content, temp=0):
    """Call OpenAI API with the given prompt and content"""
    try:
        res = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": content[:8000]}],
            temperature=temp
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return None

def scrape_google_news(query, exclude_domain=None):
    """Scrape Google News for articles matching the query"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    # Add exclusion if provided
    if exclude_domain:
        query += f" -site:{exclude_domain}"
    
    search_url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=nws"
    
    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = []
        
        for result in soup.select('.SoaBEf'):
            title = result.select_one('.n0jPhd.ynAwRc.MBeuO.nDgy9d')
            link = result.find('a', href=True)
            source = result.select_one('.NUnG9d span')
            snippet = result.select_one('.GI74Re.nDgy9d')
            
            article_data = {
                "title": title.text if title else "No Title",
                "url": link['href'] if link else "",
                "summary": snippet.text if snippet else "",
                "source": source.text if source else "Unknown"
            }
            
            # Only include articles from reputable sources
            domain = extract_domain(article_data['url'])
            if domain and any(top_domain in domain for top_domain in TOP_NEWS_DOMAINS):
                articles.append(article_data)
        
        return articles
    
    except Exception as e:
        logger.error(f"Google News scrape failed: {e}")
        return []

def fetch_comparison_articles(main_article, original_url=None):
    """Find comparison articles on the same topic"""
    if not main_article.get('title'):
        return []
    
    # Extract keywords from the main article
    keywords = extract_keywords(main_article.get('title', '') + " " + main_article.get('text', ''))
    if not keywords:
        return []
    
    # Create search query with date restriction if available
    date_query = ""
    if main_article.get('published_date'):
        try:
            pub_date = datetime.fromisoformat(main_article['published_date'].replace('Z', '+00:00'))
            date_query = f" after:{pub_date.strftime('%Y-%m-%d')} before:{(pub_date + timedelta(days=1)).strftime('%Y-%m-%d')}"
        except:
            pass
    
    query = " ".join(keywords[:5]) + date_query
    exclude_domain = extract_domain(original_url) if original_url else None
    
    # Get potential matches from Google News
    potential_articles = scrape_google_news(query, exclude_domain)
    
    # Filter to only include relevant articles
    comparison_articles = []
    for article in potential_articles:
        if is_similar_article(main_article, article):
            comparison_articles.append(article)
            if len(comparison_articles) >= 5:  # Limit to top 5 matches
                break
    
    return comparison_articles

@app.route('/api/compare-news', methods=['POST'])
def compare_news():
    """Analyze a news article for bias"""
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    logger.info(f"Analyzing article: {url}")
    try:
        result = analyze_article(url)

        if 'error' in result:
            return jsonify({'error': result['error']}), 500

        return jsonify({
            'articleTitle': result['title'],
            'articleDate': result['published_date'],
            'biasScore': result['bias_score'],
            'originalText': result['original_text'],
            'rewritten': result['rewritten_text'],
            'biasedWords': result['biased_words'],
            'neutralWords': [],  # GPT doesn't provide alternatives currently
            'publishedDate': result['published_date'],
            'source': result['source'],
            'comparison': []
        })

    except Exception as e:
        logger.error(f"Error analyzing article: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/fetch-comparisons', methods=['POST'])
def fetch_comparisons():
    """Fetch comparison articles for a given topic"""
    data = request.get_json()
    topic = data.get('topic')
    date = data.get('date')
    text = data.get('text', '')
    original_url = data.get('original_url')

    if not topic:
        return jsonify({'error': 'Missing topic'}), 400

    main_article = {
        'title': topic,
        'text': text,
        'published_date': date
    }

    comparison_articles = fetch_comparison_articles(main_article, original_url)
    return jsonify(comparison_articles)

@app.route('/')
def index():
    return render_template("index.html")

if __name__ == '__main__':
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))

































