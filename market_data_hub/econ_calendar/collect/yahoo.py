# -*- coding: utf-8 -*-
"""Yahoo su una finestra ampia: passato completo + futuro fino a coprire i semestrali.

Del passato Yahoo conserva pochissimo, ma quanto poco va misurato giornata per
giornata invece che per sondaggi sparsi. Il futuro invece e' il suo punto forte
ed e' li' che compaiono trimestrali e semestrali.
"""
import time
from datetime import datetime, timedelta

import pandas as pd
from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .browser import setup_browser, COLONNE


def scarica(driver, da, a):
    giorno = datetime.strptime(da, '%Y-%m-%d')
    fine = datetime.strptime(a, '%Y-%m-%d')
    righe, vuoti_consecutivi = [], 0

    while giorno <= fine:
        iso = giorno.strftime('%Y-%m-%d')
        offset, n_giorno = 0, 0
        while True:
            driver.get('https://finance.yahoo.com/calendar/economic'
                       f'?day={iso}&offset={offset}&size=100')
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'table tbody tr')))
            except TimeoutException:
                break
            trovate = BeautifulSoup(driver.page_source, 'html.parser').select('table tbody tr')
            for r in trovate:
                c = [x.get_text(' ', strip=True) for x in r.select('td')]
                if len(c) < 8:
                    continue
                righe.append({'Data_Rilascio': iso, 'Orario': c[2], 'Paese': c[1],
                              'Importanza': 'N/D', 'Evento': c[0],
                              'Periodo_Riferimento': c[3], 'Attuale': c[4],
                              'Previsto': c[5], 'Precedente': c[6], 'Revisione': c[7],
                              'Fonte': 'Yahoo Finance'})
                n_giorno += 1
            if len(trovate) < 100:
                break
            offset += 100

        if n_giorno:
            print(f'  {iso}: {n_giorno}', flush=True)
            vuoti_consecutivi = 0
        else:
            vuoti_consecutivi += 1
            if vuoti_consecutivi % 20 == 0:
                print(f'  ({iso}: {vuoti_consecutivi} giornate vuote di fila)', flush=True)
        giorno += timedelta(days=1)
    return pd.DataFrame(righe, columns=COLONNE)


def scarica_intervallo(da, a):
    """`scarica()` con il browser che si apre e si chiude da se'."""
    d = setup_browser()
    try:
        d.get('https://finance.yahoo.com/calendar/economic')
        time.sleep(3)
        try:
            d.find_element(By.NAME, 'reject').click()
            time.sleep(4)
        except Exception:
            pass
        df = scarica(d, da, a)
    finally:
        d.quit()
    return df.replace(['', '-'], pd.NA).fillna('N/D') if len(df) else df

