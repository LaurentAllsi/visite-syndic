"""
Visite Syndic — Laforêt Montauban
Application de suivi des visites d'immeubles (syndic + conseil syndical)
"""

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, send_file, make_response)
import sqlite3, os, io, base64, json, time as _time, secrets as _sec
from datetime import datetime, date
from functools import wraps

try:
    import msal
    MSAL_AVAILABLE = True
except ImportError:
    MSAL_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'visite-syndic-laforet-2026')
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE']   = True

# ── Config ───────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(BASE_DIR, 'data')
DATABASE     = os.path.join(DATA_DIR, 'visite.db')
APP_PASSWORD = os.environ.get('APP_PASSWORD', 'laforet2024')
NOM_AGENCE   = "Laforêt Montauban — Syndic"

# DB facturation locale (import copropriétés) — chemin relatif à visite-app/
FACT_DB = os.environ.get(
    'FACT_DB',
    os.path.join(BASE_DIR, '..', 'facturation-app', 'data', 'syndic.db')
)

# ── Azure AD / MSAL (même config que facturation) ────────────────────────────
AZURE = {
    'client_id':     os.environ.get('AZURE_CLIENT_ID', ''),
    'client_secret': os.environ.get('AZURE_CLIENT_SECRET', ''),
    'tenant_id':     os.environ.get('AZURE_TENANT_ID', 'organizations'),
    'redirect_uri':  os.environ.get('AZURE_REDIRECT_URI',
                                    'http://localhost:5001/auth/callback'),
    'scope': ['User.Read'],
}

def msal_enabled():
    return MSAL_AVAILABLE and bool(AZURE['client_id'])

# Tokens temporaires pour le relais Teams popup → onglet principal
_teams_tokens: dict = {}

def _create_teams_token(user: str, email: str = '') -> str:
    tok = _sec.token_urlsafe(32)
    _teams_tokens[tok] = {'user': user, 'email': email, 'expires': _time.time() + 300}
    expired = [k for k, v in _teams_tokens.items() if _time.time() > v['expires']]
    for k in expired:
        _teams_tokens.pop(k, None)
    return tok

CATEGORIES = [
    "Parties communes", "Cage d'escalier", "Parking", "Toiture",
    "Local poubelles", "Espaces verts", "Boîtes aux lettres",
    "Ascenseur", "Façade", "Cave", "Autre"
]
URGENCES = [
    {"value": "urgent",    "label": "Urgent",      "emoji": "🔴", "badge": "danger"},
    {"value": "planifier", "label": "À planifier", "emoji": "🟠", "badge": "warning"},
    {"value": "info",      "label": "Pour info",   "emoji": "🟢", "badge": "success"},
]
ACTIONS = [
    "Aucune action",
    "Contacter un prestataire",
    "Signaler au conseil syndical",
    "Travaux à prévoir",
    "À surveiller",
]

os.makedirs(DATA_DIR, exist_ok=True)

# ── Base de données (SQLite local ou PostgreSQL sur Render) ──────────────────
try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2 = True
except ImportError:
    _PSYCOPG2 = False

_DATABASE_URL = os.environ.get('DATABASE_URL', '')
if _DATABASE_URL.startswith('postgres://'):
    _DATABASE_URL = 'postgresql://' + _DATABASE_URL[len('postgres://'):]
USE_PG = bool(_DATABASE_URL and _PSYCOPG2)

def get_db():
    if USE_PG:
        return psycopg2.connect(_DATABASE_URL)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db

def _pg_row(row):
    d = {}
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            d[k] = v.isoformat()[:10]
        else:
            d[k] = v
    return d

def qdb(sql, args=(), one=False):
    if USE_PG:
        conn = get_db()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql.replace('?', '%s'), args or ())
            raw = cur.fetchall()
        conn.close()
        rv = [_pg_row(r) for r in raw]
    else:
        db = get_db()
        rv = [dict(r) for r in db.execute(sql, args).fetchall()]
        db.close()
    return (rv[0] if rv else None) if one else rv

