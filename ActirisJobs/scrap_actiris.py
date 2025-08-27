from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import csv
import time

# *********************
# Full Scrap of actiris using Selenium
# *********************

# Configuration Firefox 
options = Options()
options.headless = True
options.set_preference(
    "general.useragent.override",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
)

driver = webdriver.Firefox(service=Service(GeckoDriverManager().install()), options=options)

# Lien de base (tu peux personnaliser tes filtres ici)
base_url = ""
with open("actiris_base_url.txt", "r", encoding="utf-8") as f:
    base_url = f.read().strip()

# Nombre de pages à parcourir
pages_to_scrape = 10

all_links = set()

for page in range(1, pages_to_scrape + 1):
    url = base_url.format(page)
    print(f"🔄 Chargement page {page} : {url}")
    driver.get(url)

    try:
        links = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='detail-offre-d-emploi']"))
        )
        page_links = {a.get_attribute("href") for a in links}
        print(f"  → {len(page_links)} liens trouvés.")
        all_links.update(page_links)
    except Exception as e:
        print(f"  ⚠️ Erreur page {page} : {e}")
    
    time.sleep(1)

driver.quit()

# Sauvegarde
with open("actiris_detail_links.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["detail_url"])
    for url in sorted(all_links):
        writer.writerow([url])

print(f"\n✅ Total : {len(all_links)} liens uniques extraits sur {pages_to_scrape} pages.")
