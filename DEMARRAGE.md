# Visite Syndic — Notes techniques (à jour au 2026-07-08)

Remplace l'ancienne version décrivant une exécution locale Flask. Architecture
et gotchas identiques à l'app sœur `Syndic/facturation-app` (voir son
`DEMARRAGE.md` pour le détail des explications) — résumé ici, adapté à ce
projet.

## Architecture actuelle

SPA statique 100 % front-end : `public/index.html`. Parle directement à
**Microsoft Graph API** (listes SharePoint), pas de backend.

- Site SharePoint : `/sites/allsimmo.sharepoint.com:/sites/ALLSIMMO-Syndic`
  (même site que Facturation)
- Listes : `VS_Coproprietes`, `VS_Visites`, `VS_Observations`
- Photos : dossier `Visite-Photos/<visiteId>/<obsId>/` dans le drive du site
- Auth Azure AD : client ID `6b5a2aa5-cc80-4dba-8232-badc227b5996` (app
  "Visite Syndic", différente de celle de Facturation), même tenant
  `9b6f0a5e-fd44-4a3f-a1de-430911fda398`. Client public (SPA + PKCE).
  **Si un client secret existe pour cette app dans Azure Portal (héritage de
  l'ancienne appli Flask/Render), il doit être régénéré/supprimé** — cette
  SPA n'en a pas besoin.

### Deux modes d'authentification + dialogues natifs interdits

Mêmes contraintes que Facturation : le webview Teams bloque silencieusement
`window.open()` et `window.confirm()`/`alert()`. Utiliser
`microsoftTeams.authentication.authenticate()` (PKCE via
`public/auth-start.html`/`public/auth-end.html`) pour l'auth en iframe Teams,
et la fonction maison `confirmModal()` (jamais `confirm()`/`alert()`) pour
toute confirmation. Les 4 usages de `confirm()` (suppression copro/visite/
observation, réouverture) ont été corrigés le 2026-07-08.

## Déploiement — Cloudflare Pages, automatique à chaque push

Depuis le 2026-07-08, hébergé sur **Cloudflare Pages**
(`https://visite-syndic.pages.dev`), connecté en continu au dépôt GitHub.
**Chaque `git push` sur `main` déploie automatiquement** (config : Framework
preset = None, Build command = vide, Build output directory = `public`).
Voir `Syndic/facturation-app/DEMARRAGE.md` pour le détail de la migration
et de son historique (même app sœur, même migration le même jour).

⚠️ Cloudflare Pages redirige `xxx.html` → `xxx` (sans extension). Les
constantes `REDIRECT`/`AUTH_START_URL` utilisent donc `/auth-start` et
`/auth-end` (sans `.html`) — cohérent avec les URIs enregistrées dans
Azure AD pour le client `6b5a2aa5-cc80-4dba-8232-badc227b5996` :
`https://visite-syndic.pages.dev` et `https://visite-syndic.pages.dev/auth-end`.

## Incident de sécurité du 2026-07-08 (corrigé)

Même faille que sur Facturation : Netlify (l'hébergeur précédent) déployait
tout le dossier (`--dir="."`), exposant publiquement `app.py` (mot de passe
par défaut), `data/visite.db` (données réelles des visites), `render.yaml`,
`Procfile`, `requirements.txt`, les templates Flask, et les scripts d'admin
one-shot (`setup.html`, `import.html`). Aucun secret en clair trouvé dans le
code cette fois. Corrigé en isolant `public/` (3 fichiers) comme seul
dossier publié — la migration vers Cloudflare Pages a conservé ce
cloisonnement.

## Fichiers legacy conservés en dépôt (non déployés, non exposés)

- **Ancienne appli Flask** (`app.py`, `templates/`, `Procfile`, `render.yaml`,
  `requirements.txt`, `data/`) — Render mort, plus utilisée. **Toujours
  présente dans le dépôt** (contrairement à Facturation où elle a été
  supprimée) : la décision de suppression a été posée à l'utilisateur le
  2026-07-08 mais est restée sans réponse. À trancher.
- `setup.html`, `import.html` : outils d'amorçage SharePoint déjà exécutés,
  gardés en référence. Ne pas relancer sans vérifier l'état des listes.
- `teams_app/manifest.json` + `VisteSyndic-Teams.zip` : package Teams
  pointant vers `visite-syndic.pages.dev` depuis le 2026-07-08. Toute
  modification du manifeste nécessite de régénérer le zip
  (`Compress-Archive` PowerShell) et de le ré-uploader dans le Centre
  d'administration Teams — Teams peut mettre du temps à propager la mise à
  jour d'une app déjà installée (désinstaller/réinstaller au besoin).