def edb(sql, args=()):
    if USE_PG:
        s = sql.replace('?', '%s')
        is_ins = s.strip().upper().startswith('INSERT')
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute((s + ' RETURNING id') if is_ins else s, args or ())
            lid = cur.fetchone()[0] if is_ins else None
        conn.commit(); conn.close()
        return lid
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    lid = cur.lastrowid
    db.close()
    return lid

SCHEMA_SQLITE = '''
CREATE TABLE IF NOT EXISTS coproprietes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom TEXT NOT NULL, adresse TEXT, nb_lots INTEGER DEFAULT 0,
    actif INTEGER DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS visites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    copropriete_id INTEGER REFERENCES coproprietes(id),
    copropriete_nom TEXT, date_visite DATE NOT NULL,
    heure_debut TEXT, heure_fin TEXT, redacteur TEXT NOT NULL,
    personnes_presentes TEXT, statut TEXT DEFAULT 'brouillon',
    notes_globales TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visite_id INTEGER REFERENCES visites(id) ON DELETE CASCADE,
    ordre INTEGER DEFAULT 0, categorie TEXT NOT NULL,
    categorie_custom TEXT, description TEXT,
    urgence TEXT DEFAULT 'planifier', action TEXT DEFAULT 'Aucune action',
    action_commentaire TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS observation_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER REFERENCES observations(id) ON DELETE CASCADE,
    photo_data TEXT NOT NULL, ordre INTEGER DEFAULT 0
);
'''

SCHEMA_PG = [
    """CREATE TABLE IF NOT EXISTS coproprietes (
        id SERIAL PRIMARY KEY, nom TEXT NOT NULL, adresse TEXT,
        nb_lots INTEGER DEFAULT 0, actif INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS visites (
        id SERIAL PRIMARY KEY,
        copropriete_id INTEGER REFERENCES coproprietes(id),
        copropriete_nom TEXT, date_visite DATE NOT NULL,
        heure_debut TEXT, heure_fin TEXT, redacteur TEXT NOT NULL,
        personnes_presentes TEXT, statut TEXT DEFAULT 'brouillon',
        notes_globales TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS observations (
        id SERIAL PRIMARY KEY,
        visite_id INTEGER REFERENCES visites(id) ON DELETE CASCADE,
        ordre INTEGER DEFAULT 0, categorie TEXT NOT NULL,
        categorie_custom TEXT, description TEXT,
        urgence TEXT DEFAULT 'planifier', action TEXT DEFAULT 'Aucune action',
        action_commentaire TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS observation_photos (
        id SERIAL PRIMARY KEY,
        observation_id INTEGER REFERENCES observations(id) ON DELETE CASCADE,
        photo_data TEXT NOT NULL, ordre INTEGER DEFAULT 0)""",
]

def init_db():
    if USE_PG:
        conn = get_db()
        with conn.cursor() as cur:
            for sql in SCHEMA_PG:
                cur.execute(sql)
        conn.commit(); conn.close()
    else:
        db = get_db()
        db.executescript(SCHEMA_SQLITE)
        db.commit(); db.close()

init_db()

# ── Auth ─────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        pwd  = request.form.get('password', '')
        user = request.form.get('nom', 'Collaborateur').strip() or 'Collaborateur'
        if pwd == APP_PASSWORD:
            session['user'] = user
            return redirect(request.args.get('next') or url_for('dashboard'))
        error = 'Mot de passe incorrect.'
    return render_template('login.html', error=error, msal_enabled=msal_enabled())

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Auth Microsoft (MSAL) — identique à facturation ──────────────────────────
def _msal_app():
    return msal.ConfidentialClientApplication(
        AZURE['client_id'],
        authority=f"https://login.microsoftonline.com/{AZURE['tenant_id']}",
        client_credential=AZURE['client_secret'],
    )

def _build_auth_url(from_teams=False):
    state = ('T:' if from_teams else 'W:') + _sec.token_hex(16)
    session['msal_state'] = state
    return _msal_app().get_authorization_request_url(
        AZURE['scope'], redirect_uri=AZURE['redirect_uri'], state=state)

