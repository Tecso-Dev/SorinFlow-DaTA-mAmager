# رازها و تنظیمات سرور — Secrets & Server Configuration

Where every credential lives, how to add a new one, and how to rotate one
without taking the site down.

**The rule:** this repository is public. Anything written literally in a tracked
file is published the moment it is pushed — and stays readable in the history
even after you delete the line. If a value would be a problem in a stranger's
hands, it does not belong in a file. It belongs in the Kubernetes Secret.

---

## 1. Where each kind of value belongs

| Kind of value | Where it lives | In git? |
|---|---|---|
| Passwords, API keys, tokens, `SECRET_KEY` | Kubernetes Secret `sorinflow-secrets` | **never** |
| Kavenegar API key | the Secret, **or** the «پیامک» panel (encrypted) | **never** |
| SMTP / Gmail App Password | the Secret, **or** the «ایمیل» panel (encrypted) | **never** |
| TLS certificates and private keys | Traefik's own ACME store on the server | **never** |
| Divar session cookies | the `data-pvc` volume (`/app/data/cookies`) | **never** |
| Which variables exist, and what they mean | `.env.example` — names and comments only | yes |
| Non-secret settings (timeouts, limits, feature flags) | `k8s/04-backend.yaml` as plain `env:` | yes |
| Local development values | `local/local.env` — ignored by git | **never** |

If you are unsure which column something belongs in, treat it as a secret. The
cost of over-classifying is a slightly less convenient deploy; the cost of
under-classifying is this document.

---

## 2. Adding a new secret

Say you are adding the Kavenegar SMS key.

**Step 1 — declare that it exists, without its value.** In `.env.example`:

```bash
# SMS provider for portal login codes. Get the key from the Kavenegar panel.
KAVENEGAR_API_KEY=
```

**Step 2 — read it in `app/config.py`:**

```python
kavenegar_api_key: str = Field(default="", env="KAVENEGAR_API_KEY")
```

Default to empty, never to a working value. A blank key should make the feature
refuse to run and say so; a real-looking default makes a broken deploy look
healthy.

**Step 3 — put the real value in the cluster.** On the server:

```bash
kubectl patch secret sorinflow-secrets -n sorinflow \
  -p "{\"stringData\":{\"KAVENEGAR_API_KEY\":\"$REAL_KEY\"}}"
```

**Step 4 — wire it into the pod.** In `k8s/04-backend.yaml`, under `env:`:

```yaml
- name: KAVENEGAR_API_KEY
  valueFrom:
    secretKeyRef:
      name: sorinflow-secrets
      key: KAVENEGAR_API_KEY
```

**Step 5 — apply and restart:**

```bash
kubectl apply -f k8s/04-backend.yaml
kubectl rollout restart deployment/backend -n sorinflow
```

The value never appears in a commit, a pull request, or a CI log.

---

## 2b. The SMS panel (Kavenegar)

The «پیامک» screen needs one credential. It can come from either place, and the
environment always wins:

```bash
kubectl patch secret sorinflow-secrets -n sorinflow \
  -p "{\"stringData\":{\"KAVENEGAR_API_KEY\":\"$REAL_KEY\",\"KAVENEGAR_SENDER\":\"10004346\"}}"
kubectl rollout restart deployment/backend -n sorinflow
```

Or paste it into the panel: **پیامک → تنظیمات کاوه‌نگار**. A key saved there is
encrypted with a Fernet key derived from `SECRET_KEY`, stored in `app_settings`,
and never sent back to the browser — the form shows `ABCD****WXYZ` and nothing
more. This exists so the panel can be set up without a deploy; the Secret is
still the right home for it in production.

Two consequences worth knowing:

- **Rotating `SECRET_KEY` makes a panel-saved key unreadable.** That is
  deliberate — it must be re-entered rather than silently decrypting under a
  retired key. A key kept in the Secret is unaffected.
- **`KAVENEGAR_OTP_TEMPLATE`** names the template used for login codes.
  Templates are the correct channel for one-time codes: a dedicated delivery
  route, no approved sender line required, and they reach numbers that have
  opted out of advertising messages — which a login code is not, but a plain
  send is treated as. Create it in Kavenegar's panel with `%token` as the code
  placeholder.

---

## 2c. Email (Gmail SMTP)

The site sends login codes, welcome messages and notifications from
`sorinflow.agency@gmail.com`.

**Google will not accept the account password.** SMTP needs a 16-character
*App Password*, and that requires 2-Step Verification on the account first:

1. Turn on 2-Step Verification — <span dir="ltr">Google Account → Security</span>
2. <span dir="ltr">Security → App passwords</span> → create one, name it "SorinFlow"
3. Copy the 16 characters (Google shows them once)

Then either put it in the cluster:

```bash
kubectl patch secret sorinflow-secrets -n sorinflow -p "{\"stringData\":{
  \"SMTP_HOST\": \"smtp.gmail.com\",
  \"SMTP_PORT\": \"587\",
  \"SMTP_USER\": \"sorinflow.agency@gmail.com\",
  \"SMTP_PASSWORD\": \"<the 16-character app password>\"
}}"
kubectl rollout restart deployment/backend -n sorinflow
```

…or paste it into **ایمیل → تنظیمات ایمیل** in the panel, where it is encrypted
with a key derived from `SECRET_KEY` and never returned to the browser. The
environment wins if both are set.

Two buttons in that panel, and they answer different questions:

