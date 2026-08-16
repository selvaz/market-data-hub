# -*- coding: utf-8 -*-
"""Calendario economico multifonte: Yahoo Finance + Tradays + Forex Factory.

Solo Yahoo espone il periodo di riferimento del dato (colonna "For"):
e' quindi la fonte di riferimento per Periodo_Riferimento, le altre due
servono da controllo incrociato su orari, impatto e consensus.
"""
import re
import time
from datetime import datetime, timedelta

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

COLONNE = ['Data_Rilascio', 'Orario', 'Paese', 'Importanza', 'Evento',
           'Periodo_Riferimento', 'Attuale', 'Previsto', 'Precedente',
           'Revisione', 'Fonte']


def setup_browser(headless=True, timezone_id='UTC'):
    """Selenium browser for scraping, pinned to a declared timezone.

    The timezone is not a detail. Tradays renders its times in JavaScript using
    the *viewer's* clock, so what the scraper reads is this machine's timezone,
    not the site's. Measured: forcing three different zones moved 197 of 203
    events, and under 'UTC' the times are the true UTC ones (euro-area Sentix
    08:30, RBA 04:30, US bill auctions 15:30).

    That is why the offset used to be a constant of 7 that had to be re-measured
    after every clock change: 7 is this server's Pacific offset in summer, and
    it would have become 8 on 1 November with nothing failing. Declaring the
    zone here removes the constant and the chore together, and makes the scraper
    give the same answer from a machine in any timezone.

    MyFXBook is *not* affected -- it renders server-side and ignores what the
    browser declares (0 of 235 events moved) -- so its offset stays a
    measurement. See `timezones.py`, which checks both from the data.
    """
    opzioni = Options()
    if headless:
        opzioni.add_argument('--headless=new')
    opzioni.add_argument('--disable-gpu')
    opzioni.add_argument('--window-size=1920,1080')
    opzioni.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/126.0.0.0 Safari/537.36')
    servizio = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=servizio, options=opzioni)
    if timezone_id:
        driver.execute_cdp_cmd('Emulation.setTimezoneOverride',
                               {'timezoneId': timezone_id})
    return driver


def _testo(nodo):
    return nodo.get_text(' ', strip=True) if nodo else ''


def _vuoto():
    return pd.DataFrame(columns=COLONNE)


# ----------------------------------------------------------------- Yahoo ----
def _gestisci_consenso_yahoo(driver):
    """Dall'UE Yahoo redirige su consent.yahoo.com: rifiutiamo i cookie non
    essenziali (pulsante 'reject'), sufficiente per accedere al calendario."""
    driver.get("https://finance.yahoo.com/calendar/economic")
    time.sleep(3)
    if 'consent' not in driver.current_url:
        return True
    try:
        driver.find_element(By.NAME, 'reject').click()
        time.sleep(4)
        return 'consent' not in driver.current_url
    except NoSuchElementException:
        print("  ! muro di consenso Yahoo non superato")
        return False


def scarica_yahoo(driver, start_date, end_date):
    """Yahoo Finance, giorno per giorno con paginazione. Date 'YYYY-MM-DD'."""
    print(f"Scaricando Yahoo Finance da {start_date} a {end_date}...")
    if not _gestisci_consenso_yahoo(driver):
        return _vuoto()

    giorno = datetime.strptime(start_date, "%Y-%m-%d")
    fine = datetime.strptime(end_date, "%Y-%m-%d")
    dati = []

    while giorno <= fine:
        data_iso, offset, dimensione = giorno.strftime("%Y-%m-%d"), 0, 100
        while True:
            driver.get("https://finance.yahoo.com/calendar/economic"
                       f"?day={data_iso}&offset={offset}&size={dimensione}")
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'table tbody tr')))
            except TimeoutException:
                break  # giorno senza eventi (weekend, festivi)

            righe = BeautifulSoup(driver.page_source, 'html.parser').select('table tbody tr')
            for riga in righe:
                celle = [c.get_text(' ', strip=True) for c in riga.select('td')]
                if len(celle) < 8:
                    continue
                dati.append({
                    'Data_Rilascio': data_iso,
                    'Orario': celle[2],
                    'Paese': celle[1],
                    'Importanza': 'N/D',          # Yahoo non pubblica l'impatto
                    'Evento': celle[0],
                    'Periodo_Riferimento': celle[3],
                    'Attuale': celle[4],
                    'Previsto': celle[5],
                    'Precedente': celle[6],
                    'Revisione': celle[7],
                    'Fonte': 'Yahoo Finance',
                })
            if len(righe) < dimensione:
                break
            offset += dimensione
        giorno += timedelta(days=1)

    return pd.DataFrame(dati, columns=COLONNE)