@app.route('/login/microsoft')
def login_microsoft():
    if not msal_enabled():
        flash("Authentification Microsoft non configurée.", 'warning')
        return redirect(url_for('login'))
    return redirect(_build_auth_url(from_teams=False))

@app.route('/auth/teams-start')
def auth_teams_start():
    """Lance l'auth Microsoft depuis la popup Teams."""
    if not msal_enabled():
        return "msal_disabled", 400
    return redirect(_build_auth_url(from_teams=True))

@app.route('/auth/teams-end')
def auth_teams_end():
    """Page de fin popup — notifie le SDK Teams avec le token de passage."""
    token = request.args.get('token', '')
    error = request.args.get('error', '')
    return render_template('auth_teams_end.html', token=token, error=error)

@app.route('/auth/teams-complete')
def auth_teams_complete():
    """Échange le token de passage contre une session dans l'onglet Teams."""
    tok  = request.args.get('token', '')
    data = _teams_tokens.pop(tok, None)
    if not data or _time.time() > data['expires']:
        flash("Lien de connexion expiré, veuillez réessayer.", 'warning')
        return redirect(url_for('login'))
    session['user'] = data['user']
    return redirect(url_for('dashboard'))

@app.route('/auth/callback')
def auth_callback():
    if not msal_enabled():
        return redirect(url_for('login'))
    state      = request.args.get('state', '')
    from_teams = state.startswith('T:')   # encodé dans le state, pas dans la session
    error      = request.args.get('error')
    if error:
        msg = request.args.get('error_description', error)
        if from_teams:
            return redirect(url_for('auth_teams_end', error=msg))
        flash(f'Connexion Microsoft refusée : {msg}', 'danger')
        return redirect(url_for('login'))
    code = request.args.get('code')
    if not code:
        flash('Connexion Microsoft annulée.', 'warning')
        return redirect(url_for('login'))
    result = _msal_app().acquire_token_by_authorization_code(
        code, scopes=AZURE['scope'], redirect_uri=AZURE['redirect_uri'])
    if 'access_token' not in result:
        msg = result.get('error_description', 'Erreur inconnue')
        if from_teams:
            return redirect(url_for('auth_teams_end', error=msg))
        flash(f'Erreur Microsoft : {msg}', 'danger')
        return redirect(url_for('login'))
    claims = result.get('id_token_claims', {})
    user   = claims.get('name') or claims.get('preferred_username', 'Utilisateur')
    email  = claims.get('preferred_username', '')
    if from_teams:
        tok = _create_teams_token(user, email)
        return redirect(url_for('auth_teams_end', token=tok))
    session['user']       = user
    session['user_email'] = email
    return redirect(url_for('dashboard'))

# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    total_visites   = qdb('SELECT COUNT(*) n FROM visites', one=True)['n']
    visites_recentes = qdb(
        'SELECT * FROM visites ORDER BY date_visite DESC, id DESC LIMIT 5')
    nb_urgent = qdb(
        "SELECT COUNT(*) n FROM observations WHERE urgence='urgent' "
        "AND visite_id IN (SELECT id FROM visites WHERE statut='finalisee')", one=True)['n']
    total_copros = qdb('SELECT COUNT(*) n FROM coproprietes WHERE actif=1', one=True)['n']
    return render_template('dashboard.html',
                           total_visites=total_visites,
                           visites_recentes=visites_recentes,
                           nb_urgent=nb_urgent,
                           total_copros=total_copros)

# ── Visites — liste ───────────────────────────────────────────────────────────
@app.route('/visites')
@login_required
def visites_list():
    q     = request.args.get('q', '').strip()
    statut = request.args.get('statut', '')
    sql   = 'SELECT * FROM visites WHERE 1=1'
    args  = []
    if q:
        sql += ' AND (copropriete_nom LIKE ? OR redacteur LIKE ?)'
        args += [f'%{q}%', f'%{q}%']
    if statut:
        sql += ' AND statut = ?'
        args.append(statut)
    sql += ' ORDER BY date_visite DESC, id DESC'
    visites = qdb(sql, args)
    # Compter les observations urgentes par visite
    urgences = {}
    for v in visites:
        r = qdb("SELECT COUNT(*) n FROM observations WHERE visite_id=? AND urgence='urgent'",
                (v['id'],), one=True)
        urgences[v['id']] = r['n'] if r else 0
    return render_template('visites_list.html', visites=visites, urgences=urgences,
                           q=q, statut=statut)

