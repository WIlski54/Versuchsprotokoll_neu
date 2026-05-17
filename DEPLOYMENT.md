# Deployment auf Hetzner/Coolify

Die App ist als Flask-SocketIO-Projekt vorbereitet. Für WebSockets im Container wird ein einzelner Gunicorn-Worker mit Eventlet genutzt.

## Environment

```bash
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.1-flash-lite
SECRET_KEY=openssl-rand-hex-32
LEHRER_PASSWORD=sicheres-passwort
DAILY_TOKEN_LIMIT=50000
```

## Coolify

Persistent Storage:

```text
Source:      /data/versuchsprotokoll-wk
Destination: /app/data
```

Der Container lauscht auf Port `5000`. Das Dockerfile startet:

```bash
gunicorn -w 1 --worker-class eventlet -b 0.0.0.0:5000 app:app
```

## Klassischer Server

```bash
git clone https://github.com/WIlski54/Versuchsprotokoll_WK.git
cd Versuchsprotokoll_WK
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
gunicorn -w 1 --worker-class eventlet -b 127.0.0.1:5000 app:app
```

Nginx-Beispiel:

```nginx
server {
    server_name deine-domain.de;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Danach HTTPS mit Certbot:

```bash
sudo certbot --nginx -d deine-domain.de
```