- **بررسی اتصال** logs in and hangs up. Use it while typing the password — it
  costs nobody an email.
- **ارسال ایمیل آزمایشی** sends the real styled message, which proves the
  templates, the Persian and the RTL as well as the credentials.

If the account is ever compromised, revoke that App Password from the same
Google screen; it is independent of the account password and revoking it does
not lock anyone out of the mailbox.

---

## 3. Reading what is currently set

```bash
# names only — safe to run anywhere, shows no values
kubectl get secret sorinflow-secrets -n sorinflow -o jsonpath='{.data}' | jq 'keys'

# one specific value — only on a screen nobody else can see
kubectl get secret sorinflow-secrets -n sorinflow \
  -o jsonpath='{.data.API_KEY}' | base64 -d; echo
```

Never pipe a secret into a file inside the repository, and never paste one into
an issue, a commit message, or a chat.

---

## 4. Rotating a secret

Three tiers, by how much care each needs.

### 4a. Safe to change any time

`API_KEY`, `KAVENEGAR_API_KEY`, `TELEGRAM_BOT_TOKEN`, `SMTP_PASSWORD`,
`LLM_API_KEY`.

```bash
kubectl patch secret sorinflow-secrets -n sorinflow \
  -p "{\"stringData\":{\"API_KEY\":\"$(openssl rand -hex 32)\"}}"
kubectl rollout restart deployment/backend -n sorinflow
```

### 4b. Logs everyone out — `SECRET_KEY`

It signs every dashboard token, so changing it invalidates all of them. No
downtime; every staff member simply signs in again. Do it when someone is
around to notice, not at 2am.

```bash
kubectl patch secret sorinflow-secrets -n sorinflow \
  -p "{\"stringData\":{\"SECRET_KEY\":\"$(openssl rand -base64 48 | tr -d '\n')\"}}"
kubectl rollout restart deployment/backend -n sorinflow
```

### 4c. ⚠️ Takes the site down if done wrong — `POSTGRES_PASSWORD`

**Read this whole section before running anything.**

The password is stored *inside* the database, on the `postgres-pvc` volume,
from when it was first initialised. Changing only the Kubernetes Secret gives
the application a new password while the database still expects the old one:
every connection is refused and the site is fully down until it is corrected.

The database must be changed **first**, and the two must be changed in the same
window.

```bash
NEW_PW=$(openssl rand -hex 24)          # hex, not base64: it goes inside a URL

# 1. change it inside Postgres, where the old password still works
kubectl exec -n sorinflow deploy/postgres -- \
  psql -U sorinflow -d divar_scraper \
  -c "ALTER USER sorinflow WITH PASSWORD '$NEW_PW';"

# 2. update every field that carries it — DATABASE_URL embeds it too
kubectl patch secret sorinflow-secrets -n sorinflow -p "{\"stringData\":{
  \"POSTGRES_PASSWORD\": \"$NEW_PW\",
  \"DATABASE_URL\": \"postgresql+asyncpg://sorinflow:$NEW_PW@postgres:5432/divar_scraper\"
}}"

# 3. restart the app so it picks the new value up
kubectl rollout restart deployment/backend -n sorinflow
kubectl rollout status deployment/backend -n sorinflow --timeout=300s

# 4. confirm
curl -sf https://sorinflow.com/health && echo "  OK"
```

**If step 3 fails to become ready**, the app cannot reach the database. Put the
old password back with the same `ALTER USER` and re-patch the Secret; the
previous pod is still serving while the new one fails its readiness probe, so
the site stays up while you fix it.

`REDIS_PASSWORD` is the same shape but far more forgiving — Redis keeps no
persistent copy, so patch the Secret and restart both Redis and the backend.

---

## 5. If a secret is ever committed again

Treat it as compromised the moment it is pushed. GitHub, forks, clones,
Dependabot and every CI cache may already hold it.

1. **Rotate first.** Purging history does not un-publish a value that has been
   public — it only stops it being published again. Section 4 above.
2. **Then purge it from history:**

   ```bash
   git clone --mirror . ../backup.git        # always, before rewriting anything
   pip install git-filter-repo
   git filter-repo --invert-paths --path the/leaked/file --force
   git remote add origin git@github.com:Tecso-Dev/SorinFlow-DaTA-mAmager.git
   git push --force origin main
   ```
3. **Add it to `.gitignore`** so the same file cannot come back.
4. **Find what put it there.** A leak is usually a process, not an accident:
   `renew-ssl.sh` copied the TLS private key into `nginx/ssl/` on every
   certificate renewal, so the key reappeared no matter how often it was
   deleted. Deleting the file without fixing the script would have leaked it
   again on the next renewal.

---

## 6. What is currently deliberately public

These live in the repository on purpose and are not secrets:

- `.env.example` — variable names and guidance, no values
- `k8s/*.yaml` — every manifest **except** `01-secrets.yaml`, which is ignored
- `docker-compose.yml` — refuses to start unless `POSTGRES_PASSWORD` and
  `REDIS_PASSWORD` are supplied, rather than falling back to a default
- Domain names, the server IP, and the container registry path — visible from
  DNS anyway

---

## 7. Local development

`local/local.env` holds throwaway values and is ignored by git. It never shares
credentials with production, and `./local/start.sh` reads it. Real environment
variables take precedence over `.env`, so a local run cannot accidentally pick
up a production value even if one is present.

Never copy a production secret onto a laptop to "test something quickly". Point
the local stack at the local database instead.
