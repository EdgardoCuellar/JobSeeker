# JobSeeker: Automated Job Offer Scraper & Analyzer

## Overview

**JobSeeker** is a Python project designed to automate the process of job searching and analysis. It scrapes job offers from both [Actiris](https://www.actiris.brussels/) and [LinkedIn](https://www.linkedin.com/), analyzes them using a local LLM via [LM Studio](https://lmstudio.ai/), and helps us the jobless devs to quickly identify relevant job offers. The project streamlines the job search workflow, making it easier to filter, justify, and track job applications.

---

## Project Structure

- **ActirisJobs/**  
  Scrapes and analyzes job offers from Actiris.
  - `scrap_actiris.py` — Scrapes job offer links from Actiris.
  - `analyze.py` — Parses offer details and analyzes them using LM Studio.
  - `actiris_detail_links.csv` — List of scraped offer URLs.
  - `filtered_offers.csv` — Offers retained after analysis.
  - Config files.

- **LinkedinJobs/**  
  Scrapes and analyzes job offers from LinkedIn.
  - `linkedin_click_monitor.py` — Monitors and extracts job details from LinkedIn.
  - `linkedin_job_watcher_dashboard.py` — Dashboard for tracking and visualizing job search stats.
  - `jobs_db.json` — Database of analyzed LinkedIn offers.
  - `user_context.txt` — Stores user preferences/context for analysis.
  - Config files.

---

## How It Works

1. **Scraping**  
   - Actiris: `scrap_actiris.py` collects job offer URLs and saves them to `actiris_detail_links.csv`.
   - LinkedIn: `linkedin_click_monitor.py` extracts job details while browsing LinkedIn.

2. **Analysis**  
   - Both platforms use a local LLM (via LM Studio) to analyze job offers.
   - The analysis considers user context (location, experience, contract type, etc.) and outputs a decision (`OUI`/`NON`) with justification.

3. **Filtering & Tracking**  
   - Only relevant offers are retained and saved for further review.
   - The dashboard and CSV/JSON files help track applications and results.

---

## Installation

1. **Clone the repository**
   ```sh
   git clone https://github.com/yourusername/JobSeeker.git
   cd JobSeeker
   ```

2. **Install Python dependencies**
   ```sh
   pip install -r requirements.txt
   ```
   If `requirements.txt` is missing, install manually:
   ```sh
   pip install openai requests beautifulsoup4 selenium sqlite3 urllib3 Flask
   ```

3. **Install LM Studio**
   - Download and install [LM Studio](https://lmstudio.ai/) for your OS.
   - Download and load a compatible LLM model (e.g., `google/gemma-3n-e4b`).

---

## Usage

### Actiris Workflow

1. **Scrape Offers**
   ```sh
   python ActirisJobs/scrap_actiris.py
   ```
   This populates `actiris_detail_links.csv`.

2. **Analyze Offers**
   ```sh
   python ActirisJobs/analyze.py
   ```
   This analyzes each offer using LM Studio and saves relevant ones to `filtered_offers.csv`.

### LinkedIn Workflow

1. **Monitor Offers**
   ```sh
   python LinkedinJobs/linkedin_click_monitor.py
   ```
   Run while browsing LinkedIn jobs. Extracted jobs are saved to `jobs_db.json`.

2. **Analyze & Track**
   ```sh
   python LinkedinJobs/linkedin_job_watcher_dashboard.py
   ```
   Use the dashboard to visualize and track your job search.

---

## Last words

Now, HR creates job postings using AI, the AI reads them, and an HR AI analyzes them. Welcome to the new era of