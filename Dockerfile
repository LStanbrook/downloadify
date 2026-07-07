# Builds the hosted web app (see render.yaml). Not used for local/desktop
# use -- run `python main.py` from source for that instead.
FROM python:3.11-slim

# ffmpeg is required by yt-dlp to extract and encode downloaded audio as MP3.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

COPY main.py .
COPY downloadify ./downloadify

# Hardcoded (rather than left to be set in the hosting dashboard) so a public
# deployment can never accidentally run with the local-user feature set --
# see config.py's PUBLIC_DEPLOYMENT docstring for what this gates.
ENV PUBLIC_DEPLOYMENT=true

EXPOSE 8000

CMD ["sh", "-c", "python main.py --mode web --host 0.0.0.0 --port ${PORT:-8000}"]
