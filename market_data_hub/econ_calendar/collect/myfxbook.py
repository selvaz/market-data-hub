# -*- coding: utf-8 -*-
"""MyFXBook giorno per giorno: l'unica fonte con storico E periodo di riferimento.

Vincoli misurati sulla fonte (utente anonimo):
  - storico limitato a 2 anni esatti (il picker espone minDate = oggi - 2 anni);
  - ogni query storica restituisce al massimo 40 righe, qualunque sia
    l'ampiezza dell'intervallo: quindi si procede un giorno alla volta;
  - i parametri in URL sono ignorati, l'intervallo si imposta solo pilotando
    il bootstrap-daterangepicker via jQuery;
  - serve Chrome NON headless: in headless scattano interstiziali pubblicitari
    che coprono il pulsante del selettore;
  - l'applicazione fallisce spesso in silenzio (la tabella resta sulla vista
    corrente), quindi ogni giornata va verificata e ritentata.

Il periodo di riferimento sta dentro il nome evento - "Home Loans QoQ (Q2)" -
e viene estratto in colonna propria.
"""
import csv
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# selenium, bs4 and webdriver-manager arrive with the `calendar` extra, which
# the 3.9 leg of CI deliberately does not install: nothing but the collectors
# needs a browser. They are therefore imported inside the two functions that
# actually drive one, so this module's pure logic -- `paese_iso`,
# `da_ricollezionare`, the parsing helpers -- can be imported and tested
# without a browser stack. This is deferral, not a fallback: `apri()` and
# `leggi()` still fail loudly and immediately if the extra is missing.

PERIODO = re.compile(r'\(([^)]{1,12})\)\s*$')
PERCORSO_PAESE = re.compile(r'/forex-economic-calendar/([^/?#]+)')
MESI = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}
COLONNE = ['Data_Rilascio', 'Orario', 'Paese', 'Importanza', 'Evento',
           'Periodo_Riferimento', 'Attuale', 'Previsto', 'Precedente',
           'Revisione', 'Fonte']

IMPOSTA = """
  const [da, a] = [arguments[0], arguments[1]];
  const $ = window.jQuery;
  if (!$) return 'jQuery assente';
  let host = null;
  $('*').each(function () { if ($(this).data('daterangepicker')) { host = this; return false; } });
  if (!host) return 'picker non inizializzato';
  const p = $(host).data('daterangepicker');
  const M = window.moment;
  if (!M) return 'moment assente';
  p.setStartDate(M(da, 'YYYY-MM-DD'));
  p.setEndDate(M(a, 'YYYY-MM-DD'));
  $(host).trigger('apply.daterangepicker', p);
  const b = document.querySelector('.daterangepicker .applyBtn');
  if (b) b.click();
  return 'ok';
"""


def apri():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    o = Options()
    o.add_argument('--window-size=1600,1000')
    o.add_argument('--disable-blink-features=AutomationControlled')
    o.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
    d = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=o)
    d.set_page_load_timeout(60)
    return d


def carica(d):
    d.get('https://www.myfxbook.com/forex-economic-calendar')
    time.sleep(11)
    d.execute_script("""
      const b = [...document.querySelectorAll('button,a')]
        .find(x => /reject all/i.test((x.textContent||'').trim()));
      if (b) b.click();
    """)
    time.sleep(3)


def leggi(d, anno):
    from bs4 import BeautifulSoup

    s = BeautifulSoup(d.page_source, 'html.parser')
    righe = []
    for tr in s.find_all('tr'):
        c = tr.find_all('td')
        if len(c) < 9:
            continue
        testo = c[0].get_text(' ', strip=True)
        m = re.match(r'([A-Z][a-z]{2}) (\d{1,2}),?\s*(\d{2}:\d{2})?', testo)
        if not m:
            continue
        mese, giorno, ora = m.group(1), int(m.group(2)), m.group(3) or ''
        if mese not in MESI:
            continue
        evento = c[4].get_text(' ', strip=True)
        collegamento = c[4].find('a', href=True)
        mp = PERIODO.search(evento)
        righe.append({
            'Data_Rilascio': f'{anno}-{MESI[mese]:02d}-{giorno:02d}',
            'Orario': ora,
            # The visible column is a currency (EUR), not necessarily the
            # issuing country.  The event URL carries the latter.
            'Paese': paese_da_href(
                collegamento.get('href') if collegamento else '',
                c[3].get_text(' ', strip=True),
            ),
            'Importanza': c[5].get_text(' ', strip=True).lower(),
            'Evento': PERIODO.sub('', evento).strip(),
            'Periodo_Riferimento': mp.group(1) if mp else 'N/D',
            'Attuale': c[8].get_text(' ', strip=True),
            'Previsto': c[7].get_text(' ', strip=True),
            'Precedente': c[6].get_text(' ', strip=True),
            'Revisione': '',
            'Fonte': 'MyFXBook',
        })
    return righe