# ── Nouvelle visite ───────────────────────────────────────────────────────────
@app.route('/visites/new', methods=['GET', 'POST'])
@login_required
def visite_new():
    copros = qdb('SELECT * FROM coproprietes WHERE actif=1 ORDER BY nom')
    if request.method == 'POST':
        copro_id = request.form.get('copropriete_id') or None
        copro_nom = request.form.get('copropriete_nom', '').strip()
        if copro_id:
            c = qdb('SELECT nom FROM coproprietes WHERE id=?', (copro_id,), one=True)
            if c:
                copro_nom = c['nom']
        if not copro_nom:
            flash('Veuillez saisir ou sélectionner une résidence.', 'danger')
            return render_template('visite_new.html', copros=copros)
        vid = edb(
            'INSERT INTO visites (copropriete_id, copropriete_nom, date_visite, '
            'heure_debut, redacteur, personnes_presentes, statut) VALUES (?,?,?,?,?,?,?)',
            (copro_id, copro_nom,
             request.form.get('date_visite') or date.today().isoformat(),
             request.form.get('heure_debut', ''),
             session['user'],
             request.form.get('personnes_presentes', ''),
             'brouillon')
        )
        return redirect(url_for('visite_obs', vid=vid))
    return render_template('visite_new.html', copros=copros)

# ── Observations ──────────────────────────────────────────────────────────────
@app.route('/visites/<int:vid>/observations')
@login_required
def visite_obs(vid):
    v    = qdb('SELECT * FROM visites WHERE id=?', (vid,), one=True)
    if not v:
        flash('Visite introuvable.', 'danger')
        return redirect(url_for('visites_list'))
    obs  = qdb('SELECT * FROM observations WHERE visite_id=? ORDER BY ordre, id', (vid,))
    obs_with_photos = []
    for o in obs:
        photos = qdb('SELECT id, ordre FROM observation_photos WHERE observation_id=? ORDER BY ordre',
                     (o['id'],))
        obs_with_photos.append({'obs': o, 'photos': photos})
    urgences_map = {u['value']: u for u in URGENCES}
    return render_template('visite_obs.html', v=v, obs_with_photos=obs_with_photos,
                           URGENCES=URGENCES, urgences_map=urgences_map)

# ── Formulaire observation ────────────────────────────────────────────────────
@app.route('/visites/<int:vid>/observations/new', methods=['GET', 'POST'])
@login_required
def obs_new(vid):
    v = qdb('SELECT * FROM visites WHERE id=?', (vid,), one=True)
    if not v:
        return redirect(url_for('visites_list'))
    if request.method == 'POST':
        cat        = request.form.get('categorie', CATEGORIES[0])
        cat_custom = request.form.get('categorie_custom', '').strip()
        desc       = request.form.get('description', '').strip()
        urgence    = request.form.get('urgence', 'planifier')
        action     = request.form.get('action', 'Aucune action')
        action_com = request.form.get('action_commentaire', '').strip()
        nb = qdb('SELECT COUNT(*) n FROM observations WHERE visite_id=?', (vid,), one=True)['n']
        oid = edb(
            'INSERT INTO observations (visite_id, ordre, categorie, categorie_custom, '
            'description, urgence, action, action_commentaire) VALUES (?,?,?,?,?,?,?,?)',
            (vid, nb, cat, cat_custom, desc, urgence, action, action_com)
        )
        # Photos
        for f in request.files.getlist('photos'):
            if f and f.filename:
                raw = f.read()
                if raw:
                    b64 = 'data:image/jpeg;base64,' + base64.b64encode(raw).decode()
                    edb('INSERT INTO observation_photos (observation_id, photo_data) VALUES (?,?)',
                        (oid, b64))
        edb('UPDATE visites SET updated_at=CURRENT_TIMESTAMP WHERE id=?', (vid,))
        return redirect(url_for('visite_obs', vid=vid))
    nb = qdb('SELECT COUNT(*) n FROM observations WHERE visite_id=?', (vid,), one=True)['n']
    return render_template('obs_form.html', v=v, obs=None,
                           CATEGORIES=CATEGORIES, URGENCES=URGENCES, ACTIONS=ACTIONS,
                           num=nb+1)

