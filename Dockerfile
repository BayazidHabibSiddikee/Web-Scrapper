# Web Scraper Toolkit — production image
# Build: docker build -t web-scraper .  (requires Docker Desktop or podman)
# Run:   docker run --rm -v /host/output:/app/output web-scraper
# Environment overrides: CAPTCHA_API_KEY, SCRAPER_BASE_URL, HEADLESS=false

FROM python:3.13-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HEADLESS=true
ENV CAPTCHA_SERVICE=2captcha

RUN apt-get update && apt-get install -y --no-install-recommends \
        wget curl git procps less ca-certificates fonts-liberation \
        chromium chromium-sandbox libnss3 \
        libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxdamage1 libxrandr2 libgbm1 libasound2 \
        libpango-1.0-0 libcairo2 libwayland-client0 libxcomposite1 \
        libopenjp2-7 libepoxy0 liblcms2-2 libcogl-pango20 libcogl20 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip setuptools wheel \
 && pip install --no-cache-dir -r requirements.txt \
 && playwright install chromium

COPY scraper.py master_pipeline.py grab_images.py quickstart.py ./
COPY scrapling_backend.py captcha_flow.py maps_scraper.py security_utils.py cookies.py form_fill.py agent_tools.py mcp_server.py ./
COPY medex_scraper.py ./
COPY examples examples/
COPY config config/
COPY docs docs/
COPY Makefile README.md SKILL.md llms.txt SOCIAL_POSTS.md ./
COPY maps.compose.yml ./

RUN playwright install-deps chromium || true   # idempotent; may fail gracefully in non-root

EXPOSE 8000

ENTRYPOINT ["python"]
CMD ["--help"]

# Useful shortcuts:
#   docker run --rm -v $(pwd)/output:/app/output web-scraper agent_tools.py --list
#   docker run --rm -e CAPTCHA_API_KEY=$CAPTCHA_API_KEY \
#     -v $(pwd)/output:/app/output web-scraper captcha_flow.py https://site.com
#   docker run --rm -e HEADLESS=false \
#     -v $(pwd)/output:/app/output web-scraper medex_scraper.py details --mode session --limit 5
