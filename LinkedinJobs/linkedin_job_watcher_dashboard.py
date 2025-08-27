from flask import Flask, render_template_string, request, jsonify, g, redirect, url_for
import sqlite3
import os
import json
import unicodedata
from datetime import datetime

# ******************************
# FULL VIBE CODED DASHBOARD IN FLASK
# ******************************

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_DB_PATH = os.path.join(APP_DIR, 'jobs.db')          # fichier sqlite
JSON_PATH = os.path.join(APP_DIR, 'jobs_db.json')          # export/import JSON
STATS_PATH = os.path.join(APP_DIR, 'stats.json')          # stats (total_analyzed, retained)

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_db_json(db_dict):
    """Sauvegarde jobs_db.json (utilisé pour l'import/export)."""
    try:
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(db_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Erreur sauvegarde JSON:", e)

# ---------------- Database helpers ----------------

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(SQLITE_DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript('''
    CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        title TEXT,
        company TEXT,
        location TEXT,
        link TEXT,
        description_html TEXT,
        relevance_score INTEGER,
        reasons TEXT,
        analysis_raw TEXT,
        added_at TEXT,
        applied INTEGER DEFAULT 0,
        response TEXT DEFAULT NULL,
        source TEXT,
        is_deleted INTEGER DEFAULT 0
    );
    ''')
    db.commit()

def migrate_db_add_is_deleted():
    """
    Ajoute la colonne is_deleted si elle n'existe pas (safe migration).
    """
    db = get_db()
    cur = db.execute("PRAGMA table_info(jobs);")
    cols = [r["name"] for r in cur.fetchall()]
    if "is_deleted" not in cols:
        try:
            db.execute("ALTER TABLE jobs ADD COLUMN is_deleted INTEGER DEFAULT 0;")
            db.commit()
            print("[migrate] colonne is_deleted ajoutée")
        except Exception as e:
            print("[migrate] impossible d'ajouter is_deleted:", e)



def import_json_to_db():
    if not os.path.exists(JSON_PATH):
        return 0
    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except Exception:
            return 0

    db = get_db()
    inserted = 0
    # support dict or list format
    items = data.items() if isinstance(data, dict) else enumerate(data)
    for key, job in items:
        if isinstance(data, dict):
            j = job
            jid = key
        else:
            j = job
            jid = job.get('job_id') or job.get('link') or None

        jid = (j.get('job_id') or j.get('link') or jid) if isinstance(j, dict) else jid
        if not jid:
            jid = (j.get('title','') + '|' + j.get('company','')).strip()[:200]

        cur = db.execute('SELECT 1 FROM jobs WHERE job_id = ?', (jid,))
        if cur.fetchone():
            continue

        title = j.get('title') or ''
        company = j.get('company') or ''
        location = j.get('location') or ''
        # remove any characters that is before this exact string " - "
        location = location.split(" - ", 1)[-1] if " - " in location else location
        link = j.get('link') or ''
        desc = j.get('description_html') or ''
        source = j.get('source') or 'json'
        analysis = j.get('analysis') if isinstance(j.get('analysis'), dict) else None
        relevance = None
        reasons = None
        raw = None
        if analysis:
            raw = json.dumps(analysis, ensure_ascii=False)
            parsed = analysis.get('parsed') if isinstance(analysis.get('parsed'), dict) else analysis.get('parsed')
            if isinstance(parsed, dict):
                relevance = parsed.get('relevance_score')
                reasons = json.dumps(parsed.get('reasons'), ensure_ascii=False) if parsed.get('reasons') else None
        else:
            relevance = j.get('relevance_score')
            reasons = json.dumps(j.get('reasons'), ensure_ascii=False) if j.get('reasons') else None
            raw = json.dumps(j, ensure_ascii=False)

        added_at = j.get('scraped_at') or j.get('analyzed_at') or datetime.utcnow().isoformat() + 'Z'

        db.execute(
            'INSERT OR IGNORE INTO jobs (job_id, title, company, location, link, description_html, relevance_score, reasons, analysis_raw, added_at, applied, response, source)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)',
            (jid, title, company, location, link, desc, relevance, reasons, raw, added_at, source)
        )
        inserted += 1
    db.commit()
    return inserted

# -------------------------------------------------------
def _normalize_text(s):
    """Normalise une chaîne: retire accents, met en minuscule."""
    if not s:
        return ""
    # décompose accents puis enlève ce qui n'est pas ascii
    s2 = unicodedata.normalize('NFKD', s)
    return s2.encode('ascii', 'ignore').decode('ascii').lower()

def role_letter_from_title(title):
    """
    Retourne la lettre selon les règles:
      - contient 'dev' ou 'dév' -> 'S' (software dev)
      - contient 'data' -> 'D'
      - contient 'ops' -> 'O'
      - contient 'ana' -> 'A' (analyste)
      - sinon -> 'IT'
    ignore case, ignore accents.
    """
    t = _normalize_text(title)
    if 'dev' in t:   # couvre dev / Dév / developer
        return 'S'
    if 'data' in t:
        return 'D'
    if 'ops' in t or 'operation' in t:  # couvre ops / operations
        return 'O'
    if 'ana' in t or 'analyst' in t or 'analyse' in t:
        return 'A'
    return 'IT'



# ---------------- Routes & Template ----------------

INDEX_HTML = '''
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>LinkedIn Job Dashboard</title>
  <!-- Tailwind -->
  <script src="https://cdn.tailwindcss.com"></script>
  <!-- Chart.js -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { background: #f7fafc; color: #0f172a; }
    .card { background: white; border-radius: 12px; box-shadow: 0 6px 18px rgba(15,23,42,0.06); }
    .muted { color: #475569; }
    .job-card:hover { transform: translateY(-4px); transition: all .18s ease; box-shadow: 0 10px 24px rgba(2,6,23,0.08); }
    .small { font-size: 0.85rem; color: #64748b; }
    .pill { padding: 6px 10px; border-radius: 999px; font-weight:600; font-size:0.78rem; }
  </style>
</head>
<body>
  <div class="max-w-7xl mx-auto p-6">
    <header class="flex items-center justify-between mb-6">
      <div class="flex items-center gap-4">
        <!-- Simple icon -->
        <div class="p-3 bg-white rounded-lg shadow">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-8 w-8 text-indigo-600" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 7V3m8 4V3M3 11h18M5 21h14a2 2 0 0 0 2-2v-7H3v7a2 2 0 0 0 2 2z"/></svg>
        </div>
        <div>
          <h1 class="text-2xl font-extrabold">LinkedIn Job Dashboard</h1>
          <div class="text-sm muted">Suivi des offres capturées et statistiques</div>
        </div>
      </div>

      <div class="flex items-center gap-3">
        <button onclick="refreshServer()" class="px-4 py-2 bg-indigo-600 text-white rounded-lg shadow hover:bg-indigo-700 flex items-center gap-2">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v6h6M20 20v-6h-6M5 19a9 9 0 1 0 0-14"/></svg>
          Recharger JSON
        </button>
      </div>
    </header>

    <div class="grid grid-cols-3 gap-6">
      <!-- LEFT: Jobs list (span 2) -->
      <section class="col-span-2">
        <!-- Filters row -->
        <div class="card p-4 mb-6 flex items-center justify-between gap-4">
          <div class="flex items-center gap-3">
            <label class="small">Filtrer</label>
            <select id="filter" class="border rounded p-2 bg-white text-gray-800" onchange="applyFilters()">
              <option value="all" {% if filter_opt=='all' %}selected{% endif %}>Toutes</option>
              <option value="not_applied" {% if filter_opt=='not_applied' %}selected{% endif %}>Non postulé</option>
              <option value="applied" {% if filter_opt=='applied' %}selected{% endif %}>Postulé</option>
              <option value="accepted" {% if filter_opt=='accepted' %}selected{% endif %}>Accepté</option>
              <option value="rejected" {% if filter_opt=='rejected' %}selected{% endif %}>Refusé</option>
            </select>

            <label class="small">Trier</label>
            <select id="sort" class="border rounded p-2 bg-white text-gray-800" onchange="applyFilters()">
              <option value="newest" {% if sort_opt=='newest' %}selected{% endif %}>Plus récentes</option>
              <option value="oldest" {% if sort_opt=='oldest' %}selected{% endif %}>Les plus anciennes</option>
              <option value="relevance" {% if sort_opt=='relevance' %}selected{% endif %}>Par pertinence</option>
            </select>
          </div>

          <div class="flex items-center gap-3">
            <div class="text-sm muted">Base: <code>{{ sqlite_path }}</code></div>
          </div>
        </div>

        <!-- Jobs -->
        <div id="list" class="space-y-4">
          {% for job in jobs %}
          <article id="job-{{ loop.index0 }}" data-job-id="{{ job['job_id']|e }}" class="job-card card p-4 flex items-start gap-4">
          {# --- avatar avec couleur selon role_letter --- #}
          {% set rl = job.get('role_letter', 'IT') %}
          {% if rl == 'S' %}
            <div class="w-14 h-14 rounded-md bg-indigo-50 flex items-center justify-center">
              <div class="text-indigo-600 font-bold text-lg">{{ rl }}</div>
            </div>
          {% elif rl == 'D' %}
            <div class="w-14 h-14 rounded-md bg-emerald-50 flex items-center justify-center">
              <div class="text-emerald-600 font-bold text-lg">{{ rl }}</div>
            </div>
          {% elif rl == 'O' %}
            <div class="w-14 h-14 rounded-md bg-yellow-50 flex items-center justify-center">
              <div class="text-yellow-600 font-bold text-lg">{{ rl }}</div>
            </div>
          {% elif rl == 'A' %}
            <div class="w-14 h-14 rounded-md bg-teal-50 flex items-center justify-center">
              <div class="text-teal-600 font-bold text-lg">{{ rl }}</div>
            </div>
          {% else %}
            <div class="w-14 h-14 rounded-md bg-gray-100 flex items-center justify-center">
              <div class="text-gray-800 font-bold text-lg">{{ rl }}</div>
            </div>
          {% endif %}


            <div class="flex-1">
              <div class="flex items-start justify-between gap-4">
                <div>
                  <a href="{{ job['link'] }}" target="_blank" class="text-lg font-semibold text-gray-900 hover:text-indigo-600">{{ job['title'] }}</a>
                  <div class="text-sm muted mt-1">{{ job['company'] }} • {{ job['location'] }}</div>
                </div>
                <div class="text-right">
                  <div class="text-sm muted">Ajouté</div>
                  <div class="font-medium">{{ job['added_at'] }}</div>
                </div>
              </div>

              <div class="mt-3 flex items-start gap-4">
                <div class="flex-1 text-sm muted">
                  {% if job['reasons'] %}
                    <div class="text-xs font-medium text-gray-600">Raisons (LLM)</div>
                    <ul class="list-disc ml-5 text-sm text-gray-700">{% for r in job['reasons'] %}<li>{{ r }}</li>{% endfor %}</ul>
                  {% else %}
                    <div class="text-xs text-gray-500">Aucune raison fournie.</div>
                  {% endif %}
                </div>

                <div class="w-44 flex flex-col gap-2">
                  <button onclick="toggleApplied({{ loop.index0 }})" id="applied-btn-{{ loop.index0 }}" class="px-3 py-2 rounded-lg text-sm {{ 'bg-emerald-600 text-white' if job['applied'] else 'bg-gray-100 text-gray-800' }}">
                    {{ 'Postulé' if job['applied'] else 'Marquer postulé' }}
                  </button>

                  <select onchange="setResponse({{ loop.index0 }}, this.value)" id="resp-{{ loop.index0 }}" class="border rounded p-2 bg-white text-gray-800 w-full">
                    <option value="none" {{ 'selected' if not job['response'] }}>Réponse ?</option>
                    <option value="accepted" {{ 'selected' if job['response']=='accepted' }}>Accepté</option>
                    <option value="rejected" {{ 'selected' if job['response']=='rejected' }}>Refusé</option>
                  </select>

                  <button onclick="openLink({{ loop.index0 }})" class="px-3 py-2 rounded-lg bg-indigo-600 text-white">Ouvrir</button>
                </div>
              </div>

              <div class="mt-3 flex items-center justify-between">
                <div class="flex items-center gap-2">
                  
                  <button onclick="confirmDelete({{ loop.index0 }})" title="Supprimer" class="p-2 rounded bg-rose-50 hover:bg-rose-100" aria-label="Supprimer">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-rose-600" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                        d="M3 6h18M8 6v12a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2V6M10 6V4a2 2 0 0 1 2-2h0a2 2 0 0 1 2 2v2" />
                    </svg>
                  </button>

                  {% if job['applied'] %}<span class="ml-2 px-2 py-1 text-xs rounded bg-emerald-100 text-emerald-800">Postulé</span>{% endif %}
                  {% if job['response']=='accepted' %}<span class="ml-2 px-2 py-1 text-xs rounded bg-emerald-100 text-emerald-800">Accepté</span>{% elif job['response']=='rejected' %}<span class="ml-2 px-2 py-1 text-xs rounded bg-rose-100 text-rose-800">Refusé</span>{% endif %}
                </div>

                <div class="text-sm muted">Pertinence: <strong>{{ job['relevance_score'] or '-' }}</strong></div>
              </div>
            </div>
          </article>
          {% endfor %}

          {% if not jobs %}
          <div class="card p-6 text-center text-gray-600">Aucune offre trouvée — essaye de recharger le JSON.</div>
          {% endif %}
        </div>
      </section>

      <!-- RIGHT: Statistics -->
      <aside class="col-span-1 space-y-4">
        <div class="card p-4">
          <div class="flex items-center justify-between">
            <div>
              <div class="text-xs muted">Offres analysées</div>
              <div class="text-3xl font-bold">{{ total_analyzed }}</div>
            </div>
            <div>
              <div class="text-xs muted">Retenues</div>
              <div class="text-2xl font-semibold text-indigo-600">{{ retained }}</div>
            </div>
          </div>

          <div class="mt-4 grid grid-cols-2 gap-3">
            <div class="text-sm muted">Acceptées<br><strong>{{ accepted }}</strong></div>
            <div class="text-sm muted">Refusées<br><strong>{{ rejected }}</strong></div>
          </div>
        </div>

        <div class="card p-4">
          <div class="flex items-center justify-between mb-3">
            <div class="font-medium">Réponses</div>
            <div class="small muted">acceptées / refusées</div>
          </div>
          <canvas id="responsesChart" height="220"></canvas>
        </div>

        <div class="card p-4">
          <div class="flex items-center justify-between mb-3">
            <div class="font-medium">Analysées vs Retenues</div>
            <div class="small muted">aperçu global</div>
          </div>
          <canvas id="analyzedChart" height="160"></canvas>
        </div>

        <div class="card p-4">
          <div class="font-medium mb-2">Stats rapides</div>
          <ul class="text-sm muted space-y-2">
            <li>Réponses totales : <strong>{{ responses }}</strong></li>
            <li>Base SQLite : <code class="text-xs">{{ sqlite_path }}</code></li>
            <li>Dernière mise à jour : <code class="text-xs">{{ stats_last_updated or '-' }}</code></li>
          </ul>
        </div>
      </aside>
    </div>

    <footer class="mt-8 text-sm muted">© Job Monitor • <a href="/admin" class="text-indigo-600">Admin</a></footer>
  </div>

<script>
function applyFilters(){
  const f = document.getElementById('filter').value;
  const s = document.getElementById('sort').value;
  const params = new URLSearchParams(window.location.search);
  params.set('filter', f);
  params.set('sort', s);
  window.location.search = params.toString();
}

function openLink(idx){
  const art = document.getElementById('job-' + idx);
  const a = art.querySelector('a[target="_blank"]');
  if(a) window.open(a.href, '_blank');
}

async function confirmDelete(idx){
  const art = document.getElementById('job-' + idx);
  const job_id = art.getAttribute('data-job-id');
  if(!confirm('Supprimer cette offre ?')) return;
  const res = await fetch('/api/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({job_id: job_id})});
  if(res.ok){ art.remove(); } else { alert('Erreur lors de la suppression'); }
}

async function toggleApplied(idx){
  const art = document.getElementById('job-' + idx);
  const job_id = art.getAttribute('data-job-id');
  const res = await fetch('/api/toggle_applied/' + encodeURIComponent(job_id), { method: 'POST' });
  if(res.ok){ const j = await res.json(); const btn = document.getElementById('applied-btn-' + idx); if(j.applied){ btn.classList.remove('bg-gray-100'); btn.classList.add('bg-emerald-600'); btn.classList.remove('text-gray-800'); btn.classList.add('text-white'); btn.textContent = 'Postulé'; } else { btn.classList.remove('bg-emerald-600'); btn.classList.add('bg-gray-100'); btn.classList.remove('text-white'); btn.classList.add('text-gray-800'); btn.textContent = 'Marquer postulé'; } } else alert('Erreur');
}

async function setResponse(idx, value){
  const art = document.getElementById('job-' + idx);
  const job_id = art.getAttribute('data-job-id');
  if(value === 'none') value = null;
  const res = await fetch('/api/set_response', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({job_id: job_id, response: value})});
  if(res.ok){ location.reload(); } else alert('Erreur');
}

async function refreshServer(){
  const res = await fetch('/api/refresh', {method:'POST'});
  if(res.ok){ const j = await res.json(); alert('Importés: ' + j.inserted); location.reload(); } else alert('Erreur');
}

// === Charts ===
const responsesData = {
  accepted: {{ accepted|tojson }},
  rejected: {{ rejected|tojson }},
  pending: {{ pending|tojson }}
};

const totalAnalyzed = {{ total_analyzed|tojson }};
const retainedCount = {{ retained|tojson }};

document.addEventListener('DOMContentLoaded', function(){
  // Responses donut
  const ctx1 = document.getElementById('responsesChart').getContext('2d');
  new Chart(ctx1, {
    type: 'doughnut',
    data: {
      labels: ['Acceptées', 'Refusées', 'Sans réponse'],
      datasets: [{
        data: [responsesData.accepted, responsesData.rejected, responsesData.pending],
        backgroundColor: ['#06b6d4', '#fb7185', '#c7d2fe'],
        borderWidth: 0
      }]
    },
    options: {
      plugins: { legend: { position: 'bottom' } }
    }
  });

  // Analyzed vs retained
  const ctx2 = document.getElementById('analyzedChart').getContext('2d');
  new Chart(ctx2, {
    type: 'bar',
    data: {
      labels: ['Analysées', 'Retenues'],
      datasets: [{
        label: 'Nombre',
        data: [totalAnalyzed, retainedCount],
        backgroundColor: ['#6366f1', '#06b6d4']
      }]
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true } }
    }
  });
});
</script>
</body>
</html>
'''

@app.route('/')
def index():
    filter_opt = request.args.get('filter', 'all')
    sort_opt = request.args.get('sort', 'newest')
    db = get_db()

    where = []
    params = []
    where.append('is_deleted = 0')
    if filter_opt == 'not_applied':
        where.append('applied = 0')
    elif filter_opt == 'applied':
        where.append('applied = 1')
    elif filter_opt == 'accepted':
        where.append("response = 'accepted'")
    elif filter_opt == 'rejected':
        where.append("response = 'rejected'")

    order = 'added_at DESC'
    if sort_opt == 'oldest':
        order = 'added_at ASC'
    elif sort_opt == 'relevance':
        order = 'COALESCE(relevance_score,0) DESC'

    q = 'SELECT * FROM jobs' + (' WHERE ' + ' AND '.join(where) if where else '') + f' ORDER BY {order} LIMIT 1000'
    cur = db.execute(q, params)
    rows = cur.fetchall()

    jobs = []
    for r in rows:
        reasons = None
        try:
            reasons = json.loads(r['reasons']) if r['reasons'] else None
        except Exception:
            reasons = None
        # Format date
        raw_date = r['added_at']
        try:
            dt = datetime.fromisoformat(raw_date.replace('Z', ''))
            added_at_fmt = dt.strftime('%d-%m-%Y %H:%M')
        except Exception:
            added_at_fmt = raw_date
        role_letter = role_letter_from_title(r['title'] or '')
        jobs.append({
            'job_id': r['job_id'],
            'title': r['title'],
            'company': r['company'],
            'location': r['location'],
            'link': r['link'],
            'description_html': r['description_html'] or '',
            'relevance_score': r['relevance_score'],
            'reasons': reasons,
            'added_at': added_at_fmt,
            'applied': bool(r['applied']),
            'response': r['response'],
            'role_letter': role_letter
        })


    # total analysées toujours lu depuis stats.json (si tu veux garder ce compteur LLM)
    stats = load_json(STATS_PATH)
    total_analyzed = int(stats.get('total_analyzed', 0) or 0)

    # retained = nombre d'offres actuellement présentes en base et NON supprimées
    cur_ret = db.execute("SELECT COUNT(*) as c FROM jobs WHERE is_deleted = 0")
    retained = int(cur_ret.fetchone()['c'] or 0)
    stats_last_updated = stats.get('last_updated')

    # compute accepted / rejected counts from DB (live)
    cur_acc = db.execute("SELECT COUNT(*) as c FROM jobs WHERE response = 'accepted' AND is_deleted = 0")
    accepted = int(cur_acc.fetchone()['c'] or 0)
    cur_rej = db.execute("SELECT COUNT(*) as c FROM jobs WHERE response = 'rejected' AND is_deleted = 0")
    rejected = int(cur_rej.fetchone()['c'] or 0)
    # applied count (useful in stats)
    cur_applied = db.execute("SELECT COUNT(*) as c FROM jobs WHERE applied = 1 AND is_deleted = 0")
    applied_count = int(cur_applied.fetchone()['c'] or 0)
    # jobs with no response (applied but not accepted/rejected)
    cur_pending = db.execute("SELECT COUNT(*) as c FROM jobs WHERE applied = 1 AND response IS NULL AND is_deleted = 0")
    pending = int(cur_pending.fetchone()['c'] or 0)
    responses = accepted + rejected + pending

    return render_template_string(INDEX_HTML,
                                  jobs=jobs,
                                  sqlite_path=SQLITE_DB_PATH,
                                  filter_opt=filter_opt,
                                  sort_opt=sort_opt,
                                  total_analyzed=total_analyzed,
                                  retained=retained,
                                  accepted=accepted,
                                  rejected=rejected,
                                  responses=responses,
                                  pending=pending,
                                  stats_last_updated=stats_last_updated,
                                  applied_count=applied_count)


@app.route('/api/toggle_applied/<path:job_id>', methods=['POST'])
def api_toggle_applied(job_id):
    db = get_db()
    cur = db.execute('SELECT applied FROM jobs WHERE job_id = ?', (job_id,))
    r = cur.fetchone()
    if not r:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    newval = 0 if r['applied'] else 1
    db.execute('UPDATE jobs SET applied = ? WHERE job_id = ?', (newval, job_id))
    db.commit()
    return jsonify({'ok': True, 'applied': bool(newval)})


@app.route('/api/set_response', methods=['POST'])
def api_set_response():
    data = request.get_json(force=True)
    job_id = data.get('job_id')
    response = data.get('response')
    if response not in (None, 'accepted', 'rejected'):
        return jsonify({'ok': False, 'error': 'bad_value'}), 400
    db = get_db()
    cur = db.execute('SELECT 1 FROM jobs WHERE job_id = ?', (job_id,))
    if not cur.fetchone():
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    db.execute('UPDATE jobs SET response = ? WHERE job_id = ?', (response, job_id))
    db.commit()
    return jsonify({'ok': True, 'response': response})


@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    inserted = import_json_to_db()
    return jsonify({'ok': True, 'inserted': inserted})


@app.route('/api/delete', methods=['POST'])
def api_delete():
    data = request.get_json(force=True)
    job_id = data.get('job_id')
    if not job_id:
        return jsonify({'ok': False, 'error': 'missing_job_id'}), 400
    db = get_db()
    cur = db.execute('SELECT 1 FROM jobs WHERE job_id = ?', (job_id,))
    if not cur.fetchone():
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    db.execute('UPDATE jobs SET is_deleted = 1 WHERE job_id = ?', (job_id,))
    db.commit()
    return jsonify({'ok': True})


@app.route('/admin')
def admin():
    return redirect(url_for('index'))


# ------------- CLI startup --------------
if __name__ == '__main__':
    # ensure sqlite db file exists
    if not os.path.exists(SQLITE_DB_PATH):
        open(SQLITE_DB_PATH, 'a').close()
    with app.app_context():
        init_db()
        migrate_db_add_is_deleted()
        inserted = import_json_to_db()
        if inserted:
            print(f'[init] imported {inserted} new jobs from jobs_db.json')
        else:
            print('[init] no jobs imported (file missing or already synced)')

    print('Serveur démarré sur http://127.0.0.1:5000')
    app.run(debug=True)
