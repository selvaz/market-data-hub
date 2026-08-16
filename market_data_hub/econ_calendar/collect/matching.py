# -*- coding: utf-8 -*-
"""Riconciliazione degli indicatori fra Yahoo Finance, Tradays e Forex Factory.

Le tre fonti non condividono nessuna chiave: Yahoo identifica il paese (ISO),
le altre due la valuta; ogni fonte pubblica gli orari in un fuso diverso; i
nomi degli indicatori divergono ("Sentix Index" vs "Sentix Investor
Confidence"). Il match si basa quindi su valuta + istante UTC + similarita'
del nome, con il valore pubblicato come conferma.
"""
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import pandas as pd

ANNO = 2026
EUROZONA = {'AT', 'BE', 'CY', 'DE', 'EE', 'ES', 'EU', 'FI', 'FR', 'GR', 'IE',
            'IT', 'LT', 'LU', 'LV', 'MT', 'NL', 'PT', 'SI', 'SK', 'HR'}
PAESE_VALUTA = {
    'AE': 'AED', 'AR': 'ARS', 'AU': 'AUD', 'BR': 'BRL', 'BW': 'BWP', 'CA': 'CAD',
    'CH': 'CHF', 'CN': 'CNY', 'CO': 'COP', 'CZ': 'CZK', 'DK': 'DKK', 'EG': 'EGP',
    'GB': 'GBP', 'HK': 'HKD', 'HU': 'HUF', 'ID': 'IDR', 'IL': 'ILS', 'IN': 'INR',
    'JP': 'JPY', 'KE': 'KES', 'KR': 'KRW', 'KW': 'KWD', 'MU': 'MUR', 'MX': 'MXN',
    'MY': 'MYR', 'MZ': 'MZN', 'NG': 'NGN', 'NO': 'NOK', 'NZ': 'NZD', 'OM': 'OMR',
    'PE': 'PEN', 'PL': 'PLN', 'QA': 'QAR', 'RO': 'RON', 'RU': 'RUB', 'SA': 'SAR',
    'SE': 'SEK', 'SG': 'SGD', 'TH': 'THB', 'TR': 'TRY', 'TW': 'TWD', 'TZ': 'TZS',
    'UG': 'UGX', 'US': 'USD', 'ZA': 'ZAR',
}

# rumore che non distingue un indicatore da un altro.
# NB: 'change' NON va qui - distingue "Employment Change" da "Employment",
# e "Stocks Change" dal livello delle scorte.
RUMORE = {'index', 'idx', 'the', 'of', 'and', 'total', 'nsa', 'sa', 'adj',
          'adjusted', 'seasonally', 'data', 'report', 'number'}


def valuta(paese):
    paese = (paese or '').strip().upper()
    if paese in EUROZONA:
        return 'EUR'
    return PAESE_VALUTA.get(paese, paese)


def normalizza(nome):
    """Riduce un nome a token confrontabili fra fonti diverse."""
    s = (nome or '').lower()
    s = s.replace('y/y', ' yy ').replace('m/m', ' mm ').replace('q/q', ' qq ')
    s = re.sub(r'\byoy\b', 'yy', s)
    s = re.sub(r'\bmom\b', 'mm', s)
    s = re.sub(r'\bqoq\b', 'qq', s)
    s = re.sub(r'\bann\w*\b', 'yy', s)
    s = s.replace('preliminary', 'prelim').replace('flash', 'prelim')
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    token = [t for t in s.split() if t not in RUMORE]
    return token


def somiglianza(tok_a, tok_b):
    if not tok_a or not tok_b:
        return 0.0
    a, b = set(tok_a), set(tok_b)
    jaccard = len(a & b) / len(a | b)
    seq = SequenceMatcher(None, ' '.join(tok_a), ' '.join(tok_b)).ratio()
    return max(jaccard, seq)


def numero(valore):
    """Estrae il valore numerico pubblicato ('-4.9%', '8.481 M', '2.448%')."""
    if not valore or valore in ('N/D', '-'):
        return None
    testo = str(valore).replace(',', '').strip()
    m = re.match(r'^-?\d+(?:\.\d+)?', testo)
    if not m:
        return None
    x = float(m.group(0))
    if re.search(r'\bB\b|bn', testo, re.I):
        x *= 1000
    return x


def valori_uguali(a, b, tolleranza=0.02):
    na, nb = numero(a), numero(b)
    if na is None or nb is None:
        return None                      # informazione assente, non discordanza
    if na == nb:
        return True
    return abs(na - nb) <= tolleranza * max(abs(na), abs(nb), 1e-9)


