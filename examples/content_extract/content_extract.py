"""
content_extract.py
================
Content extraction tools for scraping:

1. Trafilatura  — best-in-class article/content extraction, handles
   boilerplate removal, metadata, comments, tables.
2. Readability   — Mozilla's readability port, extracts main article.
3. Newspaper3k   — article extraction with NLP (keywords, summary).
4. BeautifulSoup — manual DOM extraction, full control.

Install:
    pip install trafilatura readability-lxml newspaper3k beautifulsoup4 lxml
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class Article:
    url: str
    title: str = ""
    author: str = ""
    text: str = ""
    html: str = ""
    links: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    publish_date: str = ""
    source: str = ""
    comments: str = ""
    tables: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    extractor: str = ""


# ---------------------------------------------------------------------------
# 1. Trafilatura (recommended)
# ---------------------------------------------------------------------------

def extract_trafilatura(url: str, html: Optional[str] = None) -> Article:
    """
    Trafilatura is the gold standard for content extraction.
    It strips ads, nav, footers, related posts, etc.
    Falls back to fetching HTML if not provided.
    """
    try:
        import trafilatura

        if not html:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return Article(url=url, error="fetch failed")
            html = downloaded

        extracted = trafilatura.extract(
            html,
            include_links=True,
            include_images=True,
            include_tables=True,
            include_comments=True,
            no_fallback=False,
            favor_precision=True,        # strict: only main content
            favor_recall=False,
            deduplicate=True,
            url=url,
        )

        metadata = trafilatura.extract_metadata(html, default_url=url)
        if metadata:
            title = metadata.title or ""
            author = metadata.author or ""
            date = metadata.date or ""
            source = url
        else:
            title = author = date = source = ""

        # Extract links separately
        soup = BeautifulSoup(html, "lxml")
        links = [a.get("href", "") for a in soup.find_all("a", href=True)]
        links = [urljoin(url, l) for l in links if l]

        images = [img.get("src", "") for img in soup.find_all("img", src=True)]
        images = [urljoin(url, i) for i in images if i]

        return Article(
            url=url, title=title, author=author, text=extracted or "",
            html=html, links=links, images=images,
            publish_date=date, source=source, extractor="trafilatura",
            metadata={"description": metadata.description if metadata else "",
                      "categories": metadata.categories if metadata else []},
        )

    except ImportError:
        return Article(url=url, error="trafilatura not installed")
    except Exception as exc:
        logger.error("Trafilatura extraction failed: %s", exc)
        return Article(url=url, error=str(exc))


# ---------------------------------------------------------------------------
# 2. Readability (Mozilla)
# ---------------------------------------------------------------------------

def extract_readability(url: str, html: Optional[str] = None) -> Article:
    """
    Mozilla's readability-lxml extracts main article content.
    Good for news/blog posts. Less aggressive than trafilatura.
    """
    try:
        from readability import Document

        if not html:
            import requests
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (compatible; scraper/1.0)"
            })
            html = resp.text

        doc = Document(html)
        title = doc.title()
        cleaned_html = doc.summary()

        soup = BeautifulSoup(cleaned_html, "lxml")
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        links = [a.get("href", "") for a in soup.find_all("a", href=True)]
        links = [urljoin(url, l) for l in links if l]

        return Article(
            url=url, title=title, text=text, html=cleaned_html,
            links=links, extractor="readability",
        )

    except ImportError:
        return Article(url=url, error="readability-lxml not installed")
    except Exception as exc:
        logger.error("Readability extraction failed: %s", exc)
        return Article(url=url, error=str(exc))


# ---------------------------------------------------------------------------
# 3. Newspaper3k (with NLP)
# ---------------------------------------------------------------------------

def extract_newspaper(url: str, html: Optional[str] = None) -> Article:
    """
    newspaper3k extracts articles AND runs NLP: keywords, summary,
    sentiment. Good for news aggregation pipelines.
    """
    try:
        from newspaper import Article, ArticleException

        article = Article(url, fetch_images=True)
        if html:
            article.set_html(html)
        else:
            article.download()

        article.parse()
        article.nlp()

        soup = BeautifulSoup(article.html, "lxml")
        links = [a.get("href", "") for a in soup.find_all("a", href=True)]
        links = [urljoin(url, l) for l in links if l]

        return Article(
            url=url, title=article.title, author=", ".join(article.authors or []),
            text=article.text, html=article.html, links=links,
            publish_date=str(article.publish_date) if article.publish_date else "",
            extractor="newspaper3k",
            metadata={
                "keywords": article.keywords,
                "summary": article.summary,
                "top_image": article.top_image,
                "movies": article.movies,
            },
        )

    except ImportError:
        return Article(url=url, error="newspaper3k not installed")
    except ArticleException as exc:
        return Article(url=url, error=str(exc))
    except Exception as exc:
        logger.error("Newspaper extraction failed: %s", exc)
        return Article(url=url, error=str(exc))


# ---------------------------------------------------------------------------
# 4. Manual BeautifulSoup (full control)
# ---------------------------------------------------------------------------

def extract_manual(url: str, html: str,
                   title_sel: str = "h1",
                   content_sel: str = "article, main, #content, .post-body",
                   text_sel: str = "p") -> Article:
    """
    Manual extraction with CSS selectors. Maximum control.
    Specify selectors for your target site's structure.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove noise
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    # Title
    title = ""
    for sel in ["h1", "title", 'meta[property="og:title"]']:
        el = soup.select_one(sel)
        if el:
            title = el.get_text(strip=True) or el.get("content", "")
            if title:
                break

    # Content block
    content_el = soup.select_one(content_sel)
    if content_el:
        paragraphs = [p.get_text(strip=True) for p in content_el.find_all(text_sel)]
        text = "\n\n".join(p for p in paragraphs if len(p) > 30)
    else:
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

    links = [urljoin(url, a.get("href", "")) for a in soup.find_all("a", href=True)]
    images = [urljoin(url, img.get("src", "")) for img in soup.find_all("img", src=True)]

    return Article(
        url=url, title=title, text=text, html=str(soup),
        links=links, images=images, extractor="manual-bs4",
    )


# ---------------------------------------------------------------------------
# Compare extractors
# ---------------------------------------------------------------------------

def compare_extractors(url: str, html: Optional[str] = None):
    """Run all extractors and show character counts."""
    results = {
        "trafilatura": extract_trafilatura(url, html),
        "readability": extract_readability(url, html),
        "newspaper3k": extract_newspaper(url, html),
        "manual-bs4": extract_manual(url, html or ""),
    }
    for name, art in results.items():
        status = art.text[:30] + "..." if art.error else f"{len(art.text)} chars"
        print(f"  {name:15s}: {status}")
    return results


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    compare_extractors(target)