# --------------------------------------------------------------- Tradays ----
def scarica_tradays(driver):
    """Tradays (redirige su mql5.com): settimana corrente."""
    print("Scaricando Tradays...")
    driver.get("https://www.tradays.com/en/economic-calendar")
    try:
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.ec-table__item')))
    except TimeoutException:
        print("  ! timeout rendering Tradays")
        return _vuoto()

    corpo = BeautifulSoup(driver.page_source, 'html.parser').select_one('.ec-table__body')
    if corpo is None:
        return _vuoto()

    dati, giorno = [], 'N/D'
    for nodo in corpo.find_all('div', recursive=False):
        classi = nodo.get('class') or []
        if 'ec-table__title' in classi:                   # intestazione di giornata
            giorno = _testo(nodo)
            continue
        if 'ec-table__item' not in classi:
            continue

        imp = nodo.select_one('.ec-table__importance')
        classi_imp = ' '.join(imp.get('class') or []) if imp else ''
        importanza = next((liv for liv in ('high', 'medium', 'low')
                           if f'importance_{liv}' in classi_imp), 'none')
        dati.append({
            'Data_Rilascio': giorno,
            'Orario': _testo(nodo.select_one('.ec-table__col_time > div')),
            'Paese': _testo(nodo.select_one('.ec-table__curency-name')),
            'Importanza': importanza,
            'Evento': _testo(nodo.select_one('.ec-table__col_event')),
            'Periodo_Riferimento': 'N/D',                 # non esposto in lista
            'Attuale': _testo(nodo.select_one('.ec-table__col_actual')),
            'Previsto': _testo(nodo.select_one('.ec-table__col_forecast')),
            'Precedente': _testo(nodo.select_one('.ec-table__col_previous')),
            'Revisione': 'N/D',
            'Fonte': 'Tradays',
        })
    return pd.DataFrame(dati, columns=COLONNE)


# --------------------------------------------------------- Forex Factory ----
def scarica_forex_factory(driver):
    """Forex Factory: settimana corrente, con riporto di data e orario."""
    print("Scaricando Forex Factory...")
    driver.get("https://www.forexfactory.com/calendar")
    try:
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'tr.calendar__row')))
    except TimeoutException:
        print("  ! Forex Factory non ha risposto (possibile blocco anti-bot)")
        return _vuoto()

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    dati, data_corrente, orario_corrente = [], 'N/D', 'N/D'

    for riga in soup.select('tr.calendar__row'):
        # FF stampa data e orario solo sulla prima riga del gruppo -> riporto
        nuova_data = _testo(riga.select_one('.calendar__date'))
        if nuova_data:
            data_corrente = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', nuova_data)
        nuovo_orario = _testo(riga.select_one('.calendar__time'))
        if nuovo_orario:
            orario_corrente = nuovo_orario

        evento = _testo(riga.select_one('.calendar__event'))
        if not evento:
            continue

        impatto = riga.select_one('.calendar__impact span')
        classi_imp = ' '.join(impatto.get('class') or []) if impatto else ''
        importanza = (impatto.get('title') if impatto and impatto.get('title') else
                      next((c.split('--')[-1] for c in classi_imp.split() if 'impact' in c), 'N/D'))

        dati.append({
            'Data_Rilascio': data_corrente,
            'Orario': orario_corrente,
            'Paese': _testo(riga.select_one('.calendar__currency')),
            'Importanza': importanza,
            'Evento': evento,
            'Periodo_Riferimento': 'N/D',                 # FF non ha colonna "For"
            'Attuale': _testo(riga.select_one('.calendar__actual')),
            'Previsto': _testo(riga.select_one('.calendar__forecast')),
            'Precedente': _testo(riga.select_one('.calendar__previous')),
            'Revisione': 'N/D',
            'Fonte': 'Forex Factory',
        })
    return pd.DataFrame(dati, columns=COLONNE)


# ==========================================
# ESECUZIONE PRINCIPALE
# ==========================================
if __name__ == "__main__":
    data_inizio = "2026-08-10"
    data_fine = "2026-08-16"

    driver = setup_browser()
    try:
        df_yahoo = scarica_yahoo(driver, data_inizio, data_fine)
        df_tradays = scarica_tradays(driver)
        df_ff = scarica_forex_factory(driver)
    finally:
        driver.quit()

    validi = [df for df in (df_yahoo, df_tradays, df_ff) if not df.empty]
    if not validi:
        print("Nessun dato estratto dalle fonti.")
        raise SystemExit(1)

    df_finale = pd.concat(validi, ignore_index=True)
    df_finale = df_finale.replace(['', '-'], pd.NA).fillna("N/D")

    print("\n=== ESTRAZIONE MULTIFONTE COMPLETATA ===")
    print(df_finale['Fonte'].value_counts().to_string())
    print("\nEventi con periodo di riferimento:",
          int((df_finale['Periodo_Riferimento'] != 'N/D').sum()))
    print(df_finale[df_finale['Fonte'] == 'Yahoo Finance'].head(8).to_string())

    nome_file = f"calendario_multifonte_{data_inizio}_{data_fine}.csv"
    df_finale.to_csv(nome_file, index=False, encoding='utf-8-sig')
    print(f"\n-> Dati salvati con successo in: {nome_file}")
