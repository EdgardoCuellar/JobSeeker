#!/usr/bin/env python3
import time
import json
import re
import threading
import queue
import os
from datetime import datetime
import hashlib
from urllib.parse import urlparse, parse_qs

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.firefox_profile import FirefoxProfile


# LM Studio / OpenAI local client
# Best gpt-oss-20b or on small config google/gemma-3n-e4b
from openai import OpenAI
client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
MODEL_NAME = "google/gemma-3n-e4b"

current_fp_global = None

# ----------------- CONFIG -----------------
# Linkedin Search Link
LINKEDIN_SEARCH_URL = ""
with open("linkedin_search_url.txt", "r", encoding="utf-8") as f:
    LINKEDIN_SEARCH_URL = f.read().strip()
DB_PATH = "jobs_db.json"
ANALYSIS_WORKERS = 2
POLL_INTERVAL = 0.8  # secondes
FIREFOX_PROFILE_PATH = "E:\ROAMING\Mozilla\Firefox\Profiles\ojqxo9xy.dev-edition-default" 

# ****************************************************
# Contexte utilisateur envoyé au LLM
# Le contexte doit absoulment renvoyer NON ou OUI à la fin, dans la première ligne du prompt
# ****************************************************

USER_CONTEXT = ""
with open("user_context.txt", "r", encoding="utf-8") as f:
  USER_CONTEXT = f.read()

# ---------- STATS ----------
STATS_PATH = "stats.json"
stats_lock = threading.Lock()
stats = {
    "total_analyzed": 0,
    "retained": 0,
    "last_updated": None
}

def load_stats():
    global stats
    try:
        if os.path.exists(STATS_PATH):
            with open(STATS_PATH, "r", encoding="utf-8") as f:
                stats = json.load(f)
    except Exception:
        pass

