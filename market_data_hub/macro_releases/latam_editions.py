"""Reviewed Latin American editions through 2026-12-31; dates never prove values."""
SCHEDULES = {
    "bcb_policy": "https://www.bcb.gov.br/detalhenoticia/20739/nota",
    "banxico_policy": "https://www.banxico.org.mx/politica-monetaria/d/{3B4BDA5B-E7E8-E414-2066-DA0CB3F7C36F}.pdf",
    "mx_cpi": "https://www.inegi.org.mx/app/saladeprensa/noticia/11265",
}
ZONES = {"bcb_policy": "America/Sao_Paulo", "banxico_policy": "America/Mexico_City",
         "mx_cpi": "America/Mexico_City"}
EDITIONS = {
    # BCB specifies publication after the meeting, from 18:30, not an exact clock.
    "bcb_policy": {d: (d, "final", None) for d in (
        "2026-08-05", "2026-09-16", "2026-11-04", "2026-12-09")},
    "banxico_policy": {d: (d, "final", "13:00") for d in (
        "2026-08-06", "2026-09-24", "2026-11-05", "2026-12-17")},
    # The linked release lists previous and next publications, including Dec 9.
    "mx_cpi": {
        "2026-09-09": ("2026-08", "final", "06:00"),
        "2026-10-08": ("2026-09", "final", "06:00"),
        "2026-11-09": ("2026-10", "final", "06:00"),
        "2026-12-09": ("2026-11", "final", "06:00"),
    },
}


def reviewed(family, edition):
    from .http import FetchError
    if edition not in EDITIONS.get(family, {}):
        raise FetchError("Latin American publication edition needs review")
    return EDITIONS[family][edition]