def giornata(d, iso, tentativi=3):
    """Una giornata alla volta: il tetto di 40 righe rende inutili gli intervalli."""
    anno = int(iso[:4])
    atteso = iso[5:]                      # 'MM-DD' che devono comparire
    for n in range(tentativi):
        carica(d)
        try:
            d.execute_script("document.getElementById('calendarCustomBtn').click();")
        except Exception:
            continue
        time.sleep(4)
        esito = d.execute_script(IMPOSTA, iso, iso)
        if esito != 'ok':
            print(f'    tentativo {n + 1}: {esito}', flush=True)
            continue
        time.sleep(13)
        righe = leggi(d, anno)
        # verifica che l'intervallo sia stato applicato davvero
        date = {r['Data_Rilascio'][5:] for r in righe}
        if atteso in date or any(abs((datetime.strptime(x, '%m-%d').replace(year=anno)
                                      - datetime.strptime(atteso, '%m-%d').replace(year=anno)).days) <= 1
                                 for x in date):
            return [r for r in righe if abs(
                (datetime.strptime(r['Data_Rilascio'], '%Y-%m-%d')
                 - datetime.strptime(iso, '%Y-%m-%d')).days) <= 1]
        print(f'    tentativo {n + 1}: intervallo non applicato (viste {sorted(date)[:3]})',
              flush=True)
    return []


# MyFXBook etichetta per valuta; le altre fonti per paese ISO. La conversione
# stava in uno script a parte, il che voleva dire che il file letto dal
# consolidamento non era quello che lo scaricatore scriveva.
VALUTA_PAESE = {'USD': 'US', 'EUR': 'EU', 'GBP': 'GB', 'JPY': 'JP', 'CNY': 'CN',
                'AUD': 'AU', 'CAD': 'CA', 'NZD': 'NZ', 'CHF': 'CH', 'INR': 'IN',
                'MXN': 'MX', 'BRL': 'BR', 'KRW': 'KR', 'TWD': 'TW', 'ZAR': 'ZA'}

# Confirmed from MyFXBook event URLs for the EUR releases whose identical
# event names previously collapsed into the Euro Area.  Values are the ISO2
# codes used in the raw collector CSV; catalog.to_iso3() maps EU to EMU.
SLUG_PAESE = {
    'austria': 'AT', 'belgium': 'BE', 'estonia': 'EE', 'france': 'FR',
    'germany': 'DE', 'greece': 'GR', 'ireland': 'IE', 'lithuania': 'LT',
    'netherlands': 'NL', 'portugal': 'PT', 'slovenia': 'SI', 'spain': 'ES',
    'euro-area': 'EU',
}


