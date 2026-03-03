# CareerSignal (MVP)

CareerSignal ist ein schlankes SaaS-MVP zum **Überwachen von Karriere-Seiten**:
- User registrieren sich (E-Mail + Passwort)
- „Beobachtungen“ (Name + Karriere-URL + optional Keywords + optional Discord Webhook)
- Runner prüft regelmäßig: Erreichbarkeit/Blockade, Änderungen (Text-Hash), Best-effort Job-Links (ATS/Heuristik), Keyword-Treffer
- Benachrichtigungen: **E-Mail Pflicht**, Discord optional


> Design-Ziel: **Clean Rewrite** ohne EasyJobs-Altlasten.

---

## Projektstruktur

```
careersignal/
  app/
    main.py
    db.py
    core/
      checker.py
      config.py
      discord.py
      logging.py
      mailer.py
      security.py
      utils.py
    routers/
      auth.py
      pages.py
      api.py
    templates/
      base.html
      login.html
      register.html
      dashboard.html
      watcher_form.html
      watcher_detail.html
      settings.html
    static/
      style.css
  scripts/
    init_db.py
    run_checks.py
  systemd/
    careersignal.service
    careersignal-checks.service
    careersignal-checks.timer
  nginx/
    careersignal
  requirements.txt
  data/   (SQLite DB + secret key file)
```

---

## Lokal starten

```bash
cd careersignal
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# optional, aber empfohlen
export CAREERSIGNAL_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CAREERSIGNAL_PUBLIC_BASE_URL="http://localhost:8000"

uvicorn app.main:app --reload
```

Öffne: `http://localhost:8000`

### Checks lokal ausführen

```bash
source .venv/bin/activate
python scripts/run_checks.py
```

---

## ENV Variablen

### App
- `CAREERSIGNAL_SECRET_KEY` (empfohlen in Prod)
- `CAREERSIGNAL_PUBLIC_BASE_URL` (wichtig für Links in E-Mail/Discord)
- `CAREERSIGNAL_ALLOWED_HOSTS` (CSV, z.B. `careersignal.de,careersignal.app`)
- `CAREERSIGNAL_COOKIE_SECURE` (`1` bei HTTPS)
- `CAREERSIGNAL_CHECK_INTERVAL_MINUTES` (Default `60`)
- `CAREERSIGNAL_LOG_LEVEL` (Default `INFO`)
- `CAREERSIGNAL_LOG_FORMAT` (`json` oder `plain`, Default `json`)

### SMTP (Pflicht für produktive Nutzung)
- `SMTP_HOST`
- `SMTP_PORT` (Default `587`)
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_FROM` (z.B. `CareerSignal <noreply@deinedomain>`)
- `SMTP_STARTTLS` (`1` Default, setze `0` falls dein Server kein STARTTLS kann)

Wenn SMTP nicht gesetzt ist, läuft die App weiter – aber zeigt im UI **„E-Mail nicht konfiguriert“**.

---

## Deployment (parallel zu EasyJobs auf derselben Maschine)

Beispiel-Pfade:
- Code: `/home/careersignal/careersignal`
- Venv: `/home/careersignal/venv`
- Port: `8010`
- Env-File: `/etc/careersignal.env`

### 1) User + Verzeichnis

```bash
sudo adduser --system --group --home /home/careersignal careersignal
sudo mkdir -p /home/careersignal/careersignal
sudo chown -R careersignal:careersignal /home/careersignal
```

### 2) Code deployen + venv

```bash
sudo -u careersignal -H bash -lc '
  cd /home/careersignal/careersignal
  python3 -m venv /home/careersignal/venv
  source /home/careersignal/venv/bin/activate
  pip install -r requirements.txt
'
```

### 3) Env-Datei

```bash
sudo nano /etc/careersignal.env
```

Beispiel:
```
CAREERSIGNAL_SECRET_KEY=...random...
CAREERSIGNAL_PUBLIC_BASE_URL=https://careersignal.de
CAREERSIGNAL_ALLOWED_HOSTS=careersignal.de,careersignal.app
CAREERSIGNAL_COOKIE_SECURE=1

SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASS=...
SMTP_FROM=CareerSignal <noreply@careersignal.de>
```

### 4) systemd installieren

```bash
sudo cp systemd/careersignal.service /etc/systemd/system/
sudo cp systemd/careersignal-checks.service /etc/systemd/system/
sudo cp systemd/careersignal-checks.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now careersignal.service
sudo systemctl enable --now careersignal-checks.timer
```

Status & Logs:
```bash
systemctl status careersignal
journalctl -u careersignal -f

systemctl status careersignal-checks.timer
journalctl -u careersignal-checks.service -f
```

### 5) nginx

```bash
sudo cp nginx/careersignal /etc/nginx/sites-available/careersignal
sudo ln -s /etc/nginx/sites-available/careersignal /etc/nginx/sites-enabled/careersignal
sudo nginx -t && sudo systemctl reload nginx
```

> Wichtig: eigener Port (`8010`) und eigener systemd Service -> keine Kollision mit EasyJobs.

---

## DB / Migration / Init

Die DB wird **automatisch beim App-Start** initialisiert (`app/db.py:init_db()`).

Manuell:
```bash
python scripts/init_db.py
```

---

## Troubleshooting

### „Status: Blockiert (403/429)“
- Seite blockiert Bots oder hat Rate-Limits.
- CareerSignal setzt Backoff (Timer läuft weiter, aber die nächste Prüfung wird verzögert).

### „Fehler (Timeout/DNS/HTTP >= 400)“
- URL prüfen.
- Firewall/Netzwerk vom Server aus testen.

### Keine E-Mails
- UI zeigt oben Warnung, wenn SMTP nicht konfiguriert.
- Logs prüfen:
  ```bash
  journalctl -u careersignal-checks.service -f
  ```

---

## Hinweis zu Politeness / Last
MVP-Regel: **max. 1 HTTP Request pro Beobachtung pro Check-Lauf**.
Payload ist begrenzt, Timeouts sind gesetzt, und 403/429 wird als „Blockiert“ behandelt.