def save_stats():
    global stats
    try:
        stats["last_updated"] = datetime.utcnow().isoformat() + "Z"
        with open(STATS_PATH, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[stats] save error:", e)

# charger au démarrage
load_stats()

# ------------------------------------------

processing_queue = queue.Queue()
db_lock = threading.Lock()

# ---------------- JS WATCHER (V3) ----------------
# Ce JS retourne "injectedV3" si l'injection a pu être faite.
JS_WATCHER = r"""
return (function(){
  if (window.__jobWatcherInjectedV3) { return "already"; }

  // cookie / modal handling heuristique (tentative)
  try {
    var ot = document.querySelector('#onetrust-accept-btn-handler');
    if (ot) { ot.click(); }
  } catch(e){}

  try {
    var btns = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"]'));
    var re = /^(accept|agree|tout accepter|accepter|continuer|ok|got it|autoriser|j'accepte)$/i;
    for (var b of btns) {
      var t = (b.innerText||b.value||"").trim();
      if (t && re.test(t) && b.offsetParent !== null) {
        try { b.click(); break; } catch(e){}
      }
      var al = (b.getAttribute && (b.getAttribute('aria-label')||"")).trim();
      if (al && re.test(al) && b.offsetParent !== null) {
        try { b.click(); break; } catch(e){}
      }
    }
  } catch(e){}

  // install watcher V3
  window.__jobWatcherInjectedV3 = true;
  window.__jobQueue = window.__jobQueue || [];
  window.__jobWatcherLogs = window.__jobWatcherLogs || [];

  // Priorité de selecteurs : on teste les plus spécifiques AVANT le h1 général
  const TITLE_SELECTORS = ['[data-test-job-title]', '.jobs-unified-top-card__job-title', '.topcard__title', 'h1'];
  const COMPANY_SELECTORS = ['[data-test-company-name]', '.jobs-unified-top-card__company-name a', '.topcard__org-name-link', 'a[href*="/company/"]', 'a[href*="/cmp/"]'];
  const LOCATION_SELECTORS = ['[data-test-job-location]', '.jobs-unified-top-card__workplace-location', '.jobs-unified-top-card__bullet', '.jobs-unified-top-card__subtitle', '.jobs-search-box__container'];
  const DESC_SELECTORS = ['.jobs-description__container', '.show-more-less-html__markup', '.jobs-description-content__text', '.description__text'];

  function firstTextWithin(root, selectors){
    for(const s of selectors){
      try{ const el = root.querySelector(s); if(el && el.innerText && el.innerText.trim()) return el.innerText.trim(); } catch(e){}
    }
    return "";
  }
  function firstHtmlWithin(root, selectors){
    for(const s of selectors){
      try{ const el = root.querySelector(s); if(el && (el.innerHTML || el.innerText)) return el.innerHTML || el.innerText || ""; } catch(e){}
    }
    return "";
  }

  function findCompany(root, titleEl){
    for(const s of COMPANY_SELECTORS){
      try{
        const el = root.querySelector(s);
        if(el){
          const t = el.innerText.trim();
          if(t) return {company:t, method:"selector:"+s};
        }
      }catch(e){}
    }
    try{
      const anchors = Array.from(root.querySelectorAll('a[href]'));
      for(const a of anchors){
        const href = a.getAttribute('href') || "";
        if(/\/company\/|\/cmp\//i.test(href) && a.innerText.trim()){
          return {company: a.innerText.trim(), method:"anchor-company-href"};
        }
      }
    }catch(e){}
    try{
      if(titleEl){
        let node = titleEl;
        const p = node.parentElement;
        if(p){
          for(const ch of Array.from(p.children)){
            if(ch !== node){
              const t = (ch.innerText||"").trim();
              if(t && t.length < 80 && !t.includes("\n") && t !== titleEl.innerText.trim()){
                return {company: t, method:"near-title-sibling"};
              }
            }
          }
        }
      }
    }catch(e){}
    return {company:"", method:"not-found"};
  }

  // Detect and parse search-header patterns like:
  // FR: "203 offres d’emploi pour Ingénieur Logiciel dans le lieu sélectionné (Région de Bruxelles-Capitale, Belgique)"
  // EN: "203 jobs for Software Engineer in the selected location (Brussels-Capital Region, Belgium)"
  function tryParseSearchHeader(text){
    if(!text) return null;
    // French
    var fr = text.match(/(?:\d+\s+offres?[^\\n]*?pour\\s+)(.+?)\\s+dans le lieu sélectionn[ée]\\s*\\((.+?)\\)/i);
    if(fr){
      return {title: fr[1].trim(), location: fr[2].trim(), kind: 'fr-header'};
    }
    // English
    var en = text.match(/(?:\d+\\s+jobs?[^\\n]*?for\\s+)(.+?)\\s+in the selected location\\s*\\((.+?)\\)/i);
    if(en){
      return {title: en[1].trim(), location: en[2].trim(), kind: 'en-header'};
    }
    // fallback: parentheses at end -> treat as location
    var par = text.match(/^(.*)\\s*\\(([^)]+)\\)\\s*$/);
    if(par && par[2].length < 120){
      return {title: par[1].trim(), location: par[2].trim(), kind: 'parens'};
    }
    return null;
  }

  function getRightPaneDetail(){
    const paneCandidates = ['.jobs-search__job-details--container', '.jobs-details__main-content', '.jobs-unified-top-card', '.jobs-details__content', '.jobs-search__right-rail'];
    let root = null;
    for (var s of paneCandidates) { try { let el = document.querySelector(s); if(el){ root = el; break; } } catch(e){} }
    if(!root) root = document;

    // Try title using prioritized selectors (specific first)
    let titleEl = null;
    for(const s of TITLE_SELECTORS){
      try{
        const el = root.querySelector(s);
        if(el && el.innerText && el.innerText.trim()){
          titleEl = el;
          break;
        }
      } catch(e){}
    }
    if(!titleEl){
      // no title found in pane
      return null;
    }

    var rawTitle = titleEl.innerText.trim();
    var parsedHeader = tryParseSearchHeader(rawTitle);

    var title = rawTitle;
    var location = firstTextWithin(root, LOCATION_SELECTORS) || "";

    // If the title is actually a search header, extract role and location from it
    if(parsedHeader && parsedHeader.title){
      title = parsedHeader.title;
      if(!location && parsedHeader.location) location = parsedHeader.location;
      // small cleanup: sometimes title still contains separators
      title = title.replace(/^Offres?\\s+d[’']emploi\\s+pour\\s+/i, '').trim();
    }

    // Another fallback: if location still empty, try to find it near header or in document
    if(!location){
      try {
        // look for any small element with 'Région' / 'Region' / country words in pane
        const textCandidates = Array.from(root.querySelectorAll('span, div, p, li')).map(n=> (n.innerText||'').trim()).filter(Boolean);
        for(const t of textCandidates){
          if(/Région|Region|Belgique|Belgium|Bruxelles|Brussels|Bruxelles-Capitale|Capital/i.test(t) && t.length < 120){
            location = t; break;
          }
        }
      } catch(e){}
    }

    const companyObj = findCompany(root, titleEl);
    const company = companyObj.company || "";
    const companyMethod = companyObj.method || "";
    const description_html = firstHtmlWithin(root, DESC_SELECTORS) || root.innerHTML || "";
    const link = window.location.href;
    let job_id = "";
    try{
      const m = link.match(/currentJobId=(\d+)/) || link.match(/jobs\/view\/(\d+)/) || link.match(/position=(\d+)/) || link.match(/jobId=(\d+)/);
      if(m) job_id = m[1];
    }catch(e){}

    return { title: title, company: company, company_method: companyMethod, location: location, description_html: description_html, link: link, job_id: job_id, ts: Date.now() };
  }

  function makeHash(obj){
    const s = (obj.title||"")+"|"+(obj.company||"")+"|"+((obj.description_html||"").slice(0,300));
    let h = 0;
    for(let i=0;i<s.length;i++){ h = ((h<<5)-h) + s.charCodeAt(i); h |= 0; }
    return h;
  }

  let lastHash = null;
  function pushIfNew(d){
    try{
      if(!d) return;
      const h = makeHash(d);
      if(h !== lastHash){
        lastHash = h;
        window.__jobQueue.push(d);
        window.__jobWatcherLogs.push({event:"pushed", title:d.title, company:d.company, method:d.company_method, ts:Date.now()});
        console.log("[jobWatcherV3] pushed:", d.title, "@", d.company || "<empty>", "| location:", d.location || "<empty>");
      }
    }catch(e){ console.error(e); }
  }

  const triggerNow = function(){ try{ const d = getRightPaneDetail(); pushIfNew(d);}catch(e){} };
  document.addEventListener('click', function(){ setTimeout(triggerNow, 200); }, true);
  document.addEventListener('mousedown', function(){ setTimeout(triggerNow, 300); }, true);
  document.addEventListener('mouseup', function(){ setTimeout(triggerNow, 200); }, true);
  window.addEventListener('keydown', function(ev){ if(ev.shiftKey && ev.key.toLowerCase()==='s'){ const d = getRightPaneDetail(); if(d){ pushIfNew(d); console.log("[jobWatcherV3] manual push:", d.title); } } });

  const mo = new MutationObserver(function(muts){ try{ triggerNow(); }catch(e){} });
  mo.observe(document.body, { childList: true, subtree: true });

  (function(history){
    const origPush = history.pushState;
    const origReplace = history.replaceState;
    history.pushState = function(){ const res = origPush.apply(this, arguments); setTimeout(triggerNow, 250); return res; };
    history.replaceState = function(){ const res = origReplace.apply(this, arguments); setTimeout(triggerNow, 250); return res; };
  })(window.history);
  window.addEventListener('popstate', function(){ setTimeout(triggerNow, 200); });

  setInterval(function(){ triggerNow(); }, 1000);

  return "injectedV3";
})();
"""

# -------------------------------------------------

def load_db():
    if not os.path.exists(DB_PATH):
        return {}
    with open(DB_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def save_db(db):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def add_job_if_new(job):
    db = load_db()
    jid = job.get("job_id") or job.get("link") or f"{job.get('title')}|{job.get('company')}"
    if jid in db:
        print(f"[DB] Offre déjà présente (id={jid}) -> skip")
        return False
    db[jid] = job
    save_db(db)
    print(f"[DB] Offre enregistrée (id={jid})")
    return True

def robust_job_id(job):
    """
    Renvoie un id stable pour un job :
     - si job['job_id'] présent -> retourne
     - sinon tente d'extraire id depuis link
     - sinon hash(title|company|location|link)
    """
    jid = job.get("job_id")
    if jid:
        return str(jid)

    link = (job.get("link") or "")
    # Try query params common patterns
    m = re.search(r'(currentJobId|jobId|jobId=|jobs\/view\/)(\d+)', link)
    if m:
        # pick numeric part
        for g in m.groups()[::-1]:
            if g and g.isdigit():
                return g

    # Another pattern /jobs/view/123456/
    m2 = re.search(r'/jobs/(?:view/)?(\d+)', link)
    if m2:
        return m2.group(1)

    # fallback deterministic hash
    key = "|".join([
        (job.get("title") or "").strip()[:200],
        (job.get("company") or "").strip()[:120],
        (job.get("location") or "").strip()[:80],
        (link or "")[:300]
    ])
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return h[:16]

def page_fingerprint(driver):
    """Empreinte simple de la recherche (URL + quelques params utiles)."""
    try:
        url = driver.current_url
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        keys = {
            "path": parsed.path or "",
            "keywords": qs.get("keywords", [""])[0] or "",
            "geoId": qs.get("geoId", [""])[0] or "",
            "f_TPR": qs.get("f_TPR", [""])[0] or "",
            "f_WT": qs.get("f_WT", [""])[0] or ""
        }
        s = "|".join([keys["path"], keys["keywords"], keys["geoId"], keys["f_TPR"], keys["f_WT"]])
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return None

def ensure_watcher_injected(driver, max_attempts=3, sleep_between=0.8):
    """
    Vérifie si le watcher est présent côté page et tente la ré-injection si nécessaire.
    Retourne True si présent à la fin.
    """
    try:
        injected = driver.execute_script("return !!window.__jobWatcherInjectedV3;")
    except Exception:
        injected = False

    if injected:
        return True

    for attempt in range(1, max_attempts+1):
        try:
            print(f"[watchdog] tentative d'injection #{attempt}")
            inject_listener(driver)
            time.sleep(sleep_between)
            injected = False
            try:
                injected = driver.execute_script("return !!window.__jobWatcherInjectedV3;")
            except Exception:
                injected = False
            if injected:
                print("[watchdog] watcher injecté avec succès")
                return True
        except Exception as e:
            print("[watchdog] erreur d'injection:", e)
            time.sleep(sleep_between)
    print("[watchdog] échec d'injection après tentatives")
    return False

def analysis_worker(worker_id):
    print(f"[Worker-{worker_id}] Démarré")
    while True:
        job = processing_queue.get()

        min_len = (len((job.get("title") or "")) + len((job.get("company") or "")) + len((job.get("description_html") or "")))
        if min_len < 10:
            print(f"[Worker-{worker_id}] Offre incomplète / trop courte -> skip (id={job.get('job_id')})")
            processing_queue.task_done()
            continue

        if job is None:
            print(f"[Worker-{worker_id}] Stop signal reçu")
            break

        global current_fp_global
        if job.get("origin_fp") != current_fp_global:
            print(f"[Worker-{worker_id}] Job ignoré : provient d'une recherche différente (origin_fp={job.get('origin_fp')}, current_fp={current_fp_global})")
            processing_queue.task_done()
            continue

        print(f"[Worker-{worker_id}] Analyse de {job.get('link') or job.get('job_id') or job.get('title')[:40]}")
        prompt = f"""
{USER_CONTEXT}

Analyse maintenant l'offre ci-dessus et réponds STRICTEMENT au format demandé.
Fiche d'offre (JSON) :
{json.dumps(job, ensure_ascii=False)}
"""
        try:
            resp = client.responses.create(model=MODEL_NAME, input=prompt, temperature=0.05, top_p=0.8)
            output_text = ""
            try:
                output_text = getattr(resp, "output_text", "") or ""
            except Exception:
                output_text = ""

            if not output_text:
                try:
                    output_text = resp["output"][0]["content"][0]["text"]
                except Exception:
                    try:
                        output_text = resp["choices"][0]["message"]["content"]
                    except Exception:
                        output_text = str(resp)

            output_text = output_text.strip()
            first_line = output_text.splitlines()[0].strip() if output_text else ""
            rest = "\n".join(output_text.splitlines()[1:]).strip()

            parsed_analysis = None
            if rest:
                try:
                    parsed_analysis = json.loads(rest)
                except Exception:
                    m = re.search(r'(\{.*\})', rest, re.DOTALL)
                    if m:
                        try:
                            parsed_analysis = json.loads(m.group(1))
                        except Exception:
                            parsed_analysis = None

            should_save = False
            if first_line.upper().startswith("OUI"):
                should_save = True
            else:
                should_save = False

            if parsed_analysis and "should_save" in parsed_analysis:
                should_save = bool(parsed_analysis["should_save"])

            with stats_lock:
                stats["total_analyzed"] = stats.get("total_analyzed", 0) + 1
                if should_save:
                    stats["retained"] = stats.get("retained", 0) + 1
                # persist immédiatement (simple)
                save_stats()

            saved_obj = {
                "job_id": job.get("job_id"),
                "link": job.get("link"),
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location"),
                "description_html_snippet": (job.get("description_html") or "")[:4000],
                "analysis": {
                    "raw_output": output_text,
                    "first_line": first_line,
                    "parsed": parsed_analysis
                },
                "should_save": should_save,
                "analyzed_at": datetime.utcnow().isoformat() + "Z",
                "applied": False,              # tu avais déjà
                "source": "linkedin",
                "application_result": "no_response"   # <-- nouveau champ
            }

            if should_save:
                with db_lock:
                    add_job_if_new(saved_obj)
            else:
                print(f"[Worker-{worker_id}] Non recommandé par le modèle.")

        except Exception as e:
            print(f"[Worker-{worker_id}] Erreur durant l'analyse: {e}")

        processing_queue.task_done()

def inject_listener(driver):
    try:
        res = driver.execute_script(JS_WATCHER)
        print(f"[inject_listener] execute_script returned: {res!r}")
    except Exception as e:
        print("[inject_listener] exception:", e)
        res = None

    # si execute_script ne retourne rien, vérifie le flag côté page
    try:
        injected_flag = driver.execute_script("return !!window.__jobWatcherInjectedV3;")
        print(f"[inject_listener] window.__jobWatcherInjectedV3 -> {injected_flag}")
    except Exception as e:
        print("[inject_listener] warning: impossible de verifier flag:", e)

    return res

def poll_job_queue(driver):
    js = """
    var q = window.__jobQueue || [];
    if (q.length === 0) return [];
    var items = q.slice();
    window.__jobQueue = [];
    return items;
    """
    try:
        items = driver.execute_script(js)
        return items or []
    except Exception as e:
        print("[poll_job_queue] exception:", e)
        return []

def create_firefox_driver():
    opts = Options()
    opts.headless = False
    # essayer de réduire la detection webdriver
    try:
        opts.set_preference("dom.webdriver.enabled", False)
        opts.set_preference("useAutomationExtension", False)
        opts.set_preference("general.useragent.override",
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")
    except Exception as e:
        print("[prefs] warning: cannot set some prefs:", e)

    if FIREFOX_PROFILE_PATH:
        try:
            profile = FirefoxProfile(FIREFOX_PROFILE_PATH)
            opts.profile = profile
            driver = webdriver.Firefox(options=opts)
            print("[driver] démarré avec profil:", FIREFOX_PROFILE_PATH)
            return driver
        except Exception as e:
            print("[driver] warning: impossible d'utiliser le profil fourni:", e)
            print("[driver] démarrage sans profil...")
    # fallback: driver normal
    driver = webdriver.Firefox(options=opts)
    return driver

def main():
    driver = create_firefox_driver()
    print("Ouvre LinkedIn dans la fenêtre Firefox qui vient de s'ouvrir.")
    driver.get(LINKEDIN_SEARCH_URL)
    time.sleep(2)

    # use watchdog-injection (tries to inject or verify)
    ensure_watcher_injected(driver)

    # start workers
    workers = []
    for i in range(ANALYSIS_WORKERS):
        t = threading.Thread(target=analysis_worker, args=(i+1,), daemon=True)
        t.start()
        workers.append(t)

    print("Surveillance démarrée. Clique sur une offre (ou SHIFT+S pour forcer).")

    last_fp = page_fingerprint(driver)

    global current_fp_global
    current_fp_global = last_fp

    print(f"[main] fingerprint initiale: {last_fp}")

    try:
        while True:
            # detect search/navigation change
            try:
                cur_fp = page_fingerprint(driver)
            except Exception:
                cur_fp = None

            if cur_fp != last_fp:
                print(f"[main] Détecté changement de page/search (fp: {last_fp} -> {cur_fp})")
                last_fp = cur_fp
                current_fp_global = cur_fp
                
                ensure_watcher_injected(driver)

            items = poll_job_queue(driver)
            if items:
                print(f"[Main] {len(items)} nouvel(s) objet(s) dans la queue.")
            for job in items:
                job.setdefault("title", "")
                job.setdefault("company", "")
                job.setdefault("location", "")
                job.setdefault("description_html", "")
                job.setdefault("link", job.get("link") or "")
                job.setdefault("job_id", job.get("job_id") or None)

                # attach origin fingerprint to be able to detect source search
                job['origin_fp'] = last_fp

                # generate robust job id
                jid = robust_job_id(job)
                job["job_id"] = jid

                print(f"[Main] Mis en queue: {job.get('title')[:120]} | company: {job.get('company') or '<empty>'} | id={jid} | origin_fp={job['origin_fp']}")
                processing_queue.put(job)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("Arrêt demandé (Ctrl+C). Fermeture...")
    finally:
        for _ in workers:
            processing_queue.put(None)
        for t in workers:
            t.join(timeout=2)
        driver.quit()
        print("Terminé.")

if __name__ == "__main__":
    main()