def _iso_date(s):
    """'2026-08-16' -> date, anything else -> None."""
    try:
        return datetime.strptime(str(s).strip(), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def paese_iso(valore):
    """Currency -> ISO country, and an ISO country -> itself.

    Both have to work, because the file is appended to across runs: rows
    written today still say 'USD', rows written yesterday already say 'US'.
    Mapping the whole column through the currency dictionary turned the second
    kind into empty strings, so a resumed run erased the country from
    everything collected before it.
    """
    v = str(valore).strip()
    return VALUTA_PAESE.get(v, v)


def paese_da_href(href, valuta):
    """Return MyFXBook's issuing-country code from an event link.

    A country slug is authoritative over the currency shown in the table.
    In particular, an unknown EUR slug must not fall back to ``EU``: that
    would silently turn a national release into an EMU aggregate.  Keeping
    the unknown slug makes it remain unmatched until it is explicitly mapped.
    """
    m = PERCORSO_PAESE.search(str(href or ''))
    if not m:
        return str(valuta).strip()
    slug = m.group(1).lower()
    if slug in SLUG_PAESE:
        return SLUG_PAESE[slug]
    if str(valuta).strip() == 'EUR':
        return slug
    return str(valuta).strip()


def da_ricollezionare(fatte, rifai_giorni=7, oggi=None):
    """Split the registry into (skip, collect again).

    A finished day stays skipped; the recent ones do not. Today is routinely
    collected before its own releases come out, and recent values get revised,
    so a registry that skipped every date it had ever seen meant overlapping
    scheduled windows never refreshed any of it.
    """
    oggi = oggi or datetime.utcnow().date()
    limite = oggi - timedelta(days=max(0, rifai_giorni))
    rifare = {g for g in fatte
              if _iso_date(g) is not None and _iso_date(g) >= limite}
    return fatte - rifare, rifare


def scarica(da, a, out, registro=None, verboso=True, rifai_giorni=7):
    """Una giornata alla volta, riprendibile, con il paese in ISO.

    Scrive man mano e tiene un registro delle giornate fatte: ogni giornata
    costa una tredicina di secondi piu' i tentativi, quindi una corsa
    interrotta a meta' che ricominciasse da capo sarebbe inutilizzabile.

    `rifai_giorni` are the most recent days that get collected again even when
    the registry has them. The registry alone skipped a date forever, which is
    right for a date that is finished and wrong for the two cases that actually
    happen: today, collected in the morning before its own releases came out,
    and the recent past, whose actual values get revised. On overlapping
    scheduled windows that meant those observations were never refreshed --
    the vintage and revision tracking this calendar exists for could not see
    anything change.
    """
    out = Path(out)
    registro = Path(registro) if registro else out.with_suffix('.fatte.txt')

    fatte = set()
    if registro.exists():
        fatte = {r.strip() for r in registro.read_text(encoding='utf-8').splitlines()
                 if r.strip()}
    fatte, da_rifare = da_ricollezionare(fatte, rifai_giorni)
    if verboso:
        print(f'  giornate gia fatte: {len(fatte)}'
              + (f' (+{len(da_rifare)} recenti da rifare)' if da_rifare else ''),
              flush=True)

    nuovo = not out.exists()
    with open(out, 'a', newline='', encoding='utf-8-sig') as uscita, \
            open(registro, 'a', encoding='utf-8') as reg:
        scrittore = csv.DictWriter(uscita, fieldnames=COLONNE)
        if nuovo:
            scrittore.writeheader()
        d = apri()
        try:
            giorno = datetime.strptime(da, '%Y-%m-%d')
            fine = datetime.strptime(a, '%Y-%m-%d')
            while giorno <= fine:
                iso = giorno.strftime('%Y-%m-%d')
                if iso in fatte:
                    giorno += timedelta(days=1)
                    continue
                righe = giornata(d, iso)
                for r in righe:
                    scrittore.writerow(r)
                uscita.flush()
                reg.write(iso + '\n')
                reg.flush()
                if verboso:
                    print(f'  {iso}: {len(righe)}' + ('' if righe else '  NESSUN DATO'),
                          flush=True)
                giorno += timedelta(days=1)
        finally:
            try:
                d.quit()
            except Exception:
                pass

    df = pd.read_csv(out).fillna('')
    # Only the rows still labelled by currency need converting. Mapping the
    # whole column through a currency-keyed dictionary turned the ISO codes
    # written by an earlier run into empty strings, so every resumed run
    # stripped the country from all its predecessors and consolidation then
    # skipped them for matching no catalogue country.
    df['Paese'] = df.Paese.map(paese_iso)
    # A re-collected day appends a second copy of its rows; the later one is
    # the current truth (revised values), so it wins.
    #
    # `Orario` is deliberately NOT part of the key: a re-collection can carry a
    # corrected time, and keying on it would keep both rows instead of
    # replacing one -- leaving the ingest to pick between them by order, where
    # the stale row can win. `Periodo_Riferimento` IS part of it, because two
    # releases of the same indicator published the same day for different
    # periods are two observations, not a duplicate.
    chiave = [c for c in ('Data_Rilascio', 'Paese', 'Evento', 'Periodo_Riferimento')
              if c in df.columns]
    df = df.drop_duplicates(subset=chiave, keep='last').reset_index(drop=True)
    df.to_csv(out, index=False, encoding='utf-8-sig')
    return df
