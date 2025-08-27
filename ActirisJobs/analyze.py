# analyze_offers.py

import csv
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import time

# LM Studio / OpenAI local client
# Best gpt-oss-20b or on small config google/gemma-3n-e4b
client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
MODEL_NAME = "google/gemma-3n-e4b"  # ou le nom exact chargé dans LM Studio

# --- Fonctions utilitaires ---

def parse_offer_page(url):
    """Récupère les informations d'une offre Actiris depuis sa page HTML."""
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    # Titre (si présent dans une balise <h1>, adapter si différent)
    title_tag = soup.select_one("h1")
    title = title_tag.get_text(strip=True) if title_tag else "Titre non trouvé"

    # Infos générales (type de contrat, temps de travail, famille métier)
    info_items = soup.select("ul.picto li")
    contract_type = work_time = job_family = ""

    for li in info_items:
        text = li.get_text(strip=True)
        if "Temps de travail" in text:
            work_time = text.replace("Temps de travail :", "").strip()
        elif "Type de contrat" in text:
            contract_type = text.replace("Type de contrat :", "").strip()
        elif "Famille de métiers" in text:
            job_family = text.replace("Famille de métiers :", "").strip()

    # Description complète : bloc principal
    description_block = soup.select_one("div.bloc-emploi__text")
    description = description_block.get_text("\n", strip=True) if description_block else ""

    # Profil recherché : sous-titre + liste
    profile = ""
    profile_heading = soup.find("h3", string="Profil")
    if profile_heading:
        ul = profile_heading.find_next_sibling("ul")
        if ul:
            profile = "\n".join(li.get_text(strip=True) for li in ul.find_all("li"))

    # Compétences linguistiques
    languages = []
    lang_section = soup.find("h3", string="Compétences linguistiques")
    if lang_section:
        for lang_block in lang_section.find_all_next("li"):
            lang_name = lang_block.find("h4")
            if lang_name:
                levels = [li.get_text(strip=True) for li in lang_block.find_all("li")]
                languages.append({"langue": lang_name.text.strip(), "niveaux": levels})
            if lang_block.find_next("h3"):  # stop at next section
                break

    # Lien vers Panorama des métiers (si disponible)
    panorama_link = ""
    panorama_anchor = soup.find("a", href=True, string="Panorama des métiers")
    if panorama_anchor:
        panorama_link = panorama_anchor["href"]

    return {
        "url": url,
        "title": title,
        "contract_type": contract_type,
        "work_time": work_time,
        "job_family": job_family,
        "description": description,
        "profile": profile,
        "languages": languages,
        "panorama_link": panorama_link
    }

def ask_gpt_oss(offer):
    
    # ****************************************************
    # Contexte utilisateur envoyé au LLM
    # Le contexte doit absoulment renvoyer NON ou OUI à la fin, dans la première ligne du prompt
    # ****************************************************

    prompt = f"""
Offre à analyser :

Titre : {offer['title']}
Type de contrat : {offer['contract_type']}
Temps de travail : {offer['work_time']}
Famille de métiers : {offer['job_family']}
Description : {offer['description']}
Profil recherché : {offer['profile']}
Compétences linguistiques : {offer['languages']}
Panorama des métiers : {offer['panorama_link']}
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=512,
            stop=["FIN"],
        )
        text = response.choices[0].message.content

        # decision = first 3 char of the response in the first line
        decision = text.splitlines()[0][:3].strip()
        # justification = rest of the response but without the first line
        justification = "\n".join(text.splitlines()[1:]).strip()

        return decision, justification

    except Exception as e:
        print("  Erreur GPT-OSS :", e)
        return False, "Erreur lors de l’analyse"

# --- Traitement principal ---

def main():
    with open("actiris_detail_links.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        urls = [row["detail_url"] for row in reader]

    filtered = []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] Analyse de {url}")
        try:
            offer = parse_offer_page(url)
        except Exception as e:
            print("  Erreur parsing :", e)
            time.sleep(10)  # pour ne pas inonder le site
            continue

        try:
            decision, justification = ask_gpt_oss(offer)
        except Exception as e:
            print("  Erreur GPT-OSS :", e)
            continue

        if decision == "OUI":
            print("  → RETENU :", justification)
            filtered.append({"url": url, "justification": justification})
        else:
            print("  → IGNORÉ :", justification)

        time.sleep(1)  # pour ne pas inonder l'API

    # Sauvegarde des offres retenues sans supprimer les précédentes
    with open("filtered_offers.csv", "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["url", "justification"])
        writer.writerows(filtered)

    print(f"\n✅ {len(filtered)} offres retenues, enregistrées dans 'filtered_offers.csv'.")

if __name__ == "__main__":
    main()