# ------------------------------------------------------------ parsing date --
def _istante(fonte, data, orario):
    """Restituisce (datetime naive nel fuso della fonte, e' un evento senza orario)."""
    data, orario = (data or '').strip(), (orario or '').strip()
    try:
        if fonte == 'Yahoo Finance':
            giorno = datetime.strptime(data, '%Y-%m-%d')
            ore = datetime.strptime(orario.replace(' UTC', '').strip(), '%I:%M %p')
        elif fonte == 'Tradays':
            giorno = datetime.strptime(f"{data.split(',')[0].strip()} {ANNO}", '%d %B %Y')
            ore = datetime.strptime(orario, '%H:%M')
        else:                                                # Forex Factory
            giorno = datetime.strptime(f"{' '.join(data.split()[1:])} {ANNO}", '%b %d %Y')
            ore = datetime.strptime(orario.lower().replace(' ', ''), '%I:%M%p')
    except ValueError:
        try:                                                 # evento senza orario
            if fonte == 'Yahoo Finance':
                giorno = datetime.strptime(data, '%Y-%m-%d')
            elif fonte == 'Tradays':
                giorno = datetime.strptime(f"{data.split(',')[0].strip()} {ANNO}", '%d %B %Y')
            else:
                giorno = datetime.strptime(f"{' '.join(data.split()[1:])} {ANNO}", '%b %d %Y')
            return giorno, True
        except ValueError:
            return None, True
    return giorno + timedelta(hours=ore.hour, minutes=ore.minute), False


def prepara(df):
    righe = []
    for idx, r in df.iterrows():
        istante, senza_ora = _istante(r.Fonte, r.Data_Rilascio, r.Orario)
        righe.append({
            'idx': idx, 'Fonte': r.Fonte, 'Evento': r.Evento,
            'Valuta': valuta(r.Paese) if r.Fonte == 'Yahoo Finance' else r.Paese.strip().upper(),
            'istante': istante, 'senza_ora': senza_ora,
            'token': normalizza(r.Evento), 'Attuale': r.Attuale,
        })
    return pd.DataFrame(righe)


def deduci_scarto_orario(a, b, soglia=0.9):
    """Stima il fuso relativo fra due fonti usando i nomi quasi identici."""
    scarti = []
    for _, ra in a.iterrows():
        if ra.istante is None or ra.senza_ora:
            continue
        for _, rb in b.iterrows():
            if rb.istante is None or rb.senza_ora or ra.Valuta != rb.Valuta:
                continue
            if somiglianza(ra.token, rb.token) >= soglia:
                delta = (ra.istante - rb.istante).total_seconds() / 3600
                if abs(delta) <= 14:
                    scarti.append(round(delta * 4) / 4)      # quarti d'ora
    if not scarti:
        return 0.0, 0
    return pd.Series(scarti).mode().iloc[0], len(scarti)


def accoppia(a, b, scarto_ore, soglia=0.55, finestra_min=90, usa_valori=True):
    """Match uno-a-uno greedy fra due fonti gia' allineate nel fuso.

    Con usa_valori=False il valore pubblicato NON entra nel punteggio: serve
    per poter poi misurare l'accordo sui valori come prova indipendente.
    """
    candidati = []
    for _, ra in a.iterrows():
        for _, rb in b.iterrows():
            if ra.Valuta != rb.Valuta or ra.istante is None or rb.istante is None:
                continue
            allineato = rb.istante + timedelta(hours=scarto_ore)
            if ra.senza_ora or rb.senza_ora:
                if ra.istante.date() != allineato.date():
                    continue
                penalita = 0.05
            else:
                if abs((ra.istante - allineato).total_seconds()) > finestra_min * 60:
                    continue
                penalita = 0.0

            sim = somiglianza(ra.token, rb.token)
            uguali = valori_uguali(ra.Attuale, rb.Attuale)
            bonus = 0.0
            if usa_valori:
                bonus = 0.20 if uguali is True else -0.15 if uguali is False else 0.0
            punteggio = sim - penalita + bonus
            if sim >= 0.35 and punteggio >= soglia:
                candidati.append((punteggio, sim, uguali, ra.idx, rb.idx))

    candidati.sort(reverse=True, key=lambda c: c[0])
    presi_a, presi_b, coppie = set(), set(), []
    for punteggio, sim, uguali, ia, ib in candidati:
        if ia in presi_a or ib in presi_b:
            continue
        presi_a.add(ia)
        presi_b.add(ib)
        coppie.append({'idx_a': ia, 'idx_b': ib, 'punteggio': round(punteggio, 3),
                       'somiglianza': round(sim, 3), 'valori_coerenti': uguali})
    return pd.DataFrame(coppie)