@app.route('/visites/<int:vid>/observations/<int:oid>/edit', methods=['GET', 'POST'])
@login_required
def obs_edit(vid, oid):
    v   = qdb('SELECT * FROM visites WHERE id=?', (vid,), one=True)
    obs = qdb('SELECT * FROM observations WHERE id=? AND visite_id=?', (oid, vid), one=True)
    if not v or not obs:
        return redirect(url_for('visite_obs', vid=vid))
    photos = qdb('SELECT * FROM observation_photos WHERE observation_id=? ORDER BY ordre', (oid,))
    if request.method == 'POST':
        edb(
            'UPDATE observations SET categorie=?, categorie_custom=?, description=?, '
            'urgence=?, action=?, action_commentaire=? WHERE id=?',
            (request.form.get('categorie'), request.form.get('categorie_custom','').strip(),
             request.form.get('description','').strip(),
             request.form.get('urgence','planifier'),
             request.form.get('action','Aucune action'),
             request.form.get('action_commentaire','').strip(), oid)
        )
        # Suppression photos cochées
        for pid in request.form.getlist('delete_photo'):
            edb('DELETE FROM observation_photos WHERE id=? AND observation_id=?', (pid, oid))
        # Nouvelles photos
        for f in request.files.getlist('photos'):
            if f and f.filename:
                raw = f.read()
                if raw:
                    b64 = 'data:image/jpeg;base64,' + base64.b64encode(raw).decode()
                    edb('INSERT INTO observation_photos (observation_id, photo_data) VALUES (?,?)',
                        (oid, b64))
        edb('UPDATE visites SET updated_at=CURRENT_TIMESTAMP WHERE id=?', (vid,))
        return redirect(url_for('visite_obs', vid=vid))
    return render_template('obs_form.html', v=v, obs=obs, photos=photos,
                           CATEGORIES=CATEGORIES, URGENCES=URGENCES, ACTIONS=ACTIONS,
                           num=obs['ordre']+1)

@app.route('/visites/<int:vid>/observations/<int:oid>/delete', methods=['POST'])
@login_required
def obs_delete(vid, oid):
    edb('DELETE FROM observations WHERE id=? AND visite_id=?', (oid, vid))
    edb('UPDATE visites SET updated_at=CURRENT_TIMESTAMP WHERE id=?', (vid,))
    return redirect(url_for('visite_obs', vid=vid))

