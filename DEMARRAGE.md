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

## Déploiement — ⚠️ PAS automatique

Netlify (`famous-peony-ac78d2.netlify.app`), **pas connecté en continu à
GitHub**. Après chaque `git push` :

```bash
cd visite-app
netlify deploy --prod --dir="public"
```

**Seul `public/` est déployé** (`netlify.toml` à la racine). Ne JAMAIS
déployer avec `--dir="."`.

## Incident de sécurité du 2026-07-08 (corrigé)

Même faille que sur Facturation : Netlify déployait tout le dossier
(`--dir="."`), exposant publiquement `app.py` (mot de passe par défaut),
`data/visite.db` (données réelles des visites), `render.yaml`, `Procfile`,
`requirements.txt`, les templates Flask, et les scripts d'admin one-shot
(`setup.html`, `import.html`). Aucun secret en clair trouvé dans le code
cette fois. Corrigé en isolant `public/` (3 fichiers) comme seul dossier
publié.

## Fichiers legacy conservés en dépôt (non déployés, non exposés)

- Ancienne appli Flask (`app.py`, `templates/`, `Procfile`, `render.yaml`,
  `requirements.txt`, `data/`) — Render mort, plus utilisée. Supprimée du
  dépôt le 2026-07-08 (récupérable via l'historique Git).
- `setup.html`, `import.html` : outils d'amorçage SharePoint déjà exécutés,
  gardés en référence. Ne pas relancer sans vérifier l'état des listes.
- `teams_app/manifest.json` + `VisteSyndic-Teams.zip` : package Teams déjà
  uploadé, pointe vers l'URL Netlify. Toute modification du manifeste
  nécessite de régénérer le zip et de le ré-uploader dans le Centre
  d'administration Teams.