# ── Clôture ───────────────────────────────────────────────────────────────────
@app.route('/visites/<int:vid>/cloturer', methods=['GET', 'POST'])
@login_required
def visite_cloturer(vid):
    v   = qdb('SELECT * FROM visites WHERE id=?', (vid,), one=True)
    if not v:
        return redirect(url_for('visites_list'))
    obs = qdb('SELECT * FROM observations WHERE visite_id=? ORDER BY ordre, id', (vid,))
    urgences_count = {}
    for u in URGENCES:
        urgences_count[u['value']] = sum(1 for o in obs if o['urgence'] == u['value'])
    if request.method == 'POST':
        heure_fin     = request.form.get('heure_fin', '').strip()
        notes_globales = request.form.get('notes_globales', '').strip()
        edb('UPDATE visites SET heure_fin=?, notes_globales=?, statut=?, '
            'updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (heure_fin, notes_globales, 'finalisee', vid))
        return redirect(url_for('visite_detail', vid=vid))
    return render_template('visite_cloturer.html', v=v, obs=obs,
                           urgences_count=urgences_count, URGENCES=URGENCES)

# ── Détail visite ─────────────────────────────────────────────────────────────
@app.route('/visites/<int:vid>')
@login_required
def visite_detail(vid):
    v   = qdb('SELECT * FROM visites WHERE id=?', (vid,), one=True)
    if not v:
        flash('Visite introuvable.', 'danger')
        return redirect(url_for('visites_list'))
    obs = qdb('SELECT * FROM observations WHERE visite_id=? ORDER BY ordre, id', (vid,))
    obs_with_photos = []
    for o in obs:
        photos = qdb('SELECT photo_data FROM observation_photos WHERE observation_id=? ORDER BY ordre',
                     (o['id'],))
        obs_with_photos.append({'obs': o, 'photos': photos})
    urgences_map = {u['value']: u for u in URGENCES}
    urgences_count = {u['value']: sum(1 for o in obs if o['urgence'] == u['value']) for u in URGENCES}
    return render_template('visite_detail.html', v=v,
                           obs_with_photos=obs_with_photos,
                           urgences_map=urgences_map,
                           urgences_count=urgences_count,
                           URGENCES=URGENCES)

# ── Suppression visite ────────────────────────────────────────────────────────
@app.route('/visites/<int:vid>/delete', methods=['POST'])
@login_required
def visite_delete(vid):
    edb('DELETE FROM visites WHERE id=?', (vid,))
    flash('Visite supprimée.', 'success')
    return redirect(url_for('visites_list'))

# ── Réouverture visite ────────────────────────────────────────────────────────
@app.route('/visites/<int:vid>/reopen', methods=['POST'])
@login_required
def visite_reopen(vid):
    edb("UPDATE visites SET statut='brouillon', updated_at=CURRENT_TIMESTAMP WHERE id=?", (vid,))
    return redirect(url_for('visite_obs', vid=vid))

# ── PDF ───────────────────────────────────────────────────────────────────────
@app.route('/visites/<int:vid>/pdf')
@login_required
def visite_pdf(vid):
    v   = qdb('SELECT * FROM visites WHERE id=?', (vid,), one=True)
    if not v:
        flash('Visite introuvable.', 'danger')
        return redirect(url_for('visites_list'))
    obs = qdb('SELECT * FROM observations WHERE visite_id=? ORDER BY ordre, id', (vid,))
    obs_with_photos = []
    for o in obs:
        photos = qdb('SELECT photo_data FROM observation_photos WHERE observation_id=? ORDER BY ordre',
                     (o['id'],))
        obs_with_photos.append({'obs': o, 'photos': [p['photo_data'] for p in photos]})

    urgences_map   = {u['value']: u for u in URGENCES}
    urgences_count = {u['value']: sum(1 for o in obs if o['urgence'] == u['value']) for u in URGENCES}

    # Essayer WeasyPrint, sinon retourner HTML imprimable
    html_str = render_template('rapport_pdf.html', v=v,
                               obs_with_photos=obs_with_photos,
                               urgences_map=urgences_map,
                               urgences_count=urgences_count,
                               URGENCES=URGENCES,
                               nom_agence=NOM_AGENCE,
                               now=datetime.now())
    try:
        from weasyprint import HTML as WP_HTML
        pdf_bytes = WP_HTML(string=html_str, base_url=request.host_url).write_pdf()
        nom_fichier = f"Visite_{v['copropriete_nom'].replace(' ','_')}_{v['date_visite']}.pdf"
        return send_file(io.BytesIO(pdf_bytes), mimetype='application/pdf',
                         as_attachment=True, download_name=nom_fichier)
    except ImportError:
        pass
    try:
        from xhtml2pdf import pisa
        pdf_buf = io.BytesIO()
        pisa.CreatePDF(html_str, dest=pdf_buf, encoding='utf-8')
        pdf_buf.seek(0)
        nom_fichier = f"Visite_{v['copropriete_nom'].replace(' ','_')}_{v['date_visite']}.pdf"
        return send_file(pdf_buf, mimetype='application/pdf',
                         as_attachment=True, download_name=nom_fichier)
    except ImportError:
        pass
    # Fallback : HTML imprimable dans le navigateur
    response = make_response(html_str)
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response

# ── Copropriétés ──────────────────────────────────────────────────────────────
@app.route('/coproprietes')
@login_required
def coproprietes_list():
    copros = qdb('SELECT * FROM coproprietes ORDER BY nom')
    return render_template('coproprietes_list.html', copros=copros)

@app.route('/coproprietes/new', methods=['GET', 'POST'])
@login_required
def copropriete_new():
    if request.method == 'POST':
        nom = request.form.get('nom', '').strip()
        if not nom:
            flash('Le nom est obligatoire.', 'danger')
            return render_template('copropriete_form.html', copro=None)
        edb('INSERT INTO coproprietes (nom, adresse, nb_lots) VALUES (?,?,?)',
            (nom, request.form.get('adresse','').strip(),
             int(request.form.get('nb_lots', 0) or 0)))
        flash(f'Copropriété « {nom} » créée.', 'success')
        return redirect(url_for('coproprietes_list'))
    return render_template('copropriete_form.html', copro=None)

@app.route('/coproprietes/<int:cid>/edit', methods=['GET', 'POST'])
@login_required
def copropriete_edit(cid):
    copro = qdb('SELECT * FROM coproprietes WHERE id=?', (cid,), one=True)
    if not copro:
        return redirect(url_for('coproprietes_list'))
    if request.method == 'POST':
        nom = request.form.get('nom', '').strip()
        if not nom:
            flash('Le nom est obligatoire.', 'danger')
            return render_template('copropriete_form.html', copro=copro)
        edb('UPDATE coproprietes SET nom=?, adresse=?, nb_lots=?, actif=? WHERE id=?',
            (nom, request.form.get('adresse','').strip(),
             int(request.form.get('nb_lots', 0) or 0),
             1 if request.form.get('actif') else 0, cid))
        flash(f'Copropriété « {nom} » mise à jour.', 'success')
        return redirect(url_for('coproprietes_list'))
    return render_template('copropriete_form.html', copro=copro)

@app.route('/coproprietes/<int:cid>/delete', methods=['POST'])
@login_required
def copropriete_delete(cid):
    edb('DELETE FROM coproprietes WHERE id=?', (cid,))
    flash('Copropriété supprimée.', 'success')
    return redirect(url_for('coproprietes_list'))

@app.route('/coproprietes/import-facturation', methods=['POST'])
@login_required
def import_from_facturation():
    """Importe les copropriétés depuis la base facturation."""
    fact_path = os.path.abspath(FACT_DB)
    if not os.path.exists(fact_path):
        flash('Base facturation introuvable. Vérifiez le chemin.', 'danger')
        return redirect(url_for('coproprietes_list'))
    try:
        fdb = sqlite3.connect(fact_path)
        fdb.row_factory = sqlite3.Row
        rows = fdb.execute('SELECT nom, adresse, nb_lots FROM coproprietes WHERE actif=1').fetchall()
        fdb.close()
        imported = 0
        for r in rows:
            existing = qdb('SELECT id FROM coproprietes WHERE nom=?', (r['nom'],), one=True)
            if not existing:
                edb('INSERT INTO coproprietes (nom, adresse, nb_lots) VALUES (?,?,?)',
                    (r['nom'], r['adresse'] or '', r['nb_lots'] or 0))
                imported += 1
        flash(f'{imported} copropriété(s) importée(s) depuis la facturation.', 'success')
    except Exception as e:
        flash(f'Erreur import : {e}', 'danger')
    return redirect(url_for('coproprietes_list'))

# ── Filtres Jinja ─────────────────────────────────────────────────────────────
@app.template_filter('fmt_date')
def fmt_date(d):
    if not d:
        return '—'
    try:
        return datetime.strptime(str(d)[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
    except Exception:
        return str(d)

@app.template_filter('fmt_date_long')
def fmt_date_long(d):
    if not d:
        return '—'
    try:
        JOURS = ['lundi','mardi','mercredi','jeudi','vendredi','samedi','dimanche']
        MOIS  = ['','janvier','février','mars','avril','mai','juin',
                 'juillet','août','septembre','octobre','novembre','décembre']
        dt = datetime.strptime(str(d)[:10], '%Y-%m-%d')
        return f"{JOURS[dt.weekday()]} {dt.day} {MOIS[dt.month]} {dt.year}"
    except Exception:
        return str(d)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
